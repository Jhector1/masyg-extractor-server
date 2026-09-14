from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any

import httpx


class PlaidConfigurationError(RuntimeError):
    pass


class PlaidApiError(RuntimeError):
    def __init__(self, message: str, *, error_code: str | None = None, request_id: str | None = None) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.request_id = request_id


@dataclass(frozen=True)
class PlaidClientConfig:
    client_id: str
    secret: str
    base_url: str
    country_codes: tuple[str, ...]
    transactions_days: int
    client_name: str
    webhook_url: str | None = None

    @classmethod
    def from_env(cls) -> "PlaidClientConfig":
        plaid_env = (os.getenv("PLAID_ENV") or "").strip().lower()
        app_env = (os.getenv("FAST_API_ENV") or "development").strip().lower()
        if not plaid_env:
            if app_env == "production":
                raise PlaidConfigurationError("PLAID_ENV must be configured explicitly in production.")
            plaid_env = "sandbox"

        hosts = {
            "sandbox": "https://sandbox.plaid.com",
            "production": "https://production.plaid.com",
        }
        base_url = hosts.get(plaid_env)
        if not base_url:
            raise PlaidConfigurationError("PLAID_ENV must be either 'sandbox' or 'production'.")

        client_id = (os.getenv("PLAID_CLIENT_ID") or "").strip()
        secret = (os.getenv("PLAID_SECRET") or "").strip()
        if not client_id or not secret:
            raise PlaidConfigurationError("Plaid credentials are not configured.")

        countries = tuple(
            code.strip().upper()
            for code in (os.getenv("PLAID_COUNTRY_CODES") or "US").split(",")
            if code.strip()
        ) or ("US",)

        try:
            days = int(os.getenv("PLAID_TRANSACTIONS_DAYS", "90"))
        except ValueError as exc:
            raise PlaidConfigurationError("PLAID_TRANSACTIONS_DAYS must be an integer.") from exc
        if not 1 <= days <= 730:
            raise PlaidConfigurationError("PLAID_TRANSACTIONS_DAYS must be between 1 and 730.")

        client_name = (os.getenv("PLAID_CLIENT_NAME") or "Masyg Extractor").strip() or "Masyg Extractor"
        webhook_url = (os.getenv("PLAID_WEBHOOK_URL") or "").strip() or None

        return cls(
            client_id=client_id,
            secret=secret,
            base_url=base_url,
            country_codes=countries,
            transactions_days=days,
            client_name=client_name[:30],
            webhook_url=webhook_url,
        )


class PlaidClient:
    def __init__(self, config: PlaidClientConfig | None = None, *, timeout_seconds: float = 25.0) -> None:
        self._config = config
        self.timeout_seconds = timeout_seconds

    def _resolved_config(self) -> PlaidClientConfig:
        if self._config is None:
            self._config = PlaidClientConfig.from_env()
        return self._config

    async def _post(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        config = self._resolved_config()
        headers = {
            "Content-Type": "application/json",
            "PLAID-CLIENT-ID": config.client_id,
            "PLAID-SECRET": config.secret,
        }
        try:
            async with httpx.AsyncClient(base_url=config.base_url, timeout=self.timeout_seconds) as client:
                response = await client.post(endpoint, json=payload, headers=headers)
        except httpx.RequestError as exc:
            raise PlaidApiError("Unable to reach the bank connection provider.") from exc

        try:
            body = response.json()
        except ValueError:
            body = {}

        if response.is_error:
            raise PlaidApiError(
                str(body.get("error_message") or "Bank connection provider rejected the request."),
                error_code=str(body.get("error_code") or "").strip() or None,
                request_id=str(body.get("request_id") or "").strip() or None,
            )
        if not isinstance(body, dict):
            raise PlaidApiError("Unexpected bank connection provider response.")
        return body

    async def create_link_token(self, user_id: str) -> dict[str, Any]:
        config = self._resolved_config()
        payload: dict[str, Any] = {
            "client_name": config.client_name,
            "language": "en",
            "country_codes": list(config.country_codes),
            "user": {"client_user_id": user_id},
            "products": ["transactions"],
            "transactions": {"days_requested": config.transactions_days},
        }
        if config.webhook_url:
            payload["webhook"] = config.webhook_url
        return await self._post("/link/token/create", payload)

    async def create_update_link_token(
        self,
        user_id: str,
        access_token: str,
    ) -> dict[str, Any]:
        config = self._resolved_config()
        payload: dict[str, Any] = {
            "client_name": config.client_name,
            "language": "en",
            "country_codes": list(config.country_codes),
            "user": {"client_user_id": user_id},
            "access_token": access_token,
        }
        if config.webhook_url:
            payload["webhook"] = config.webhook_url
        return await self._post("/link/token/create", payload)

    async def exchange_public_token(self, public_token: str) -> dict[str, Any]:
        return await self._post("/item/public_token/exchange", {"public_token": public_token})

    async def get_accounts(self, access_token: str) -> dict[str, Any]:
        return await self._post("/accounts/get", {"access_token": access_token})

    async def get_item(self, access_token: str) -> dict[str, Any]:
        return await self._post("/item/get", {"access_token": access_token})

    async def remove_item(self, access_token: str) -> dict[str, Any]:
        return await self._post("/item/remove", {"access_token": access_token})

    async def get_webhook_verification_key(
        self,
        key_id: str,
    ) -> dict[str, Any]:
        response = await self._post(
            "/webhook_verification_key/get",
            {"key_id": key_id},
        )
        key = response.get("key")
        if not isinstance(key, dict):
            raise PlaidApiError(
                "Bank connection provider did not return a webhook verification key."
            )
        return key

    async def sync_transactions(self, access_token: str, *, cursor: str | None) -> dict[str, Any]:
        payload: dict[str, Any] = {"access_token": access_token}
        if cursor is not None:
            payload["cursor"] = cursor
        return await self._post("/transactions/sync", payload)
