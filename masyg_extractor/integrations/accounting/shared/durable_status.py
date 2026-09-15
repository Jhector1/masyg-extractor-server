from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AccountingRecordLookup:
    record_type: str
    legacy_record_types: tuple[str, ...] = ()


_ACCOUNTING_RECORD_LOOKUPS: dict[
    tuple[str, str],
    AccountingRecordLookup,
] = {
    (
        "quickbooks",
        "create_ar_invoice",
    ): AccountingRecordLookup(
        record_type="invoices",
    ),
    (
        "quickbooks",
        "create_sales_receipt",
    ): AccountingRecordLookup(
        record_type="salesreceipts",
    ),
    (
        "xero",
        "create_ar_invoice",
    ): AccountingRecordLookup(
        record_type="invoices",
        legacy_record_types=("invoicess",),
    ),
    (
        "xero",
        "create_ap_bill",
    ): AccountingRecordLookup(
        record_type="bills",
        legacy_record_types=("invoicess",),
    ),
}


def resolve_accounting_record_lookup(
    provider: str,
    intent: str,
) -> AccountingRecordLookup:
    key = (
        str(provider or "").strip().lower(),
        str(intent or "").strip(),
    )

    lookup = _ACCOUNTING_RECORD_LOOKUPS.get(key)

    if lookup is None:
        raise ValueError(
            "Unsupported accounting provider/intent combination."
        )

    return lookup


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None

    rendered = str(value).strip()
    return rendered or None


def _public_status(
    *,
    provider: str,
    intent: str,
    group_id: str,
    file_id: str,
    record_type: str,
    record: dict[str, Any] | None,
    legacy: bool,
) -> dict[str, Any]:
    if record is None:
        return {
            "provider": provider,
            "intent": intent,
            "group_id": group_id,
            "file_id": file_id,
            "record_type": record_type,
            "status": "none",
            "provider_document_id": None,
            "provider_document_number": None,
            "completed_at": None,
            "claimed_at": None,
            "uncertain_at": None,
            "legacy": False,
        }

    raw_status = _string_or_none(
        record.get("status")
    )

    if raw_status in {
        "sending",
        "succeeded",
        "uncertain",
    }:
        status = raw_status
    else:
        # Before V4.15.9, accounting records were persisted only
        # after provider creation completed successfully.
        status = "succeeded"
        legacy = True

    provider_document_number = (
        record.get("providerDocumentNumber")
        or record.get("docNumber")
    )

    return {
        "provider": provider,
        "intent": intent,
        "group_id": group_id,
        "file_id": file_id,
        "record_type": record_type,
        "status": status,
        "provider_document_id": _string_or_none(
            record.get("providerDocumentId")
        ),
        "provider_document_number": _string_or_none(
            provider_document_number
        ),
        "completed_at": _string_or_none(
            record.get("completedAt")
        ),
        "claimed_at": _string_or_none(
            record.get("claimedAt")
        ),
        "uncertain_at": _string_or_none(
            record.get("uncertainAt")
        ),
        "legacy": bool(legacy),
    }


def read_accounting_durable_status(
    repo: Any,
    *,
    provider: str,
    intent: str,
    group_id: str,
    file_id: str,
) -> dict[str, Any]:
    provider = str(provider or "").strip().lower()
    intent = str(intent or "").strip()
    group_id = str(group_id or "").strip()
    file_id = str(file_id or "").strip()

    if not group_id or not file_id:
        raise ValueError(
            "group_id and file_id are required."
        )

    lookup = resolve_accounting_record_lookup(
        provider,
        intent,
    )

    record = repo.get_record(
        lookup.record_type,
        group_id,
        file_id,
    )

    if record is not None:
        return _public_status(
            provider=provider,
            intent=intent,
            group_id=group_id,
            file_id=file_id,
            record_type=lookup.record_type,
            record=record,
            legacy=False,
        )

    # Historical Xero accounting normalization appended "-0"
    # to a document's persisted transaction ID. Keep the public API
    # canonical: callers always provide the real persisted file_id.
    # The alias exists only for reading pre-canonical legacy records.
    legacy_transaction_ids = tuple(
        dict.fromkeys(
            (
                file_id,
                f"{file_id}-0",
            )
        )
    )

    for legacy_record_type in (
        lookup.legacy_record_types
    ):
        for legacy_transaction_id in (
            legacy_transaction_ids
        ):
            legacy_record = repo.get_record(
                legacy_record_type,
                group_id,
                legacy_transaction_id,
            )

            if legacy_record is None:
                continue

            return _public_status(
                provider=provider,
                intent=intent,
                group_id=group_id,
                file_id=file_id,
                record_type=legacy_record_type,
                record=legacy_record,
                legacy=True,
            )

    return _public_status(
        provider=provider,
        intent=intent,
        group_id=group_id,
        file_id=file_id,
        record_type=lookup.record_type,
        record=None,
        legacy=False,
    )
