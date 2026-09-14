from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import os
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from firebase_admin import firestore


class BankRepositoryConfigurationError(RuntimeError):
    pass


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fernet_from_env() -> Fernet:
    key = (os.getenv("ENCRYPTION_KEY") or "").strip()
    if not key:
        raise BankRepositoryConfigurationError("ENCRYPTION_KEY is required for bank token storage.")
    try:
        return Fernet(key.encode())
    except (TypeError, ValueError) as exc:
        raise BankRepositoryConfigurationError("ENCRYPTION_KEY is not a valid Fernet key.") from exc


def _transaction_doc_id(transaction_id: str) -> str:
    return hashlib.sha256(transaction_id.encode("utf-8")).hexdigest()


class BankIntegrationRepository:
    def __init__(self, user_id: str, *, db: Any = None, fernet: Fernet | None = None) -> None:
        if not user_id:
            raise ValueError("user_id is required")
        self.user_id = user_id
        self.db = db if db is not None else firestore.client()
        self.fernet = fernet if fernet is not None else _fernet_from_env()
        self.bank_ref = (
            self.db.collection("users")
            .document(user_id)
            .collection("integrations")
            .document("bank")
        )

    @property
    def items_ref(self):
        return self.bank_ref.collection("items")

    def save_item(
        self,
        *,
        item_id: str,
        access_token: str,
        institution_id: str | None,
        institution_name: str | None,
        accounts: list[dict[str, Any]],
    ) -> None:
        encrypted = self.fernet.encrypt(access_token.encode()).decode()
        now = _utc_now_iso()
        self.bank_ref.set({"provider": "plaid", "updatedAt": now}, merge=True)
        self.items_ref.document(item_id).set(
            {
                "itemId": item_id,
                "encryptedAccessToken": encrypted,
                "institutionId": institution_id,
                "institutionName": institution_name,
                "accounts": accounts,
                "transactionsCursor": None,
                "health": "healthy",
                "needsAttention": False,
                "errorCode": None,
                "connectedAt": now,
                "updatedAt": now,
            },
            merge=True,
        )

    def list_items(self) -> list[dict[str, Any]]:
        return [snap.to_dict() or {} for snap in self.items_ref.stream() if snap.exists]

    def get_item(self, item_id: str) -> dict[str, Any] | None:
        snap = self.items_ref.document(item_id).get()
        return (snap.to_dict() or {}) if snap.exists else None

    def access_token_for_item(self, item_id: str) -> str:
        item = self.get_item(item_id) or {}
        encrypted = str(item.get("encryptedAccessToken") or "")
        if not encrypted:
            raise BankRepositoryConfigurationError("Stored bank connection token is unavailable.")
        try:
            return self.fernet.decrypt(encrypted.encode()).decode()
        except InvalidToken as exc:
            raise BankRepositoryConfigurationError("Stored bank connection token cannot be decrypted.") from exc

    def update_accounts(self, item_id: str, accounts: list[dict[str, Any]]) -> None:
        self.items_ref.document(item_id).set(
            {"accounts": accounts, "updatedAt": _utc_now_iso()},
            merge=True,
        )

    def update_item_status(
        self,
        item_id: str,
        *,
        health: str,
        needs_attention: bool,
        error_code: str | None,
        consent_expiration_time: str | None,
        last_successful_update: str | None,
        last_failed_update: str | None,
    ) -> None:
        self.items_ref.document(item_id).set(
            {
                "health": health,
                "needsAttention": needs_attention,
                "errorCode": error_code,
                "consentExpirationTime": consent_expiration_time,
                "lastSuccessfulUpdate": last_successful_update,
                "lastFailedUpdate": last_failed_update,
                "updatedAt": _utc_now_iso(),
            },
            merge=True,
        )

    def update_cursor(self, item_id: str, cursor: str | None) -> None:
        now = _utc_now_iso()
        self.items_ref.document(item_id).set(
            {"transactionsCursor": cursor, "lastSyncedAt": now, "updatedAt": now},
            merge=True,
        )

    def upsert_transaction(self, item_id: str, transaction: dict[str, Any]) -> None:
        transaction_id = str(transaction.get("transaction_id") or "")
        if not transaction_id:
            return
        (
            self.items_ref.document(item_id)
            .collection("transactions")
            .document(_transaction_doc_id(transaction_id))
            .set(transaction, merge=True)
        )

    def delete_transaction(self, item_id: str, transaction_id: str) -> None:
        if not transaction_id:
            return
        (
            self.items_ref.document(item_id)
            .collection("transactions")
            .document(_transaction_doc_id(transaction_id))
            .delete()
        )

    def list_transactions(self, *, limit: int = 100) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for item in self.list_items():
            item_id = str(item.get("itemId") or "")
            if not item_id:
                continue
            for snap in self.items_ref.document(item_id).collection("transactions").stream():
                if snap.exists:
                    row = snap.to_dict() or {}
                    row.setdefault("item_id", item_id)
                    rows.append(row)
        rows.sort(
            key=lambda row: (str(row.get("date") or ""), str(row.get("transaction_id") or "")),
            reverse=True,
        )
        return rows[: max(1, min(limit, 500))]

    def delete_item(self, item_id: str) -> None:
        item_ref = self.items_ref.document(item_id)
        for snapshot in item_ref.collection("transactions").stream():
            snapshot.reference.delete()
        item_ref.delete()

    @staticmethod
    def public_item(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "item_id": item.get("itemId"),
            "institution_id": item.get("institutionId"),
            "institution_name": item.get("institutionName"),
            "accounts": item.get("accounts") or [],
            "connected_at": item.get("connectedAt"),
            "last_synced_at": item.get("lastSyncedAt"),
            "health": item.get("health") or "unknown",
            "needs_attention": bool(item.get("needsAttention")),
            "error_code": item.get("errorCode"),
            "consent_expiration_time": item.get("consentExpirationTime"),
            "last_successful_update": item.get("lastSuccessfulUpdate"),
            "last_failed_update": item.get("lastFailedUpdate"),
        }
