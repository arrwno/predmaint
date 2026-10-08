import traceback
from types import SimpleNamespace

import pytest
from azure.core.exceptions import (
    ClientAuthenticationError,
    HttpResponseError,
    ServiceRequestError,
)

from predmaint_backend import azure_security as vault

VAULT_URL = "https://predmaint-dev.vault.azure.net"
CLIENT_ID = "642db12b-9b35-4447-9edb-2b698d65fa88"


class FakeCredential:
    def __init__(self, close_error=None):
        self.closed = False
        self.close_error = close_error

    def close(self):
        self.closed = True
        if self.close_error is not None:
            raise self.close_error


class FakeClient:
    def __init__(self, value="synthetic-test-value", error=None, close_error=None):
        self.value = value
        self.error = error
        self.close_error = close_error
        self.closed = False
        self.calls = []

    def get_secret(self, name, **kwargs):
        self.calls.append((name, kwargs))
        if self.error is not None:
            raise self.error
        return SimpleNamespace(value=self.value)

    def close(self):
        self.closed = True
        if self.close_error is not None:
            raise self.close_error


@pytest.mark.parametrize(
    "url",
    [
        "http://predmaint-dev.vault.azure.net",
        "https://127.0.0.1",
        "https://predmaint-dev.vault.azure.net.evil.test",
        "https://evil.test/predmaint-dev.vault.azure.net",
        "https://user@predmaint-dev.vault.azure.net",
        "https://user:password@predmaint-dev.vault.azure.net",
        "https://predmaint-dev.vault.azure.net:443",
        "https://predmaint-dev.vault.azure.net/",
        "https://predmaint-dev.vault.azure.net/secrets/name",
        "https://predmaint-dev.vault.azure.net?query",
        "https://predmaint-dev.vault.azure.net#fragment",
        "https://predmaint-dev.vault.azure.net.",
        "https://predmaint--dev.vault.azure.net",
        "https://a.vault.azure.net",
        " https://predmaint-dev.vault.azure.net",
    ],
)
def test_rejects_untrusted_vault_urls(url):
    with pytest.raises(vault.VaultConfigurationError):
        vault.validate_vault_url(url)


def test_canonical_vault_url():
    assert vault.validate_vault_url(VAULT_URL) == VAULT_URL


@pytest.mark.parametrize(
    "mode,environment,client_id",
    [
        ("", "development", None),
        ("default", "production", None),
        ("development", "production", None),
        ("development", "test", None),
        ("azure", "", None),
        ("azure", "production", "not-a-uuid"),
        ("development", "development", CLIENT_ID),
    ],
)
def test_explicit_identity_configuration(mode, environment, client_id):
    with pytest.raises(vault.VaultConfigurationError):
        vault.VaultConfiguration(mode, environment, VAULT_URL, client_id)


def test_environment_specific_vault(monkeypatch):
    monkeypatch.setenv("PREDMAINT_AZURE_MODE", "azure")
    monkeypatch.setenv("PREDMAINT_ENVIRONMENT", "production")
    monkeypatch.delenv("PREDMAINT_MANAGED_IDENTITY_CLIENT_ID", raising=False)
    monkeypatch.setenv("PREDMAINT_KEY_VAULT_URL_DEVELOPMENT", VAULT_URL)
    monkeypatch.delenv("PREDMAINT_KEY_VAULT_URL_PRODUCTION", raising=False)
    monkeypatch.setenv("PREDMAINT_KEY_VAULT_URL", VAULT_URL)
    with pytest.raises(vault.VaultConfigurationError):
        vault.VaultConfiguration.from_environment()
    production_url = "https://predmaint-prod.vault.azure.net"
    monkeypatch.setenv("PREDMAINT_KEY_VAULT_URL_PRODUCTION", production_url)
    assert vault.VaultConfiguration.from_environment().vault_url == production_url


def test_no_default_identity_mode(monkeypatch):
    monkeypatch.delenv("PREDMAINT_AZURE_MODE", raising=False)
    monkeypatch.setenv("PREDMAINT_ENVIRONMENT", "development")
    monkeypatch.setenv("PREDMAINT_KEY_VAULT_URL_DEVELOPMENT", VAULT_URL)
    with pytest.raises(vault.VaultConfigurationError):
        vault.VaultConfiguration.from_environment()


@pytest.mark.parametrize(
    "mode,environment", [("azure", "production"), ("development", "development")]
)
def test_explicit_credential_and_bounded_client(monkeypatch, mode, environment):
    calls = []
    credential = FakeCredential()
    client = FakeClient()

    def managed(**kwargs):
        calls.append(("managed", kwargs))
        return credential

    def cli(**kwargs):
        calls.append(("cli", kwargs))
        return credential

    def secret_client(**kwargs):
        calls.append(("client", kwargs))
        return client

    monkeypatch.setattr(vault, "ManagedIdentityCredential", managed)
    monkeypatch.setattr(vault, "AzureCliCredential", cli)
    monkeypatch.setattr(vault, "SecretClient", secret_client)
    config = vault.VaultConfiguration(
        mode, environment, VAULT_URL, CLIENT_ID if mode == "azure" else None
    )
    with vault.KeyVaultSecretProvider(config) as provider:
        assert provider.get_secret("synthetic-secret") == "synthetic-test-value"
    expected_identity = "managed" if mode == "azure" else "cli"
    assert calls[0][0] == expected_identity
    if mode == "azure":
        assert calls[0][1]["client_id"] == CLIENT_ID
        assert calls[0][1]["retry_total"] == 0
    else:
        assert calls[0][1] == {"process_timeout": 5}
    assert calls[1][1] == {
        "vault_url": VAULT_URL,
        "credential": credential,
        "connection_timeout": 5,
        "read_timeout": 5,
        "retry_total": 0,
        "logging_enable": False,
    }
    assert client.closed and credential.closed


@pytest.mark.parametrize(
    "name", ["", "../secret", "name/version", "https://evil.test", "x" * 128, None]
)
def test_secret_identifier_validation(name):
    credential, client = FakeCredential(), FakeClient()
    with vault.KeyVaultSecretProvider(
        vault.VaultConfiguration("azure", "test", VAULT_URL),
        credential=credential,
        client=client,
    ) as provider:
        with pytest.raises(vault.VaultConfigurationError):
            provider.get_secret(name)
    assert client.calls == []


@pytest.mark.parametrize("value", [None, "", " ", 1, SimpleNamespace(value="nested")])
def test_no_fake_success_on_empty_or_nonstring_value(value):
    with vault.KeyVaultSecretProvider(
        vault.VaultConfiguration("azure", "test", VAULT_URL),
        credential=FakeCredential(),
        client=FakeClient(value),
    ) as provider:
        with pytest.raises(vault.VaultAccessError, match="no usable value"):
            provider.get_secret("synthetic-secret")


@pytest.mark.parametrize(
    "error_type",
    [ClientAuthenticationError, HttpResponseError, ServiceRequestError, OSError],
)
def test_access_and_network_errors_do_not_leak(error_type, caplog):
    sensitive = "synthetic-sensitive-exception-detail"
    client = FakeClient(error=error_type(sensitive))
    credential = FakeCredential()
    with vault.KeyVaultSecretProvider(
        vault.VaultConfiguration("azure", "test", VAULT_URL),
        credential=credential,
        client=client,
    ) as provider:
        with pytest.raises(vault.VaultAccessError) as error:
            provider.get_secret("synthetic-secret")
    assert sensitive not in str(error.value)
    assert sensitive not in caplog.text
    assert credential.closed and client.closed


def test_context_manager_closes_on_failure_and_is_idempotent():
    client, credential = FakeClient(), FakeCredential()
    provider = vault.KeyVaultSecretProvider(
        vault.VaultConfiguration("azure", "test", VAULT_URL),
        credential=credential,
        client=client,
    )
    with pytest.raises(RuntimeError, match="synthetic failure"):
        with provider:
            raise RuntimeError("synthetic failure")
    provider.close()
    assert client.closed and credential.closed
    with pytest.raises(vault.VaultAccessError, match="closed"):
        provider.get_secret("synthetic-secret")


@pytest.mark.parametrize(
    "error_type", [ServiceRequestError, HttpResponseError, OSError, ValueError]
)
def test_failed_client_initialization_closes_credential(monkeypatch, error_type):
    credential = FakeCredential()

    def fail(**kwargs):
        raise error_type("sensitive SDK detail")

    monkeypatch.setattr(vault, "SecretClient", fail)
    with pytest.raises(vault.VaultAccessError, match="initialization failed"):
        vault.KeyVaultSecretProvider(
            vault.VaultConfiguration("azure", "test", VAULT_URL),
            credential=credential,
        )
    assert credential.closed


def test_unexpected_initialization_error_propagates_and_closes_credential(monkeypatch):
    credential = FakeCredential()

    def fail(**kwargs):
        raise RuntimeError("synthetic programming error")

    monkeypatch.setattr(vault, "SecretClient", fail)
    with pytest.raises(RuntimeError, match="synthetic programming error"):
        vault.KeyVaultSecretProvider(
            vault.VaultConfiguration("azure", "test", VAULT_URL),
            credential=credential,
        )
    assert credential.closed


@pytest.mark.parametrize("cleanup_error_type", [ServiceRequestError, OSError])
def test_initialization_failure_does_not_leak_credential_cleanup_error(
    monkeypatch, cleanup_error_type, caplog
):
    sensitive = "synthetic-sensitive-cleanup-detail"
    credential = FakeCredential(close_error=cleanup_error_type(sensitive))

    def fail(**kwargs):
        raise ServiceRequestError("synthetic-sensitive-initialization-detail")

    monkeypatch.setattr(vault, "SecretClient", fail)
    with pytest.raises(vault.VaultAccessError, match="initialization failed") as error:
        vault.KeyVaultSecretProvider(
            vault.VaultConfiguration("azure", "test", VAULT_URL),
            credential=credential,
        )
    rendered = "".join(traceback.format_exception(error.value))
    assert sensitive not in rendered
    assert "synthetic-sensitive-initialization-detail" not in rendered
    assert sensitive not in caplog.text
    assert "Vault credential cleanup failed after initialization failure." in caplog.text
    assert credential.closed


@pytest.mark.parametrize(
    "client_error,credential_error",
    [
        (ServiceRequestError, None),
        (OSError, None),
        (None, ServiceRequestError),
        (None, OSError),
        (ServiceRequestError, OSError),
    ],
)
def test_cleanup_failures_are_sanitized_and_both_resources_attempted(
    client_error, credential_error, caplog
):
    sensitive = "synthetic-sensitive-cleanup-detail"
    client = FakeClient(close_error=client_error(sensitive) if client_error else None)
    credential = FakeCredential(
        close_error=credential_error(sensitive) if credential_error else None
    )
    provider = vault.KeyVaultSecretProvider(
        vault.VaultConfiguration("azure", "test", VAULT_URL),
        credential=credential,
        client=client,
    )
    with pytest.raises(vault.VaultAccessError, match="cleanup failed") as error:
        provider.close()
    assert client.closed and credential.closed
    assert sensitive not in "".join(traceback.format_exception(error.value))
    assert sensitive not in caplog.text
    provider.close()


def test_unexpected_client_cleanup_error_still_attempts_credential_cleanup():
    client = FakeClient(close_error=RuntimeError("synthetic programming error"))
    credential = FakeCredential(close_error=OSError("synthetic-sensitive-cleanup-detail"))
    provider = vault.KeyVaultSecretProvider(
        vault.VaultConfiguration("azure", "test", VAULT_URL),
        credential=credential,
        client=client,
    )
    with pytest.raises(RuntimeError, match="synthetic programming error"):
        provider.close()
    assert client.closed and credential.closed
