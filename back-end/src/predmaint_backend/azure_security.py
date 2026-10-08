"""Explicit identity-only vault access; infrastructure policy remains an IaC gate."""

import logging
import os
import re
from dataclasses import dataclass
from urllib.parse import urlsplit
from uuid import UUID

from azure.core.exceptions import AzureError
from azure.identity import AzureCliCredential, ManagedIdentityCredential
from azure.keyvault.secrets import SecretClient

_VAULT_HOST = re.compile(r"[a-z][a-z0-9-]{1,22}[a-z0-9]\.vault\.azure\.net")
_SECRET_NAME = re.compile(r"[A-Za-z0-9-]{1,127}")
_logger = logging.getLogger(__name__)


class VaultConfigurationError(ValueError):
    """The explicit identity/environment/vault configuration is invalid."""


class VaultAccessError(RuntimeError):
    """Secret-dependent work failed; never contains a secret or SDK exception text."""


def validate_vault_url(value: str) -> str:
    try:
        url = urlsplit(value)
        if (
            url.scheme != "https"
            or not url.hostname
            or not _VAULT_HOST.fullmatch(url.hostname)
            or "--" in url.hostname
            or url.netloc != url.hostname
            or url.username is not None
            or url.password is not None
            or url.port is not None
            or url.path
            or url.query
            or url.fragment
            or value != f"https://{url.hostname}"
        ):
            raise ValueError()
    except TypeError, ValueError:
        raise VaultConfigurationError(
            "A canonical Azure Key Vault HTTPS URL is required."
        ) from None
    return value


@dataclass(frozen=True)
class VaultConfiguration:
    mode: str
    environment: str
    vault_url: str
    managed_identity_client_id: str | None = None

    def __post_init__(self):
        if self.mode not in {"azure", "development"}:
            raise VaultConfigurationError("An explicit Azure identity mode is required.")
        if self.environment not in {"development", "test", "production"}:
            raise VaultConfigurationError("An explicit vault environment is required.")
        if self.mode == "development" and self.environment != "development":
            raise VaultConfigurationError("Developer identity requires a development vault.")
        validate_vault_url(self.vault_url)
        if self.managed_identity_client_id is not None:
            try:
                UUID(self.managed_identity_client_id)
            except ValueError, TypeError, AttributeError:
                raise VaultConfigurationError(
                    "Managed Identity client ID must be a UUID."
                ) from None
            if self.mode != "azure":
                raise VaultConfigurationError("Managed Identity client ID requires Azure mode.")

    @classmethod
    def from_environment(cls) -> VaultConfiguration:
        mode = os.environ.get("PREDMAINT_AZURE_MODE", "")
        environment = os.environ.get("PREDMAINT_ENVIRONMENT", "")
        if environment not in {"development", "test", "production"}:
            raise VaultConfigurationError("An explicit vault environment is required.")
        vault_url = os.environ.get(f"PREDMAINT_KEY_VAULT_URL_{environment.upper()}", "")
        return cls(
            mode,
            environment,
            vault_url,
            os.environ.get("PREDMAINT_MANAGED_IDENTITY_CLIENT_ID"),
        )


class KeyVaultSecretProvider:
    """Own and close the client/credential, including explicitly injected test doubles."""

    def __init__(
        self,
        configuration: VaultConfiguration,
        *,
        credential=None,
        client=None,
    ):
        self._closed = False
        if credential is None:
            if configuration.mode == "azure":
                credential = ManagedIdentityCredential(
                    client_id=configuration.managed_identity_client_id,
                    connection_timeout=5,
                    read_timeout=5,
                    retry_total=0,
                )
            else:
                credential = AzureCliCredential(process_timeout=5)
        self._credential = credential
        initialized = False
        try:
            self._client = (
                client
                if client is not None
                else SecretClient(
                    vault_url=configuration.vault_url,
                    credential=credential,
                    connection_timeout=5,
                    read_timeout=5,
                    retry_total=0,
                    logging_enable=False,
                )
            )
            initialized = True
        except AzureError, ValueError, OSError:
            raise VaultAccessError("Vault client initialization failed.") from None
        finally:
            if not initialized:
                try:
                    credential.close()
                except AzureError, OSError:
                    _logger.warning("Vault credential cleanup failed after initialization failure.")

    def get_secret(self, name: str) -> str:
        if self._closed:
            raise VaultAccessError("Vault provider is closed.")
        if not isinstance(name, str) or not _SECRET_NAME.fullmatch(name):
            raise VaultConfigurationError("Invalid Key Vault secret identifier.")
        try:
            value = self._client.get_secret(name, logging_enable=False).value
        except AzureError, OSError:
            raise VaultAccessError("Vault secret access failed.") from None
        if not isinstance(value, str) or not value.strip():
            raise VaultAccessError("Vault secret has no usable value.")
        return value

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            cleanup_failed = False
            try:
                try:
                    self._client.close()
                except AzureError, OSError:
                    cleanup_failed = True
            finally:
                try:
                    self._credential.close()
                except AzureError, OSError:
                    cleanup_failed = True
            if cleanup_failed:
                raise VaultAccessError("Vault provider cleanup failed.") from None

    def __enter__(self) -> KeyVaultSecretProvider:
        if self._closed:
            raise VaultAccessError("Vault provider is closed.")
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()
