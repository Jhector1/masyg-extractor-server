from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any


ACCOUNTING_SENDING_RECOVERY_AFTER = timedelta(
    minutes=30,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_utc_timestamp(
    value: Any,
) -> datetime | None:
    rendered = _string_or_none(value)

    if rendered is None:
        return None

    try:
        parsed = datetime.fromisoformat(
            rendered.replace(
                "Z",
                "+00:00",
            )
        )
    except ValueError:
        return None

    if parsed.tzinfo is None:
        return None

    return parsed.astimezone(timezone.utc)


def _sending_recovery_metadata(
    *,
    status: str,
    claimed_at: str | None,
    now: datetime | None,
) -> tuple[bool, str | None]:
    if status != "sending":
        return False, None

    claimed = _parse_utc_timestamp(
        claimed_at
    )

    if claimed is None:
        # A sending record without a trustworthy ownership timestamp
        # must stay create-blocking, but it cannot safely be treated
        # as an actively progressing operation forever.
        return True, "unverifiable_sending"

    current = now or _utc_now()

    if current.tzinfo is None:
        current = current.replace(
            tzinfo=timezone.utc
        )
    else:
        current = current.astimezone(
            timezone.utc
        )

    if claimed > current:
        # A claim timestamp in the future is not a trustworthy
        # active-processing signal. Keep ownership intact and require
        # verification rather than showing Processing indefinitely.
        return True, "unverifiable_sending"

    if (
        current - claimed
        >= ACCOUNTING_SENDING_RECOVERY_AFTER
    ):
        return True, "stale_sending"

    return False, None


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
    now: datetime | None = None,
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
            "recovery_required": False,
            "recovery_reason": None,
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

    claimed_at = _string_or_none(
        record.get("claimedAt")
    )

    (
        recovery_required,
        recovery_reason,
    ) = _sending_recovery_metadata(
        status=status,
        claimed_at=claimed_at,
        now=now,
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
        "claimed_at": claimed_at,
        "uncertain_at": _string_or_none(
            record.get("uncertainAt")
        ),
        "recovery_required": recovery_required,
        "recovery_reason": recovery_reason,
        "legacy": bool(legacy),
    }


def read_accounting_durable_status(
    repo: Any,
    *,
    provider: str,
    intent: str,
    group_id: str,
    file_id: str,
    now: datetime | None = None,
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
            now=now,
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
                now=now,
            )

    return _public_status(
        provider=provider,
        intent=intent,
        group_id=group_id,
        file_id=file_id,
        record_type=lookup.record_type,
        record=None,
        legacy=False,
        now=now,
    )
