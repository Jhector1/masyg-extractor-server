from __future__ import annotations

import asyncio
from typing import Any

from masyg_extractor.integrations.bank.plaid_client import PlaidApiError, PlaidClient
from masyg_extractor.integrations.bank.repository import BankIntegrationRepository


SYNC_MUTATION_CODE = "TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION"


def normalize_account(account: dict[str, Any], *, item_id: str) -> dict[str, Any]:
    balances = account.get("balances") or {}
    return {
        "item_id": item_id,
        "account_id": account.get("account_id"),
        "name": account.get("name"),
        "official_name": account.get("official_name"),
        "mask": account.get("mask"),
        "type": account.get("type"),
        "subtype": account.get("subtype"),
        "balances": {
            "available": balances.get("available"),
            "current": balances.get("current"),
            "limit": balances.get("limit"),
            "iso_currency_code": balances.get("iso_currency_code"),
            "unofficial_currency_code": balances.get("unofficial_currency_code"),
        },
    }


def normalize_transaction(transaction: dict[str, Any], *, item_id: str) -> dict[str, Any]:
    category = transaction.get("personal_finance_category") or {}
    return {
        "item_id": item_id,
        "transaction_id": transaction.get("transaction_id"),
        "account_id": transaction.get("account_id"),
        "name": transaction.get("name"),
        "merchant_name": transaction.get("merchant_name"),
        "date": transaction.get("date"),
        "authorized_date": transaction.get("authorized_date"),
        "amount": transaction.get("amount"),
        "iso_currency_code": transaction.get("iso_currency_code"),
        "unofficial_currency_code": transaction.get("unofficial_currency_code"),
        "pending": bool(transaction.get("pending")),
        "payment_channel": transaction.get("payment_channel"),
        "category": category.get("primary"),
        "category_detail": category.get("detailed"),
    }


def normalize_item_status(response: dict[str, Any]) -> dict[str, Any]:
    item = response.get("item") or {}
    status = response.get("status") or {}
    transactions = status.get("transactions") or {}
    error = item.get("error") or {}
    error_code = str(error.get("error_code") or "").strip() or None
    health = "healthy"
    if error_code == "ITEM_LOGIN_REQUIRED":
        health = "needs_attention"
    elif error_code:
        health = "error"
    return {
        "health": health,
        "needs_attention": bool(error_code),
        "error_code": error_code,
        "consent_expiration_time": item.get("consent_expiration_time"),
        "last_successful_update": transactions.get("last_successful_update"),
        "last_failed_update": transactions.get("last_failed_update"),
    }


class BankService:
    def __init__(
        self,
        user_id: str,
        *,
        client: PlaidClient | None = None,
        repository: BankIntegrationRepository | None = None,
    ) -> None:
        self.user_id = user_id
        self.client = client if client is not None else PlaidClient()
        self.repository = repository if repository is not None else BankIntegrationRepository(user_id)

    async def create_link_token(self) -> dict[str, Any]:
        response = await self.client.create_link_token(self.user_id)
        return {"link_token": response.get("link_token"), "expiration": response.get("expiration")}

    async def create_update_link_token(self, item_id: str) -> dict[str, Any]:
        item = await asyncio.to_thread(self.repository.get_item, item_id)
        if not item:
            raise KeyError("Bank connection not found.")
        access_token = await asyncio.to_thread(self.repository.access_token_for_item, item_id)
        response = await self.client.create_update_link_token(self.user_id, access_token)
        return {"link_token": response.get("link_token"), "expiration": response.get("expiration")}

    async def exchange_public_token(
        self,
        public_token: str,
        *,
        institution_id: str | None = None,
        institution_name: str | None = None,
    ) -> dict[str, Any]:
        exchanged = await self.client.exchange_public_token(public_token)
        item_id = str(exchanged.get("item_id") or "")
        access_token = str(exchanged.get("access_token") or "")
        if not item_id or not access_token:
            raise PlaidApiError("Bank connection provider did not return a usable connection.")

        account_response = await self.client.get_accounts(access_token)
        accounts = [
            normalize_account(row, item_id=item_id)
            for row in (account_response.get("accounts") or [])
            if isinstance(row, dict)
        ]
        item = account_response.get("item") or {}
        provider_institution_id = str(item.get("institution_id") or institution_id or "").strip() or None

        await asyncio.to_thread(
            self.repository.save_item,
            item_id=item_id,
            access_token=access_token,
            institution_id=provider_institution_id,
            institution_name=institution_name,
            accounts=accounts,
        )
        await self.refresh_item_status(item_id)
        return {"connected": True, "item_id": item_id, "accounts": accounts}

    async def refresh_item_status(self, item_id: str) -> dict[str, Any]:
        access_token = await asyncio.to_thread(self.repository.access_token_for_item, item_id)
        response = await self.client.get_item(access_token)
        normalized = normalize_item_status(response)
        await asyncio.to_thread(
            self.repository.update_item_status,
            item_id,
            health=normalized["health"],
            needs_attention=normalized["needs_attention"],
            error_code=normalized["error_code"],
            consent_expiration_time=normalized["consent_expiration_time"],
            last_successful_update=normalized["last_successful_update"],
            last_failed_update=normalized["last_failed_update"],
        )
        return normalized

    async def accounts(self) -> dict[str, Any]:
        items = await asyncio.to_thread(self.repository.list_items)
        if not items:
            return {"connected": False, "items": [], "accounts": []}

        public_items: list[dict[str, Any]] = []
        accounts: list[dict[str, Any]] = []

        for item in items:
            item_id = str(item.get("itemId") or "")
            if not item_id:
                continue

            # Item health must be resolved before account retrieval.
            #
            # Plaid intentionally rejects product endpoints such as
            # /accounts/get when an Item enters ITEM_LOGIN_REQUIRED. We still
            # need to surface the existing connection and its stored account
            # metadata so the client can offer Link update mode.
            item_status = await self.refresh_item_status(item_id)

            refreshed = dict(item)
            refreshed["health"] = item_status["health"]
            refreshed["needsAttention"] = item_status["needs_attention"]
            refreshed["errorCode"] = item_status["error_code"]
            refreshed["consentExpirationTime"] = item_status["consent_expiration_time"]
            refreshed["lastSuccessfulUpdate"] = item_status["last_successful_update"]
            refreshed["lastFailedUpdate"] = item_status["last_failed_update"]

            if item_status["needs_attention"]:
                stored_accounts = [
                    row
                    for row in (item.get("accounts") or [])
                    if isinstance(row, dict)
                ]
                refreshed["accounts"] = stored_accounts
                public_items.append(self.repository.public_item(refreshed))
                accounts.extend(stored_accounts)
                continue

            access_token = await asyncio.to_thread(
                self.repository.access_token_for_item,
                item_id,
            )
            response = await self.client.get_accounts(access_token)

            normalized = [
                normalize_account(row, item_id=item_id)
                for row in (response.get("accounts") or [])
                if isinstance(row, dict)
            ]

            await asyncio.to_thread(
                self.repository.update_accounts,
                item_id,
                normalized,
            )

            refreshed["accounts"] = normalized
            public_items.append(self.repository.public_item(refreshed))
            accounts.extend(normalized)

        return {
            "connected": bool(public_items),
            "items": public_items,
            "accounts": accounts,
        }

    async def transactions(self, *, limit: int = 100) -> dict[str, Any]:
        rows = await asyncio.to_thread(self.repository.list_transactions, limit=limit)
        return {"transactions": rows}

    async def disconnect(self, item_id: str) -> dict[str, Any]:
        item = await asyncio.to_thread(self.repository.get_item, item_id)
        if not item:
            raise KeyError("Bank connection not found.")
        access_token = await asyncio.to_thread(self.repository.access_token_for_item, item_id)
        await self.client.remove_item(access_token)
        await asyncio.to_thread(self.repository.delete_item, item_id)
        remaining = await asyncio.to_thread(self.repository.list_items)
        return {"disconnected": True, "connected": bool(remaining)}

    async def _collect_pages(
        self,
        *,
        access_token: str,
        starting_cursor: str | None,
    ):
        cursor = starting_cursor
        added: list[dict[str, Any]] = []
        modified: list[dict[str, Any]] = []
        removed: list[str] = []
        latest_accounts: list[dict[str, Any]] = []

        for _ in range(100):
            response = await self.client.sync_transactions(access_token, cursor=cursor)
            added.extend(row for row in (response.get("added") or []) if isinstance(row, dict))
            modified.extend(row for row in (response.get("modified") or []) if isinstance(row, dict))
            removed.extend(
                str(row.get("transaction_id") or "")
                for row in (response.get("removed") or [])
                if isinstance(row, dict)
            )
            latest_accounts = [row for row in (response.get("accounts") or []) if isinstance(row, dict)]
            cursor = response.get("next_cursor")
            if not response.get("has_more"):
                return added, modified, removed, cursor, latest_accounts

        raise PlaidApiError("Transaction sync exceeded the pagination safety limit.")

    async def _sync_item(self, item: dict[str, Any]) -> dict[str, int]:
        item_id = str(item.get("itemId") or "")
        if not item_id:
            return {"added": 0, "modified": 0, "removed": 0}

        access_token = await asyncio.to_thread(self.repository.access_token_for_item, item_id)
        starting_cursor = item.get("transactionsCursor")

        for attempt in range(2):
            try:
                added, modified, removed, next_cursor, accounts = await self._collect_pages(
                    access_token=access_token,
                    starting_cursor=starting_cursor,
                )
                break
            except PlaidApiError as exc:
                if attempt == 0 and exc.error_code == SYNC_MUTATION_CODE:
                    continue
                raise
        else:
            raise PlaidApiError("Transaction sync could not be completed.")

        normalized_accounts = [normalize_account(row, item_id=item_id) for row in accounts]
        if normalized_accounts:
            await asyncio.to_thread(self.repository.update_accounts, item_id, normalized_accounts)

        for row in added:
            await asyncio.to_thread(
                self.repository.upsert_transaction,
                item_id,
                normalize_transaction(row, item_id=item_id),
            )
        for row in modified:
            await asyncio.to_thread(
                self.repository.upsert_transaction,
                item_id,
                normalize_transaction(row, item_id=item_id),
            )
        for transaction_id in removed:
            if transaction_id:
                await asyncio.to_thread(self.repository.delete_transaction, item_id, transaction_id)

        await asyncio.to_thread(self.repository.update_cursor, item_id, next_cursor)
        await self.refresh_item_status(item_id)
        return {
            "added": len(added),
            "modified": len(modified),
            "removed": len([value for value in removed if value]),
        }

    async def sync_item(self, item_id: str) -> dict[str, int]:
        item = await asyncio.to_thread(
            self.repository.get_item,
            item_id,
        )
        if not item:
            raise KeyError("Bank connection not found.")
        return await self._sync_item(item)

    async def sync_transactions(self) -> dict[str, Any]:
        items = await asyncio.to_thread(self.repository.list_items)
        if not items:
            return {
                "connected": False,
                "added": 0,
                "modified": 0,
                "removed": 0,
                "transactions": [],
            }

        totals = {"added": 0, "modified": 0, "removed": 0}
        for item in items:
            item_id = str(item.get("itemId") or "")
            if not item_id:
                continue
            result = await self.sync_item(item_id)
            for key in totals:
                totals[key] += result[key]

        transactions = await asyncio.to_thread(self.repository.list_transactions, limit=100)
        return {"connected": True, **totals, "transactions": transactions}
