from __future__ import annotations

import base64
import json
import os
import re
from typing import Any

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


_ENVELOPE_VERSION = 1
_ALGORITHM = "AES-256-GCM"
_NONCE_BYTES = 12
_HKDF_SALT = b"masyg-accounting-integration-secrets-v1"
_CURRENT_VERSION_ENV = "INTEGRATION_ENCRYPTION_KEY_VERSION"
_GENERIC_KEY_ENV = "INTEGRATION_ENCRYPTION_KEY"
_LEGACY_ROOT_KEY_ENV = "ENCRYPTION_KEY"

SECRET_FIELD_NAMES = frozenset(
    {
        "accessToken",
        "refreshToken",
        "access_token",
        "refresh_token",
        "id_token",
        "idToken",
    }
)


class IntegrationSecretConfigurationError(RuntimeError):
    pass


class IntegrationSecretDecryptionError(RuntimeError):
    pass


def _normalized_provider(integration: str) -> str:
    provider = str(integration or "").strip().lower()
    if not provider:
        raise ValueError("integration is required")
    return provider


def _normalized_user_id(user_id: str) -> str:
    value = str(user_id or "").strip()
    if not value:
        raise ValueError("user_id is required")
    return value


def current_key_version() -> str:
    version = str(
        os.getenv(_CURRENT_VERSION_ENV) or "v1"
    ).strip()
    if not version or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", version):
        raise IntegrationSecretConfigurationError(
            f"{_CURRENT_VERSION_ENV} is invalid."
        )
    return version


def _version_key_env(version: str) -> str:
    suffix = re.sub(r"[^A-Za-z0-9]", "_", version).upper()
    return f"INTEGRATION_ENCRYPTION_KEY_{suffix}"


def _decode_root_key(value: str, *, source: str) -> bytes:
    encoded = str(value or "").strip()
    if not encoded:
        raise IntegrationSecretConfigurationError(
            f"{source} is empty."
        )

    try:
        padded = encoded + ("=" * (-len(encoded) % 4))
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
    except Exception as exc:
        raise IntegrationSecretConfigurationError(
            f"{source} must be a URL-safe base64 encoded 32-byte key."
        ) from exc

    if len(raw) != 32:
        raise IntegrationSecretConfigurationError(
            f"{source} must decode to exactly 32 bytes."
        )
    return raw


def _root_key_for_version(version: str) -> bytes:
    version_env = _version_key_env(version)
    version_value = os.getenv(version_env)
    if version_value:
        return _decode_root_key(
            version_value,
            source=version_env,
        )

    if version == current_key_version():
        generic = os.getenv(_GENERIC_KEY_ENV)
        if generic:
            return _decode_root_key(
                generic,
                source=_GENERIC_KEY_ENV,
            )

        legacy = os.getenv(_LEGACY_ROOT_KEY_ENV)
        if legacy:
            return _decode_root_key(
                legacy,
                source=_LEGACY_ROOT_KEY_ENV,
            )

    raise IntegrationSecretConfigurationError(
        "Missing integration encryption key for "
        f"key version {version!r}. Configure {version_env}; "
        f"for the current version, {_GENERIC_KEY_ENV} or "
        f"{_LEGACY_ROOT_KEY_ENV} may be used as a compatibility fallback."
    )


def _aead_key(version: str) -> bytes:
    root_key = _root_key_for_version(version)
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=_HKDF_SALT,
        info=(
            b"masyg/accounting/integration-secrets/"
            + version.encode("utf-8")
        ),
    ).derive(root_key)


def _aad(
    *,
    user_id: str,
    integration: str,
    key_version: str,
) -> bytes:
    payload = {
        "v": _ENVELOPE_VERSION,
        "userId": _normalized_user_id(user_id),
        "integration": _normalized_provider(integration),
        "keyVersion": key_version,
    }
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii")


def _b64decode(value: Any, *, field: str) -> bytes:
    if not isinstance(value, str) or not value:
        raise IntegrationSecretDecryptionError(
            f"Encrypted integration secret envelope is missing {field}."
        )
    try:
        return base64.urlsafe_b64decode(value.encode("ascii"))
    except Exception as exc:
        raise IntegrationSecretDecryptionError(
            f"Encrypted integration secret envelope has invalid {field}."
        ) from exc


def encrypt_secret_map(
    *,
    user_id: str,
    integration: str,
    secrets: dict[str, str],
) -> dict[str, Any]:
    normalized = {
        str(key): str(value)
        for key, value in secrets.items()
        if key in SECRET_FIELD_NAMES
        and value not in (None, "")
    }
    if not normalized:
        raise ValueError("At least one integration secret is required.")

    key_version = current_key_version()
    nonce = os.urandom(_NONCE_BYTES)
    plaintext = json.dumps(
        normalized,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    ciphertext = AESGCM(
        _aead_key(key_version)
    ).encrypt(
        nonce,
        plaintext,
        _aad(
            user_id=user_id,
            integration=integration,
            key_version=key_version,
        ),
    )

    return {
        "v": _ENVELOPE_VERSION,
        "alg": _ALGORITHM,
        "keyVersion": key_version,
        "nonce": _b64encode(nonce),
        "ciphertext": _b64encode(ciphertext),
    }


def decrypt_secret_map(
    *,
    user_id: str,
    integration: str,
    envelope: dict[str, Any],
) -> dict[str, str]:
    if not isinstance(envelope, dict):
        raise IntegrationSecretDecryptionError(
            "Encrypted integration secret envelope is invalid."
        )

    if envelope.get("v") != _ENVELOPE_VERSION:
        raise IntegrationSecretDecryptionError(
            "Encrypted integration secret envelope version is unsupported."
        )
    if envelope.get("alg") != _ALGORITHM:
        raise IntegrationSecretDecryptionError(
            "Encrypted integration secret algorithm is unsupported."
        )

    key_version = str(envelope.get("keyVersion") or "").strip()
    if not key_version:
        raise IntegrationSecretDecryptionError(
            "Encrypted integration secret key version is missing."
        )

    nonce = _b64decode(envelope.get("nonce"), field="nonce")
    if len(nonce) != _NONCE_BYTES:
        raise IntegrationSecretDecryptionError(
            "Encrypted integration secret nonce has invalid length."
        )
    ciphertext = _b64decode(
        envelope.get("ciphertext"),
        field="ciphertext",
    )

    try:
        plaintext = AESGCM(
            _aead_key(key_version)
        ).decrypt(
            nonce,
            ciphertext,
            _aad(
                user_id=user_id,
                integration=integration,
                key_version=key_version,
            ),
        )
        decoded = json.loads(plaintext.decode("utf-8"))
    except IntegrationSecretConfigurationError:
        raise
    except Exception as exc:
        raise IntegrationSecretDecryptionError(
            "Stored integration credentials failed authenticated decryption."
        ) from exc

    if not isinstance(decoded, dict):
        raise IntegrationSecretDecryptionError(
            "Stored integration credential payload is invalid."
        )

    result: dict[str, str] = {}
    for key, value in decoded.items():
        if (
            key not in SECRET_FIELD_NAMES
            or not isinstance(value, str)
            or not value
        ):
            raise IntegrationSecretDecryptionError(
                "Stored integration credential payload contains invalid fields."
            )
        result[key] = value

    return result


def seal_token_data(
    *,
    user_id: str,
    integration: str,
    token_data: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(token_data, dict):
        raise TypeError("token_data must be a dictionary.")

    metadata: dict[str, Any] = {}
    secrets: dict[str, str] = {}

    for key, value in token_data.items():
        if key == "encryptedSecrets":
            continue
        if key in SECRET_FIELD_NAMES:
            if value not in (None, ""):
                secrets[key] = str(value)
            continue
        metadata[key] = value

    if secrets:
        metadata["encryptedSecrets"] = encrypt_secret_map(
            user_id=user_id,
            integration=integration,
            secrets=secrets,
        )
    elif isinstance(token_data.get("encryptedSecrets"), dict):
        metadata["encryptedSecrets"] = dict(
            token_data["encryptedSecrets"]
        )

    return metadata


def unseal_token_data(
    *,
    user_id: str,
    integration: str,
    token_data: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    # Return (runtime_token_data, migrated_storage_record).
    # migrated_storage_record is non-None only for legacy/plaintext cleanup.
    if not isinstance(token_data, dict):
        return {}, None

    envelope = token_data.get("encryptedSecrets")
    plaintext_fields = {
        key: value
        for key, value in token_data.items()
        if key in SECRET_FIELD_NAMES
        and value not in (None, "")
    }

    metadata = {
        key: value
        for key, value in token_data.items()
        if key not in SECRET_FIELD_NAMES
        and key != "encryptedSecrets"
    }

    if isinstance(envelope, dict):
        secrets = decrypt_secret_map(
            user_id=user_id,
            integration=integration,
            envelope=envelope,
        )
        runtime = {**metadata, **secrets}
        cleaned = (
            {**metadata, "encryptedSecrets": dict(envelope)}
            if plaintext_fields
            else None
        )
        return runtime, cleaned

    if plaintext_fields:
        runtime = {
            **metadata,
            **{
                key: str(value)
                for key, value in plaintext_fields.items()
            },
        }
        migrated = seal_token_data(
            user_id=user_id,
            integration=integration,
            token_data=runtime,
        )
        return runtime, migrated

    return metadata, None
