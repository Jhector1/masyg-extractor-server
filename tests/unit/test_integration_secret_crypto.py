from __future__ import annotations

from cryptography.fernet import Fernet
import pytest

from masyg_extractor.integrations.accounting.shared.integration_secret_crypto import (
    IntegrationSecretConfigurationError,
    IntegrationSecretDecryptionError,
    decrypt_secret_map,
    encrypt_secret_map,
    seal_token_data,
    unseal_token_data,
)


@pytest.fixture
def integration_key(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("INTEGRATION_ENCRYPTION_KEY_VERSION", "v1")
    monkeypatch.setenv("INTEGRATION_ENCRYPTION_KEY_V1", key)
    monkeypatch.delenv("INTEGRATION_ENCRYPTION_KEY", raising=False)
    return key


def test_sealed_token_data_contains_no_plaintext_provider_tokens(
    integration_key,
):
    stored = seal_token_data(
        user_id="user-1",
        integration="quickbooks",
        token_data={
            "accessToken": "access-secret-123",
            "refreshToken": "refresh-secret-456",
            "tokenType": "Bearer",
            "expiresAt": "2026-09-17T00:00:00Z",
            "realmId": "company-7",
        },
    )

    serialized = repr(stored)
    assert "access-secret-123" not in serialized
    assert "refresh-secret-456" not in serialized
    assert stored["tokenType"] == "Bearer"
    assert stored["realmId"] == "company-7"
    assert stored["encryptedSecrets"]["alg"] == "AES-256-GCM"


def test_round_trip_preserves_runtime_contract(
    integration_key,
):
    original = {
        "accessToken": "access-value",
        "refreshToken": "refresh-value",
        "id_token": "xero-id-token",
        "expiresAt": "2026-09-17T00:00:00Z",
        "tenant_id": "tenant-1",
    }
    stored = seal_token_data(
        user_id="user-1",
        integration="xero",
        token_data=original,
    )

    runtime, migrated = unseal_token_data(
        user_id="user-1",
        integration="xero",
        token_data=stored,
    )

    assert migrated is None
    assert runtime == original


def test_ciphertext_is_bound_to_user_and_provider(
    integration_key,
):
    envelope = encrypt_secret_map(
        user_id="user-1",
        integration="quickbooks",
        secrets={"refreshToken": "refresh-value"},
    )

    with pytest.raises(IntegrationSecretDecryptionError):
        decrypt_secret_map(
            user_id="user-2",
            integration="quickbooks",
            envelope=envelope,
        )

    with pytest.raises(IntegrationSecretDecryptionError):
        decrypt_secret_map(
            user_id="user-1",
            integration="xero",
            envelope=envelope,
        )


def test_legacy_plaintext_record_is_returned_and_migrated(
    integration_key,
):
    legacy = {
        "accessToken": "legacy-access",
        "refreshToken": "legacy-refresh",
        "expiresAt": "2026-09-17T00:00:00Z",
        "realmId": "realm-1",
    }

    runtime, migrated = unseal_token_data(
        user_id="user-1",
        integration="quickbooks",
        token_data=legacy,
    )

    assert runtime == legacy
    assert migrated is not None
    assert "accessToken" not in migrated
    assert "refreshToken" not in migrated
    assert "legacy-access" not in repr(migrated)
    assert "legacy-refresh" not in repr(migrated)


def test_missing_encryption_key_fails_closed(monkeypatch):
    monkeypatch.setenv("INTEGRATION_ENCRYPTION_KEY_VERSION", "v1")
    monkeypatch.delenv("INTEGRATION_ENCRYPTION_KEY_V1", raising=False)
    monkeypatch.delenv("INTEGRATION_ENCRYPTION_KEY", raising=False)
    monkeypatch.delenv("ENCRYPTION_KEY", raising=False)

    with pytest.raises(IntegrationSecretConfigurationError):
        encrypt_secret_map(
            user_id="user-1",
            integration="quickbooks",
            secrets={"refreshToken": "refresh-value"},
        )
