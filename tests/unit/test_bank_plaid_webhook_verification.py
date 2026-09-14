import asyncio
import base64
import hashlib
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from jose import jwt

from masyg_extractor.integrations.bank.webhook_verifier import (
    PlaidWebhookVerificationError,
    PlaidWebhookVerifier,
)


ROOT = Path(__file__).resolve().parents[2]


def _b64url_uint(value: int) -> str:
    raw = value.to_bytes(32, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _ec_fixture():
    private_key = ec.generate_private_key(ec.SECP256R1())
    public_numbers = private_key.public_key().public_numbers()

    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )

    public_jwk = {
        "alg": "ES256",
        "crv": "P-256",
        "kid": "unit-test-key",
        "kty": "EC",
        "use": "sig",
        "x": _b64url_uint(public_numbers.x),
        "y": _b64url_uint(public_numbers.y),
    }
    return private_pem, public_jwk


class FakePlaidClient:
    def __init__(self, key: dict):
        self.key = key
        self.calls = 0

    async def get_webhook_verification_key(self, key_id: str):
        self.calls += 1
        assert key_id == self.key["kid"]
        return dict(self.key)


def _signed_webhook(
    raw_body: bytes,
    private_pem: bytes,
    *,
    issued_at: int,
) -> str:
    return jwt.encode(
        {
            "iat": issued_at,
            "request_body_sha256": hashlib.sha256(raw_body).hexdigest(),
        },
        private_pem,
        algorithm="ES256",
        headers={
            "alg": "ES256",
            "kid": "unit-test-key",
            "typ": "JWT",
        },
    )


def test_verifier_accepts_exact_signed_raw_body_and_caches_jwk():
    private_pem, public_jwk = _ec_fixture()
    client = FakePlaidClient(public_jwk)
    verifier = PlaidWebhookVerifier(client, clock=lambda: 1_000)

    raw_body = (
        b'{"webhook_type":"TRANSACTIONS","webhook_code":'
        b'"SYNC_UPDATES_AVAILABLE","item_id":"item-1"}'
    )
    token = _signed_webhook(
        raw_body,
        private_pem,
        issued_at=999,
    )

    first = asyncio.run(verifier.verify(raw_body, token))
    second = asyncio.run(verifier.verify(raw_body, token))

    assert first["request_body_sha256"] == hashlib.sha256(raw_body).hexdigest()
    assert second["iat"] == 999
    assert client.calls == 1


def test_verifier_rejects_body_that_changed_after_signing():
    private_pem, public_jwk = _ec_fixture()
    verifier = PlaidWebhookVerifier(
        FakePlaidClient(public_jwk),
        clock=lambda: 1_000,
    )

    original = b'{"webhook_type":"ITEM"}'
    changed = b'{ "webhook_type": "ITEM" }'
    token = _signed_webhook(
        original,
        private_pem,
        issued_at=999,
    )

    with pytest.raises(
        PlaidWebhookVerificationError,
        match="body hash",
    ):
        asyncio.run(verifier.verify(changed, token))


def test_verifier_rejects_webhooks_older_than_five_minutes():
    private_pem, public_jwk = _ec_fixture()
    verifier = PlaidWebhookVerifier(
        FakePlaidClient(public_jwk),
        clock=lambda: 1_000,
    )

    raw_body = b'{"webhook_type":"ITEM"}'
    token = _signed_webhook(
        raw_body,
        private_pem,
        issued_at=699,
    )

    with pytest.raises(
        PlaidWebhookVerificationError,
        match="too old",
    ):
        asyncio.run(verifier.verify(raw_body, token))


def test_verifier_rejects_non_es256_before_key_lookup():
    _, public_jwk = _ec_fixture()
    client = FakePlaidClient(public_jwk)
    verifier = PlaidWebhookVerifier(client, clock=lambda: 1_000)

    token = jwt.encode(
        {
            "iat": 999,
            "request_body_sha256": hashlib.sha256(b"{}").hexdigest(),
        },
        "unit-test-secret",
        algorithm="HS256",
        headers={
            "kid": "unit-test-key",
            "typ": "JWT",
        },
    )

    with pytest.raises(
        PlaidWebhookVerificationError,
        match="ES256",
    ):
        asyncio.run(verifier.verify(b"{}", token))

    assert client.calls == 0


def test_webhook_route_reads_raw_body_before_json_and_uses_no_browser_auth():
    router = (
        ROOT / "masyg_extractor/integrations/bank/router.py"
    ).read_text()
    client = (
        ROOT / "masyg_extractor/integrations/bank/plaid_client.py"
    ).read_text()

    start = router.index('@router.post("/webhook")')
    end = router.index('@router.post("/link-token")', start)
    block = router[start:end]

    assert "raw_body = await request.body()" in block
    assert 'request.headers.get("Plaid-Verification")' in block
    assert "_webhook_verifier.verify(raw_body, signed_jwt)" in block
    assert "json.loads(raw_body.decode" in block
    assert "request.json()" not in block
    assert "Depends(get_current_user_from_cookie)" not in block

    assert '"/webhook_verification_key/get"' in client
    assert '{"key_id": key_id}' in client
