from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import os
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from firebase_admin import firestore

from masyg_extractor.integrations.bank.webhook_repository import (
    BankWebhookRepository,
)


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


def _reconciliation_claim_doc_id(
    group_id: str,
    file_id: str,
) -> str:
    raw = (
        group_id.encode("utf-8")
        + b"\0"
        + file_id.encode("utf-8")
    )
    return hashlib.sha256(raw).hexdigest()


class BankIntegrationRepository:
    def __init__(self, user_id: str, *, db: Any = None, fernet: Fernet | None = None) -> None:
        if not user_id:
            raise ValueError("user_id is required")
        self.user_id = user_id
        self.db = db if db is not None else firestore.client()
        self.fernet = fernet if fernet is not None else _fernet_from_env()
        self.webhook_repository = BankWebhookRepository(db=self.db)
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
        self.webhook_repository.register_item_owner(
            item_id,
            self.user_id,
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
        if not item_id or not transaction_id:
            return

        transaction_ref = (
            self.items_ref.document(item_id)
            .collection("transactions")
            .document(_transaction_doc_id(transaction_id))
        )

        claims_ref = self.bank_ref.collection("reconciliationClaims")
        transaction = self.db.transaction()

        @firestore.transactional
        def _delete(txn):
            snapshot = transaction_ref.get(transaction=txn)
            if not snapshot.exists:
                return

            row = snapshot.to_dict() or {}
            reconciliation = row.get("reconciliation") or {}

            group_id = (
                str(reconciliation.get("group_id") or "").strip()
                if isinstance(reconciliation, dict)
                and reconciliation.get("status") == "matched"
                else ""
            )
            file_id = (
                str(reconciliation.get("file_id") or "").strip()
                if isinstance(reconciliation, dict)
                and reconciliation.get("status") == "matched"
                else ""
            )

            claim_ref = (
                claims_ref.document(
                    _reconciliation_claim_doc_id(
                        group_id,
                        file_id,
                    )
                )
                if group_id and file_id
                else None
            )

            claim_snapshot = (
                claim_ref.get(transaction=txn)
                if claim_ref is not None
                else None
            )

            # Complete every read before beginning transaction writes.
            if (
                claim_ref is not None
                and claim_snapshot is not None
                and claim_snapshot.exists
            ):
                owner = claim_snapshot.to_dict() or {}

                if (
                    str(owner.get("itemId") or "") == item_id
                    and str(owner.get("transactionId") or "")
                    == transaction_id
                ):
                    txn.delete(claim_ref)

            txn.delete(transaction_ref)

        _delete(transaction)

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

    def get_transaction(
        self,
        item_id: str,
        transaction_id: str,
    ) -> dict[str, Any] | None:
        if not item_id or not transaction_id:
            return None

        snapshot = (
            self.items_ref.document(item_id)
            .collection("transactions")
            .document(_transaction_doc_id(transaction_id))
            .get()
        )

        if not snapshot.exists:
            return None

        row = snapshot.to_dict() or {}
        row.setdefault("item_id", item_id)
        return row

    def update_transaction_reconciliation(
        self,
        item_id: str,
        transaction_id: str,
        reconciliation: dict[str, Any],
    ) -> None:
        if not item_id or not transaction_id:
            raise ValueError("item_id and transaction_id are required")

        transaction_ref = (
            self.items_ref.document(item_id)
            .collection("transactions")
            .document(_transaction_doc_id(transaction_id))
        )

        claims_ref = self.bank_ref.collection("reconciliationClaims")
        transaction = self.db.transaction()

        @firestore.transactional
        def _update(txn):
            snapshot = transaction_ref.get(transaction=txn)
            if not snapshot.exists:
                raise KeyError("Bank transaction not found.")

            current = snapshot.to_dict() or {}
            previous = current.get("reconciliation") or {}

            old_group_id = (
                str(previous.get("group_id") or "").strip()
                if isinstance(previous, dict)
                and previous.get("status") == "matched"
                else ""
            )
            old_file_id = (
                str(previous.get("file_id") or "").strip()
                if isinstance(previous, dict)
                and previous.get("status") == "matched"
                else ""
            )

            new_status = str(
                reconciliation.get("status") or ""
            ).strip()

            new_group_id = (
                str(reconciliation.get("group_id") or "").strip()
                if new_status == "matched"
                else ""
            )
            new_file_id = (
                str(reconciliation.get("file_id") or "").strip()
                if new_status == "matched"
                else ""
            )

            if new_status == "matched" and (
                not new_group_id or not new_file_id
            ):
                raise ValueError(
                    "Matched reconciliation requires a document identity."
                )

            old_claim_ref = (
                claims_ref.document(
                    _reconciliation_claim_doc_id(old_group_id, old_file_id)
                )
                if old_group_id and old_file_id
                else None
            )

            new_claim_ref = (
                claims_ref.document(
                    _reconciliation_claim_doc_id(new_group_id, new_file_id)
                )
                if new_group_id and new_file_id
                else None
            )

            old_claim_snapshot = (
                old_claim_ref.get(transaction=txn)
                if old_claim_ref is not None
                else None
            )

            if (
                new_claim_ref is not None
                and old_claim_ref is not None
                and new_claim_ref.path == old_claim_ref.path
            ):
                new_claim_snapshot = old_claim_snapshot
            elif new_claim_ref is not None:
                new_claim_snapshot = new_claim_ref.get(
                    transaction=txn
                )
            else:
                new_claim_snapshot = None

            if (
                new_claim_snapshot is not None
                and new_claim_snapshot.exists
            ):
                owner = new_claim_snapshot.to_dict() or {}

                same_owner = (
                    str(owner.get("itemId") or "") == item_id
                    and str(owner.get("transactionId") or "")
                    == transaction_id
                )

                if not same_owner:
                    raise ValueError(
                        "Document is already matched to another "
                        "bank transaction."
                    )

            # All reads happen before writes so the Firestore transaction
            # remains valid under concurrent match attempts.

            if (
                old_claim_ref is not None
                and (
                    new_claim_ref is None
                    or new_claim_ref.path != old_claim_ref.path
                )
                and old_claim_snapshot is not None
                and old_claim_snapshot.exists
            ):
                owner = old_claim_snapshot.to_dict() or {}

                if (
                    str(owner.get("itemId") or "") == item_id
                    and str(owner.get("transactionId") or "")
                    == transaction_id
                ):
                    txn.delete(old_claim_ref)

            if new_claim_ref is not None:
                txn.set(
                    new_claim_ref,
                    {
                        "groupId": new_group_id,
                        "fileId": new_file_id,
                        "itemId": item_id,
                        "transactionId": transaction_id,
                        "updatedAt": _utc_now_iso(),
                    },
                )

            # Replace the complete local reconciliation map. Provider
            # transaction fields remain untouched.
            txn.update(
                transaction_ref,
                {
                    "reconciliation": reconciliation,
                },
            )

        _update(transaction)

    def delete_item(self, item_id: str) -> None:
        item_ref = self.items_ref.document(item_id)

        for snapshot in item_ref.collection("transactions").stream():
            row = snapshot.to_dict() or {}
            transaction_id = str(
                row.get("transaction_id") or ""
            ).strip()

            if transaction_id:
                self.delete_transaction(
                    item_id,
                    transaction_id,
                )
            else:
                # A legacy/corrupt row without its Plaid transaction id
                # cannot own a canonical reconciliation claim.
                snapshot.reference.delete()

        # Best-effort cleanup for orphaned claims that may predate
        # claim-aware transaction deletion.
        for claim_snapshot in (
            self.bank_ref
            .collection("reconciliationClaims")
            .stream()
        ):
            if not claim_snapshot.exists:
                continue

            claim = claim_snapshot.to_dict() or {}

            if str(claim.get("itemId") or "") == item_id:
                claim_snapshot.reference.delete()

        item_ref.delete()

        self.webhook_repository.unregister_item_owner(
            item_id,
            self.user_id,
        )

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
