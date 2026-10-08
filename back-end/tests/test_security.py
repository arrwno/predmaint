import json
import time
from dataclasses import FrozenInstanceError
from urllib.error import URLError

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from predmaint_backend import security

TENANT = "8b1f7a70-04bf-4720-a2da-24a7db28c161"
SUBJECT = "8ee12206-4c62-482f-82a2-2f5ddda02346"
AUDIENCE = "api://predmaint-test"


@pytest.fixture(scope="module")
def private_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def jwks(key, kid="synthetic-key"):
    public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    return {"keys": [{**public, "kid": kid, "use": "sig", "alg": "RS256"}]}


@pytest.fixture
def configured(monkeypatch, private_key):
    monkeypatch.setenv("PREDMAINT_ENTRA_TENANT_ID", TENANT)
    monkeypatch.setenv("PREDMAINT_ENTRA_AUDIENCE", AUDIENCE)
    security._signing_keys.cache_clear()
    monkeypatch.setattr(security, "_fetch_jwks", lambda url: jwks(private_key))
    yield
    security._signing_keys.cache_clear()


def token(key, *, updates=None, omitted=(), algorithm="RS256", kid="synthetic-key"):
    now = int(time.time())
    claims = {
        "iss": f"https://login.microsoftonline.com/{TENANT}/v2.0",
        "aud": AUDIENCE,
        "tid": TENANT,
        "oid": SUBJECT,
        "nbf": now - 10,
        "exp": now + 300,
        "roles": ["operator", "reviewer"],
    }
    claims.update(updates or {})
    for name in omitted:
        del claims[name]
    return jwt.encode(claims, key, algorithm=algorithm, headers={"kid": kid})


def request(value=None):
    headers = [] if value is None else [(b"authorization", value.encode())]
    return Request({"type": "http", "headers": headers})


def authenticate(value):
    return security.get_principal(request(f"Bearer {value}"))


def assert_error(status, callback):
    with pytest.raises(HTTPException) as error:
        callback()
    assert error.value.status_code == status
    assert set(error.value.detail) == {"error"}
    assert set(error.value.detail["error"]) == {"code", "message"}
    return error.value


def test_valid_signed_principal(configured, private_key):
    principal = authenticate(token(private_key))
    assert principal == security.Principal(TENANT, SUBJECT, frozenset({"operator", "reviewer"}))
    with pytest.raises(FrozenInstanceError):
        principal.subject_id = "replacement"


@pytest.mark.parametrize(
    "updates",
    [
        {"iss": "https://login.microsoftonline.com/common/v2.0"},
        {"aud": "api://another-resource"},
        {"aud": [AUDIENCE, "api://another-resource"]},
        {"tid": "931b5e46-8927-4320-ac62-70c5ea379851"},
        {"tid": "not-a-uuid"},
        {"oid": "not-a-uuid"},
        {"oid": None},
        {"exp": 1},
        {"nbf": 9999999999},
        {"roles": "admin"},
        {"roles": ["operator", "administrator"]},
        {"roles": [""]},
        {"roles": [123]},
        {"roles": [None]},
    ],
)
def test_invalid_claims(configured, private_key, updates):
    encoded = token(private_key, updates=updates)
    error = assert_error(401, lambda: authenticate(encoded))
    assert encoded not in str(error.detail)
    assert error.headers == {"WWW-Authenticate": "Bearer"}


@pytest.mark.parametrize("claim", ["iss", "aud", "exp", "nbf", "tid", "oid"])
def test_required_claims(configured, private_key, claim):
    assert_error(401, lambda: authenticate(token(private_key, omitted=(claim,))))


def test_no_roles_grants_no_access(configured, private_key):
    assert authenticate(token(private_key, omitted=("roles",))).roles == frozenset()


@pytest.mark.parametrize(
    "header",
    [None, "", "Basic synthetic", "Bearer", "Bearer one two", "Bearer not-a-jwt"],
)
def test_missing_or_malformed_bearer(configured, header):
    assert_error(401, lambda: security.get_principal(request(header)))


def test_duplicate_authorization(configured, private_key):
    value = f"Bearer {token(private_key)}".encode()
    req = Request({"type": "http", "headers": [(b"authorization", value)] * 2})
    assert_error(401, lambda: security.get_principal(req))


@pytest.mark.parametrize(
    "name,value",
    [
        ("PREDMAINT_ENTRA_TENANT_ID", None),
        ("PREDMAINT_ENTRA_TENANT_ID", "https://untrusted.test"),
        ("PREDMAINT_ENTRA_AUDIENCE", None),
        ("PREDMAINT_ENTRA_AUDIENCE", ""),
        ("PREDMAINT_ENTRA_AUDIENCE", " api://resource"),
    ],
)
def test_missing_or_invalid_configuration(configured, monkeypatch, name, value):
    if value is None:
        monkeypatch.delenv(name)
    else:
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(security, "_fetch_jwks", lambda url: pytest.fail("Must not fetch"))
    assert_error(503, lambda: security.get_principal(request()))


@pytest.mark.parametrize("algorithm", ["HS256", "none", "RS512"])
def test_forbidden_algorithms(configured, private_key, algorithm):
    key = (
        private_key
        if algorithm == "RS512"
        else (None if algorithm == "none" else "synthetic-hmac-key-at-least-32-bytes")
    )
    assert_error(401, lambda: authenticate(token(key, algorithm=algorithm)))


def test_invalid_signature_and_unknown_key(configured, private_key):
    different = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    assert_error(401, lambda: authenticate(token(different)))
    assert_error(401, lambda: authenticate(token(private_key, kid="unknown")))


@pytest.mark.parametrize(
    "result",
    [URLError("synthetic-sensitive-detail"), {}, {"keys": []}, {"keys": "invalid"}],
)
def test_unavailable_keys(configured, monkeypatch, private_key, result):
    def fetch(url):
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(security, "_fetch_jwks", fetch)
    error = assert_error(503, lambda: authenticate(token(private_key)))
    assert "synthetic-sensitive-detail" not in str(error.detail)


def test_cache_rotation_and_refresh_bound(configured, monkeypatch, private_key):
    now = [1000]
    calls = []
    rotated = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def fetch(url):
        calls.append(url)
        return jwks(private_key) if len(calls) == 1 else jwks(rotated, "rotated")

    monkeypatch.setattr(security.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(security, "_fetch_jwks", fetch)
    authenticate(token(private_key))
    authenticate(token(private_key))
    assert_error(401, lambda: authenticate(token(rotated, kid="rotated")))
    assert len(calls) == 1
    now[0] += 31
    authenticate(token(rotated, kid="rotated"))
    assert calls == [f"https://login.microsoftonline.com/{TENANT}/discovery/v2.0/keys"] * 2


def test_jwks_fetch_timeout_size_and_redirect_policy(monkeypatch):
    calls = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, length):
            calls.append(length)
            return b"x" * length

    class Opener:
        def open(self, req, *, timeout):
            calls.append((req.full_url, timeout))
            return Response()

    def opener(handler):
        assert isinstance(handler, security._NoRedirect)
        assert handler.redirect_request(None, None, 302, "", {}, "https://evil.test") is None
        return Opener()

    monkeypatch.setattr(security, "build_opener", opener)
    url = f"https://login.microsoftonline.com/{TENANT}/discovery/v2.0/keys"
    with pytest.raises(ValueError, match="too large"):
        security._fetch_jwks(url)
    assert calls == [(url, 5), 256 * 1024 + 1]


def test_expired_cache_never_falls_back(configured, monkeypatch, private_key):
    now = [1000]
    monkeypatch.setattr(security.time, "monotonic", lambda: now[0])
    authenticate(token(private_key))
    now[0] += 301

    def fail(url):
        raise URLError("offline")

    monkeypatch.setattr(security, "_fetch_jwks", fail)
    assert_error(503, lambda: authenticate(token(private_key)))
    assert_error(503, lambda: authenticate(token(private_key)))


def test_fastapi_dependency_and_error_envelope(configured, private_key):
    app = FastAPI()

    @app.exception_handler(HTTPException)
    async def handle_error(request, exc):
        return JSONResponse(status_code=exc.status_code, content=exc.detail, headers=exc.headers)

    @app.get("/")
    def protected(principal=Depends(security.get_principal)):
        return {"subject": principal.subject_id}

    with TestClient(app) as client:
        assert client.get("/").json()["error"]["code"] == "authentication_required"
        response = client.get("/", headers={"Authorization": f"Bearer {token(private_key)}"})
        assert response.status_code == 200
        assert response.json() == {"subject": SUBJECT}
