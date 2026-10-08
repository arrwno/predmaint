"""Tenant-bound Entra authentication; the synchronous dependency runs in a worker."""

import json
import os
import threading
import time
from dataclasses import dataclass
from functools import lru_cache
from http.client import HTTPException as HTTPClientError
from urllib.error import URLError
from urllib.request import HTTPRedirectHandler, build_opener
from urllib.request import Request as URLRequest
from uuid import UUID

import jwt
from fastapi import HTTPException, Request

_ROLES = frozenset({"operator", "reviewer", "admin"})
_CACHE_SECONDS = 300
_REFRESH_SECONDS = 30
_TIMEOUT_SECONDS = 5
_MAX_JWKS_BYTES = 256 * 1024


@dataclass(frozen=True)
class Principal:
    tenant_id: str
    subject_id: str
    roles: frozenset[str]


def _error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status,
        detail={"error": {"code": code, "message": message}},
        headers={"WWW-Authenticate": "Bearer"} if status == 401 else None,
    )


def _uuid(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("Expected a UUID")
    return str(UUID(value))


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _fetch_jwks(url: str) -> dict:
    # Never follow a redirect away from Microsoft's fixed tenant endpoint.
    with build_opener(_NoRedirect()).open(
        URLRequest(url, headers={"Accept": "application/json"}),
        timeout=_TIMEOUT_SECONDS,
    ) as response:
        data = response.read(_MAX_JWKS_BYTES + 1)
    if len(data) > _MAX_JWKS_BYTES:
        raise ValueError("Signing key response too large")
    return json.loads(data)


class _SigningKeys:
    def __init__(self, tenant: str):
        self.url = f"https://login.microsoftonline.com/{tenant}/discovery/v2.0/keys"
        self._keys: dict[str, jwt.PyJWK] = {}
        self._loaded_at: float | None = None
        self._attempted_at: float | None = None
        self._unavailable = False
        self._lock = threading.Lock()

    def get(self, kid: str) -> jwt.PyJWK:
        with self._lock:
            now = time.monotonic()
            fresh = self._loaded_at is not None and now - self._loaded_at < _CACHE_SECONDS
            if fresh and kid in self._keys:
                return self._keys[kid]
            may_refresh = self._attempted_at is None or now - self._attempted_at >= _REFRESH_SECONDS
            if may_refresh:
                self._attempted_at = now
                try:
                    document = _fetch_jwks(self.url)
                    entries = document["keys"]
                    if not isinstance(entries, list) or len(entries) > 100:
                        raise ValueError("Invalid signing key response")
                    keys = {}
                    for entry in entries:
                        if not isinstance(entry, dict):
                            raise ValueError("Invalid signing key")
                        if (
                            entry.get("kty") != "RSA"
                            or entry.get("use", "sig") != "sig"
                            or entry.get("alg", "RS256") != "RS256"
                        ):
                            continue
                        key_id = entry.get("kid")
                        if not isinstance(key_id, str) or not key_id or key_id in keys:
                            raise ValueError("Invalid signing key identifier")
                        keys[key_id] = jwt.PyJWK.from_dict(entry, algorithm="RS256")
                    if not keys:
                        raise ValueError("No usable signing keys")
                    self._keys = keys
                    self._loaded_at = now
                    self._unavailable = False
                except (
                    URLError,
                    OSError,
                    HTTPClientError,
                    ValueError,
                    KeyError,
                    TypeError,
                    RecursionError,
                    jwt.PyJWTError,
                ):
                    self._unavailable = True
                    raise _error(
                        503, "signing_keys_unavailable", "Signing keys are unavailable."
                    ) from None
            if (
                self._unavailable
                or self._loaded_at is None
                or not (now - self._loaded_at < _CACHE_SECONDS)
            ):
                raise _error(503, "signing_keys_unavailable", "Signing keys are unavailable.")
            if kid not in self._keys:
                raise _error(401, "invalid_token", "Invalid access token.")
            return self._keys[kid]


@lru_cache(maxsize=8)
def _signing_keys(tenant: str) -> _SigningKeys:
    return _SigningKeys(tenant)


def get_principal(request: Request) -> Principal:
    """Validate an Entra v2 access token without any local authentication bypass."""
    try:
        tenant = _uuid(os.environ["PREDMAINT_ENTRA_TENANT_ID"])
        audience = os.environ["PREDMAINT_ENTRA_AUDIENCE"]
        if (
            not audience
            or audience != audience.strip()
            or any(character.isspace() for character in audience)
        ):
            raise ValueError("Invalid audience configuration")
    except KeyError, ValueError:
        raise _error(
            503, "authentication_unconfigured", "Authentication is not configured."
        ) from None

    authorizations = request.headers.getlist("authorization")
    if len(authorizations) != 1:
        raise _error(401, "authentication_required", "A bearer access token is required.")
    parts = authorizations[0].split()
    if len(parts) != 2 or parts[0].lower() != "bearer" or len(parts[1]) > 16 * 1024:
        raise _error(401, "authentication_required", "A bearer access token is required.")
    token = parts[1]
    try:
        header = jwt.get_unverified_header(token)
        kid = header.get("kid")
        if header.get("alg") != "RS256" or not isinstance(kid, str) or not kid:
            raise jwt.InvalidTokenError()
        key = _signing_keys(tenant).get(kid)
        claims = jwt.decode(
            token,
            key.key,
            algorithms=["RS256"],
            issuer=f"https://login.microsoftonline.com/{tenant}/v2.0",
            audience=audience,
            options={
                "require": ["exp", "iss", "aud", "nbf", "tid", "oid"],
                "strict_aud": True,
            },
        )
        if _uuid(claims["tid"]) != tenant:
            raise jwt.InvalidTokenError()
        subject = _uuid(claims["oid"])
        roles = claims.get("roles", [])
        if not isinstance(roles, list) or any(
            not isinstance(role, str) or role not in _ROLES for role in roles
        ):
            raise jwt.InvalidTokenError()
        return Principal(tenant, subject, frozenset(roles))
    except jwt.PyJWTError, ValueError, TypeError, KeyError:
        raise _error(401, "invalid_token", "Invalid access token.") from None
