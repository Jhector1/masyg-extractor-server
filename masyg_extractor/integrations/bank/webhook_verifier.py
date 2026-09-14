from __future__ import annotations

import hashlib
import hmac
import time
from collections.abc import Callable
from typing import Any

from jose import jwk, jwt
from jose.exceptions import JOSEError

from masyg_extractor.integrations.bank.plaid_client import PlaidClient


MAX_WEBHOOK_AGE_SECONDS = 5 * 60


class PlaidWebhookVerificationError(RuntimeError):
    pass


class PlaidWebhookVerifier:
    """
    Verify Plaid webhook authenticity before webhook JSON is parsed.

    The request body must be the exact raw bytes received from Plaid because
    request_body_sha256 is whitespace-sensitive.
    """

    def __init__(
        self,
        client: PlaidClient | None = None,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.client = client if client is not None else PlaidClient()
        self.clock = clock
        self._key_cache: dict[str, dict[str, Any]] = {}

    async def _verification_key(self, kid: str) -> dict[str, Any]:
        cached = self._key_cache.get(kid)
        if cached is not None:
            return cached

        key = await self.client.get_webhook_verification_key(kid)

        if str(key.get("kid") or "") != kid:
            raise PlaidWebhookVerificationError(
                "Plaid webhook verification key id mismatch."
            )
        if str(key.get("alg") or "") != "ES256":
            raise PlaidWebhookVerificationError(
                "Plaid webhook verification key algorithm is invalid."
            )
        if str(key.get("kty") or "") != "EC":
            raise PlaidWebhookVerificationError(
                "Plaid webhook verification key type is invalid."
            )
        if str(key.get("crv") or "") != "P-256":
            raise PlaidWebhookVerificationError(
                "Plaid webhook verification key curve is invalid."
            )

        self._key_cache[kid] = dict(key)
        return self._key_cache[kid]

    async def verify(
        self,
        raw_body: bytes,
        signed_jwt: str | None,
    ) -> dict[str, Any]:
        token = str(signed_jwt or "").strip()
        if not token:
            raise PlaidWebhookVerificationError(
                "Missing Plaid-Verification header."
            )

        try:
            header = jwt.get_unverified_header(token)
        except JOSEError as exc:
            raise PlaidWebhookVerificationError(
                "Malformed Plaid webhook verification token."
            ) from exc

        if str(header.get("alg") or "") != "ES256":
            raise PlaidWebhookVerificationError(
                "Plaid webhook verification token must use ES256."
            )

        kid = str(header.get("kid") or "").strip()
        if not kid:
            raise PlaidWebhookVerificationError(
                "Plaid webhook verification token is missing kid."
            )

        key_data = await self._verification_key(kid)

        try:
            key = jwk.construct(key_data, algorithm="ES256")
            claims = jwt.decode(
                token,
                key.to_pem(),
                algorithms=["ES256"],
                options={
                    "verify_aud": False,
                    "verify_iat": False,
                    "verify_nbf": False,
                },
            )
        except JOSEError as exc:
            raise PlaidWebhookVerificationError(
                "Plaid webhook signature is invalid."
            ) from exc

        iat = claims.get("iat")
        if isinstance(iat, bool) or not isinstance(iat, (int, float)):
            raise PlaidWebhookVerificationError(
                "Plaid webhook verification token is missing a valid iat."
            )

        age_seconds = self.clock() - float(iat)
        if age_seconds > MAX_WEBHOOK_AGE_SECONDS:
            raise PlaidWebhookVerificationError(
                "Plaid webhook verification token is too old."
            )

        claimed_hash = claims.get("request_body_sha256")
        if not isinstance(claimed_hash, str) or not claimed_hash:
            raise PlaidWebhookVerificationError(
                "Plaid webhook verification token is missing the body hash."
            )

        actual_hash = hashlib.sha256(raw_body).hexdigest()
        if not hmac.compare_digest(actual_hash, claimed_hash):
            raise PlaidWebhookVerificationError(
                "Plaid webhook body hash does not match."
            )

        return claims
