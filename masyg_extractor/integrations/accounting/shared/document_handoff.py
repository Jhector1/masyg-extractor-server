from __future__ import annotations

from typing import Any, Mapping

from masyg_extractor.documents.document_types import (
    DocumentType,
    canonical_document_type,
)
from masyg_extractor.integrations.accounting.core.document_intent import (
    AccountingIntent,
    default_accounting_intent,
)


def resolve_accounting_document_handoff(
    document: Mapping[str, Any],
    *,
    group_id: str,
    file_id: str,
) -> dict[str, str]:
    group_id = str(group_id or "").strip()
    file_id = str(file_id or "").strip()

    if not group_id or not file_id:
        raise ValueError(
            "group_id and file_id are required."
        )

    if bool(document.get("trashed")):
        raise ValueError(
            "Trashed documents cannot be sent to accounting."
        )

    status = str(
        document.get("status") or "ok"
    ).strip().lower()

    if status == "failed" or document.get("error"):
        raise ValueError(
            "Failed documents cannot be sent to accounting."
        )

    raw_document_type = str(
        document.get("documentType") or "other"
    )

    try:
        document_type = canonical_document_type(
            raw_document_type
        )
    except (KeyError, TypeError, ValueError):
        document_type = DocumentType.OTHER

    try:
        accounting_intent = default_accounting_intent(
            document_type
        )
    except (KeyError, TypeError, ValueError):
        accounting_intent = (
            AccountingIntent.REVIEW_REQUIRED
        )

    return {
        "group_id": group_id,
        "file_id": file_id,
        "document_type": document_type.value,
        "accounting_intent": accounting_intent.value,
    }
