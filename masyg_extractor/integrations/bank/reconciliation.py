from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from enum import Enum
import re
import unicodedata
from typing import Any, Mapping

from masyg_extractor.integrations.accounting.core.document_intent import (
    AccountingIntent,
    default_accounting_intent,
)
from masyg_extractor.integrations.bank.repository import BankIntegrationRepository
from masyg_extractor.services.analytics import as_number, line_total


class ReconciliationStatus(str, Enum):
    UNREVIEWED = "unreviewed"
    MATCHED = "matched"
    MISSING_DOCUMENT = "missing_document"
    TRANSFER = "transfer"
    IGNORED = "ignored"


MANUAL_RECONCILIATION_STATUSES = {
    ReconciliationStatus.UNREVIEWED.value,
    ReconciliationStatus.MISSING_DOCUMENT.value,
    ReconciliationStatus.TRANSFER.value,
    ReconciliationStatus.IGNORED.value,
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_party_name(value: object) -> str:
    raw = unicodedata.normalize("NFKD", str(value or ""))

    # Apostrophes are normally part of a word rather than a token boundary:
    # "McDonald's" and "McDonalds" should reconcile to the same party.
    raw = raw.replace("'", "").replace("’", "").replace("`", "")

    ascii_value = raw.encode("ascii", "ignore").decode("ascii")
    tokens = re.sub(r"[^a-zA-Z0-9]+", " ", ascii_value.casefold())
    return " ".join(tokens.split())


def _date_value(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value

    text = str(value or "").strip()
    if not text:
        return None

    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _currency(value: object) -> str | None:
    code = str(value or "").strip().upper()
    return code if len(code) == 3 else None


def transaction_direction(transaction: Mapping[str, Any]) -> str | None:
    amount = as_number(transaction.get("amount"), default=0.0)
    if amount > 0:
        # Plaid transaction amounts are positive when money leaves the account.
        return "out"
    if amount < 0:
        return "in"
    return None


def document_direction(document_type: object) -> str | None:
    try:
        intent = default_accounting_intent(str(document_type or "other"))
    except (KeyError, TypeError, ValueError):
        return None

    if intent in {
        AccountingIntent.CREATE_AP_BILL,
        AccountingIntent.RECONCILE_EXPENSE,
        AccountingIntent.CREATE_CUSTOMER_CREDIT,
    }:
        return "out"

    if intent in {
        AccountingIntent.CREATE_AR_INVOICE,
        AccountingIntent.CREATE_SALES_RECEIPT,
        AccountingIntent.CREATE_VENDOR_CREDIT,
    }:
        return "in"

    return None


def document_amount(document: Mapping[str, Any]) -> float:
    # Reuse the dashboard/backend line-item math as the canonical first choice.
    line_items = document.get("line_items") or []
    if isinstance(line_items, list):
        total = 0.0
        for row in line_items:
            if isinstance(row, Mapping):
                total += line_total(row)
        if abs(total) > 0.0001:
            return round(abs(total), 2)

    # Legacy/provider payloads can also carry a top-level total.
    for key in ("total_amount", "total", "grand_total", "amount"):
        value = abs(as_number(document.get(key), default=0.0))
        if value > 0:
            return round(value, 2)

    return 0.0


def is_provider_transfer(transaction: Mapping[str, Any]) -> bool:
    primary = str(transaction.get("category") or "").strip().upper()
    detailed = str(transaction.get("category_detail") or "").strip().upper()

    return (
        primary == "TRANSFER"
        or primary.startswith("TRANSFER_")
        or detailed == "TRANSFER"
        or detailed.startswith("TRANSFER_")
    )


def score_document_candidate(
    transaction: Mapping[str, Any],
    document: Mapping[str, Any],
) -> dict[str, Any] | None:
    transaction_amount = abs(as_number(transaction.get("amount"), default=0.0))
    candidate_amount = abs(as_number(document.get("amount"), default=0.0))

    if transaction_amount <= 0 or candidate_amount <= 0:
        return None

    if abs(transaction_amount - candidate_amount) > 0.01:
        return None

    transaction_currency = _currency(
        transaction.get("iso_currency_code")
        or transaction.get("unofficial_currency_code")
    )
    document_currency = _currency(document.get("currency"))

    if (
        transaction_currency
        and document_currency
        and transaction_currency != document_currency
    ):
        return None

    tx_direction = transaction_direction(transaction)
    doc_direction = str(document.get("direction") or "").strip() or None

    if tx_direction and doc_direction and tx_direction != doc_direction:
        return None

    score = 40
    evidence = ["amount_exact"]

    transaction_party = normalize_party_name(
        transaction.get("merchant_name") or transaction.get("name")
    )
    document_party = normalize_party_name(document.get("vendor_name"))

    if transaction_party and document_party and transaction_party == document_party:
        score += 30
        evidence.append("party_exact")

    transaction_date = _date_value(
        transaction.get("date") or transaction.get("authorized_date")
    )
    document_date = _date_value(document.get("date"))

    if transaction_date and document_date:
        day_delta = abs((transaction_date - document_date).days)

        # When both sides have authoritative dates, do not let amount +
        # merchant alone bridge unrelated statement periods.
        if day_delta > 3:
            return None

        if day_delta == 0:
            # Exact date receives the proximity weight as well.
            score += 30
            evidence.extend(["date_exact", "date_within_3_days"])
        else:
            score += 10
            evidence.append("date_within_3_days")

    if score < 70:
        return None

    return {
        "score": score,
        "confidence": round(min(score, 100) / 100.0, 2),
        "evidence": evidence,
    }


class BankReconciliationService:
    def __init__(
        self,
        user_id: str,
        *,
        repository: BankIntegrationRepository | None = None,
    ) -> None:
        if not user_id:
            raise ValueError("user_id is required")

        self.user_id = user_id
        self.repository = (
            repository
            if repository is not None
            else BankIntegrationRepository(user_id)
        )

    @property
    def groups_ref(self):
        return (
            self.repository.db.collection("users")
            .document(self.user_id)
            .collection("groups")
        )

    @staticmethod
    def _valid_file(data: Mapping[str, Any]) -> bool:
        if bool(data.get("trashed")):
            return False

        if str(data.get("status") or "ok").strip().lower() == "failed":
            return False

        if data.get("error"):
            return False

        return True

    @staticmethod
    def _public_document(
        *,
        group_id: str,
        file_id: str,
        data: Mapping[str, Any],
    ) -> dict[str, Any]:
        document_type = str(data.get("documentType") or "other")
        return {
            "group_id": group_id,
            "file_id": file_id,
            "vendor_name": data.get("vendor_name") or data.get("vendor"),
            "date": data.get("date"),
            "document_type": document_type,
            "amount": document_amount(data),
            "currency": _currency(data.get("currency")),
            "direction": document_direction(document_type),
        }

    def _list_documents(self) -> list[dict[str, Any]]:
        documents: list[dict[str, Any]] = []

        for group_snapshot in self.groups_ref.stream():
            if not group_snapshot.exists:
                continue

            group_data = group_snapshot.to_dict() or {}
            metadata = group_data.get("metadata") or {}

            if isinstance(metadata, Mapping) and bool(metadata.get("trashed")):
                continue

            for file_snapshot in group_snapshot.reference.collection("files").stream():
                if not file_snapshot.exists:
                    continue

                file_data = file_snapshot.to_dict() or {}
                if not self._valid_file(file_data):
                    continue

                documents.append(
                    self._public_document(
                        group_id=group_snapshot.id,
                        file_id=file_snapshot.id,
                        data=file_data,
                    )
                )

        return documents

    def _get_document(
        self,
        group_id: str,
        file_id: str,
    ) -> dict[str, Any] | None:
        group_snapshot = self.groups_ref.document(group_id).get()
        if not group_snapshot.exists:
            return None

        group_data = group_snapshot.to_dict() or {}
        metadata = group_data.get("metadata") or {}

        if isinstance(metadata, Mapping) and bool(metadata.get("trashed")):
            return None

        file_snapshot = group_snapshot.reference.collection("files").document(file_id).get()
        if not file_snapshot.exists:
            return None

        file_data = file_snapshot.to_dict() or {}
        if not self._valid_file(file_data):
            return None

        return self._public_document(
            group_id=group_id,
            file_id=file_id,
            data=file_data,
        )

    @staticmethod
    def _effective_reconciliation(
        transaction: Mapping[str, Any],
    ) -> dict[str, Any]:
        stored = transaction.get("reconciliation")

        if isinstance(stored, Mapping):
            status = str(stored.get("status") or "").strip()

            if status in {value.value for value in ReconciliationStatus}:
                return dict(stored)

        if is_provider_transfer(transaction):
            return {
                "status": ReconciliationStatus.TRANSFER.value,
                "source": "provider",
                "confidence": 1.0,
            }

        return {
            "status": ReconciliationStatus.UNREVIEWED.value,
            "source": "system",
            "confidence": None,
        }

    @staticmethod
    def _account_map(
        items: list[dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}

        for item in items:
            for account in item.get("accounts") or []:
                if not isinstance(account, Mapping):
                    continue

                account_id = str(account.get("account_id") or "").strip()
                if not account_id:
                    continue

                result[account_id] = {
                    "account_id": account_id,
                    "name": account.get("name"),
                    "official_name": account.get("official_name"),
                    "mask": account.get("mask"),
                    "type": account.get("type"),
                    "subtype": account.get("subtype"),
                }

        return result

    @staticmethod
    def _document_indexes(
        documents: list[dict[str, Any]],
    ) -> tuple[
        dict[tuple[str, int], list[dict[str, Any]]],
        dict[tuple[str, str], dict[str, Any]],
    ]:
        by_amount: dict[tuple[str, int], list[dict[str, Any]]] = {}
        by_identity: dict[tuple[str, str], dict[str, Any]] = {}

        for document in documents:
            identity = (
                str(document.get("group_id") or ""),
                str(document.get("file_id") or ""),
            )

            by_identity[identity] = document

            direction = str(document.get("direction") or "")
            amount = as_number(document.get("amount"), default=0.0)

            if not direction or amount <= 0:
                continue

            cents = int(round(abs(amount) * 100))
            by_amount.setdefault((direction, cents), []).append(document)

        return by_amount, by_identity

    @staticmethod
    def _candidate_documents(
        transaction: Mapping[str, Any],
        by_amount: dict[tuple[str, int], list[dict[str, Any]]],
        *,
        excluded_document_ids: set[tuple[str, str]] | None = None,
    ) -> list[dict[str, Any]]:
        direction = transaction_direction(transaction)
        amount = abs(as_number(transaction.get("amount"), default=0.0))

        if not direction or amount <= 0:
            return []

        candidates = []
        excluded = excluded_document_ids or set()

        for document in by_amount.get(
            (direction, int(round(amount * 100))),
            [],
        ):
            identity = (
                str(document.get("group_id") or ""),
                str(document.get("file_id") or ""),
            )

            if identity in excluded:
                continue

            score = score_document_candidate(transaction, document)
            if score is None:
                continue

            candidates.append(
                {
                    **document,
                    **score,
                }
            )

        candidates.sort(
            key=lambda row: (
                float(row.get("confidence") or 0.0),
                str(row.get("date") or ""),
                str(row.get("file_id") or ""),
            ),
            reverse=True,
        )

        return candidates[:5]

    async def reconciliation(
        self,
        *,
        limit: int = 500,
    ) -> dict[str, Any]:
        transactions, documents, items = await asyncio.gather(
            asyncio.to_thread(
                self.repository.list_transactions,
                limit=limit,
            ),
            asyncio.to_thread(self._list_documents),
            asyncio.to_thread(self.repository.list_items),
        )

        by_amount, by_identity = self._document_indexes(documents)
        account_map = self._account_map(items)

        claimed_document_ids: set[tuple[str, str]] = set()

        for transaction in transactions:
            state = self._effective_reconciliation(transaction)

            if state.get("status") != ReconciliationStatus.MATCHED.value:
                continue

            group_id = str(state.get("group_id") or "").strip()
            file_id = str(state.get("file_id") or "").strip()

            if group_id and file_id:
                claimed_document_ids.add((group_id, file_id))

        summary = {
            "total": 0,
            "needs_review": 0,
            "matched": 0,
            "missing_document": 0,
            "transfers": 0,
            "ignored": 0,
            "money_out": 0.0,
            "money_in": 0.0,
        }

        enriched: list[dict[str, Any]] = []

        for transaction in transactions:
            state = self._effective_reconciliation(transaction)
            status = str(state.get("status") or ReconciliationStatus.UNREVIEWED.value)

            amount = as_number(transaction.get("amount"), default=0.0)
            money_out = round(amount, 2) if amount > 0 else 0.0
            money_in = round(abs(amount), 2) if amount < 0 else 0.0

            summary["total"] += 1
            summary["money_out"] += money_out
            summary["money_in"] += money_in

            if status == ReconciliationStatus.MATCHED.value:
                summary["matched"] += 1
            elif status == ReconciliationStatus.MISSING_DOCUMENT.value:
                summary["missing_document"] += 1
            elif status == ReconciliationStatus.TRANSFER.value:
                summary["transfers"] += 1
            elif status == ReconciliationStatus.IGNORED.value:
                summary["ignored"] += 1
            else:
                summary["needs_review"] += 1

            candidates: list[dict[str, Any]] = []
            if (
                status == ReconciliationStatus.UNREVIEWED.value
                and not bool(transaction.get("pending"))
            ):
                candidates = self._candidate_documents(
                    transaction,
                    by_amount,
                    excluded_document_ids=claimed_document_ids,
                )

            matched_document = None
            match_missing = False

            if status == ReconciliationStatus.MATCHED.value:
                group_id = str(state.get("group_id") or "")
                file_id = str(state.get("file_id") or "")

                if group_id and file_id:
                    matched_document = by_identity.get((group_id, file_id))

                match_missing = matched_document is None

            account_id = str(transaction.get("account_id") or "")

            enriched.append(
                {
                    **transaction,
                    "money_out": money_out,
                    "money_in": money_in,
                    "account": account_map.get(account_id),
                    "reconciliation": state,
                    "matched_document": matched_document,
                    "match_missing": match_missing,
                    "candidates": candidates,
                }
            )

        summary["money_out"] = round(summary["money_out"], 2)
        summary["money_in"] = round(summary["money_in"], 2)

        return {
            "summary": summary,
            "transactions": enriched,
            "generated_at": _utc_now_iso(),
        }

    async def match_transaction(
        self,
        *,
        item_id: str,
        transaction_id: str,
        group_id: str,
        file_id: str,
    ) -> dict[str, Any]:
        transaction, document = await asyncio.gather(
            asyncio.to_thread(
                self.repository.get_transaction,
                item_id,
                transaction_id,
            ),
            asyncio.to_thread(
                self._get_document,
                group_id,
                file_id,
            ),
        )

        if not transaction:
            raise KeyError("Bank transaction not found.")

        if not document:
            raise KeyError("Document not found.")

        transaction_amount = abs(
            as_number(transaction.get("amount"), default=0.0)
        )
        candidate_amount = abs(
            as_number(document.get("amount"), default=0.0)
        )

        if (
            transaction_amount <= 0
            or candidate_amount <= 0
            or abs(transaction_amount - candidate_amount) > 0.01
        ):
            raise ValueError(
                "Bank transaction and document totals must match before reconciliation."
            )

        tx_direction = transaction_direction(transaction)
        doc_direction = str(document.get("direction") or "") or None

        if tx_direction and doc_direction and tx_direction != doc_direction:
            raise ValueError(
                "Bank transaction and document accounting directions do not match."
            )

        tx_currency = _currency(
            transaction.get("iso_currency_code")
            or transaction.get("unofficial_currency_code")
        )
        doc_currency = _currency(document.get("currency"))

        if tx_currency and doc_currency and tx_currency != doc_currency:
            raise ValueError(
                "Bank transaction and document currencies do not match."
            )

        scored = score_document_candidate(transaction, document)
        now = _utc_now_iso()

        state = {
            "status": ReconciliationStatus.MATCHED.value,
            "group_id": group_id,
            "file_id": file_id,
            "confidence": (
                scored.get("confidence")
                if scored is not None
                else None
            ),
            "source": "manual",
            "confirmed_at": now,
            "updated_at": now,
        }

        await asyncio.to_thread(
            self.repository.update_transaction_reconciliation,
            item_id,
            transaction_id,
            state,
        )

        return {
            "updated": True,
            "transaction_id": transaction_id,
            "reconciliation": state,
            "matched_document": document,
        }

    async def set_status(
        self,
        *,
        item_id: str,
        transaction_id: str,
        reconciliation_status: str,
    ) -> dict[str, Any]:
        status_value = str(reconciliation_status or "").strip()

        if status_value not in MANUAL_RECONCILIATION_STATUSES:
            raise ValueError(
                "Reconciliation status must be unreviewed, "
                "missing_document, transfer, or ignored."
            )

        transaction = await asyncio.to_thread(
            self.repository.get_transaction,
            item_id,
            transaction_id,
        )

        if not transaction:
            raise KeyError("Bank transaction not found.")

        state = {
            "status": status_value,
            "source": "manual",
            "confidence": None,
            "updated_at": _utc_now_iso(),
        }

        await asyncio.to_thread(
            self.repository.update_transaction_reconciliation,
            item_id,
            transaction_id,
            state,
        )

        return {
            "updated": True,
            "transaction_id": transaction_id,
            "reconciliation": state,
        }

    async def bulk_set_status(
        self,
        *,
        identities: list[Mapping[str, Any]],
        reconciliation_status: str,
    ) -> dict[str, Any]:
        status_value = str(
            reconciliation_status or ""
        ).strip()

        if status_value not in MANUAL_RECONCILIATION_STATUSES:
            raise ValueError(
                "Reconciliation status must be unreviewed, "
                "missing_document, transfer, or ignored."
            )

        requested = list(identities or [])

        if not requested or len(requested) > 100:
            raise ValueError(
                "Bulk reconciliation requires between "
                "1 and 100 transactions."
            )

        results: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        updated = 0
        failed = 0

        for identity in requested:
            item_id = str(
                identity.get("item_id") or ""
            ).strip()
            transaction_id = str(
                identity.get("transaction_id") or ""
            ).strip()

            if not item_id or not transaction_id:
                failed += 1
                results.append(
                    {
                        "item_id": item_id,
                        "transaction_id": transaction_id,
                        "status": "error",
                        "error": "invalid_identity",
                    }
                )
                continue

            key = (item_id, transaction_id)

            if key in seen:
                failed += 1
                results.append(
                    {
                        "item_id": item_id,
                        "transaction_id": transaction_id,
                        "status": "error",
                        "error": "duplicate",
                    }
                )
                continue

            seen.add(key)

            try:
                await self.set_status(
                    item_id=item_id,
                    transaction_id=transaction_id,
                    reconciliation_status=status_value,
                )
            except KeyError:
                failed += 1
                results.append(
                    {
                        "item_id": item_id,
                        "transaction_id": transaction_id,
                        "status": "error",
                        "error": "not_found",
                    }
                )
                continue

            updated += 1
            results.append(
                {
                    "item_id": item_id,
                    "transaction_id": transaction_id,
                    "status": "ok",
                }
            )

        return {
            "requested": len(requested),
            "updated": updated,
            "failed": failed,
            "reconciliation_status": status_value,
            "results": results,
        }

    async def unmatch_transaction(
        self,
        *,
        item_id: str,
        transaction_id: str,
    ) -> dict[str, Any]:
        return await self.set_status(
            item_id=item_id,
            transaction_id=transaction_id,
            reconciliation_status=ReconciliationStatus.UNREVIEWED.value,
        )
