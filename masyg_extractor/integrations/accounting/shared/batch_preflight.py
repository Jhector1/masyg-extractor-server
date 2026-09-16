from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable, Mapping

from masyg_extractor.integrations.accounting.registry import (
    get_accounting_execution_action,
)

from masyg_extractor.integrations.accounting.shared.durable_status import (
    read_accounting_durable_status,
)

from masyg_extractor.integrations.accounting.shared.source_validation import (
    validate_accounting_source_document,
)


PREFLIGHT_STATES = (
    "ready",
    "sending",
    "succeeded",
    "uncertain",
    "unsupported",
    "unavailable",
)


def unavailable_accounting_preflight_result(
    *,
    provider: str,
    group_id: str,
    file_id: str,
    reason: str,
) -> dict[str, Any]:
    return {
        "provider": str(provider or "").strip().lower(),
        "group_id": str(group_id or "").strip(),
        "file_id": str(file_id or "").strip(),
        "document_type": None,
        "accounting_intent": None,
        "state": "unavailable",
        "reason": str(reason or "Document unavailable."),
        "durable_status": None,
    }


def preflight_accounting_document(
    repo: Any,
    *,
    provider: str,
    handoff: Mapping[str, Any],
    source_document: Mapping[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    provider = str(provider or "").strip().lower()

    group_id = str(
        handoff.get("group_id") or ""
    ).strip()

    file_id = str(
        handoff.get("file_id") or ""
    ).strip()

    document_type = str(
        handoff.get("document_type") or ""
    ).strip()

    accounting_intent = str(
        handoff.get("accounting_intent") or ""
    ).strip()

    if (
        not group_id
        or not file_id
        or not document_type
        or not accounting_intent
    ):
        raise ValueError(
            "Canonical accounting handoff is incomplete."
        )

    try:
        get_accounting_execution_action(
            provider,
            accounting_intent,
        )
    except KeyError:
        # The canonical backend execution registry owns whether a
        # provider may execute this accounting intent. Never infer or
        # guess an action from the document type.
        return {
            "provider": provider,
            "group_id": group_id,
            "file_id": file_id,
            "document_type": document_type,
            "accounting_intent": accounting_intent,
            "state": "unsupported",
            "reason": (
                "This accounting provider does not support "
                "the document action."
            ),
            "durable_status": None,
        }

    try:
        durable_status = (
            read_accounting_durable_status(
                repo,
                provider=provider,
                intent=accounting_intent,
                group_id=group_id,
                file_id=file_id,
                now=now,
            )
        )
    except ValueError as exc:
        # A registered execution action without a corresponding
        # durable-status owner is a backend configuration defect.
        # Fail closed rather than misclassifying it as unsupported.
        raise ValueError(
            "Accounting execution capability and durable "
            "status configuration are inconsistent."
        ) from exc

    raw_status = str(
        durable_status.get("status") or ""
    ).strip()

    recovery_required = (
        durable_status.get(
            "recovery_required"
        )
        is True
    )

    state = (
        "uncertain"
        if (
            raw_status == "sending"
            and recovery_required
        )
        else (
            "ready"
            if raw_status == "none"
            else raw_status
        )
    )

    if state not in {
        "ready",
        "sending",
        "succeeded",
        "uncertain",
    }:
        raise ValueError(
            "Unexpected durable accounting status."
        )

    reason = (
        "The accounting operation needs verification "
        "before another provider action is allowed."
        if recovery_required
        else None
    )

    # Durable status alone is not enough to declare a source document
    # executable. Validate known provider-materialization requirements
    # before exposing `ready` to an execution plan.
    if (
        state == "ready"
        and source_document is not None
    ):
        try:
            validate_accounting_source_document(
                source_document,
                accounting_intent=accounting_intent,
            )
        except (TypeError, ValueError) as exc:
            state = "unavailable"
            reason = str(exc)

    return {
        "provider": provider,
        "group_id": group_id,
        "file_id": file_id,
        "document_type": document_type,
        "accounting_intent": accounting_intent,
        "state": state,
        "reason": reason,
        "durable_status": durable_status,
    }


def summarize_accounting_preflight(
    *,
    provider: str,
    documents: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    rendered_documents = [
        dict(document)
        for document in documents
    ]

    counts = {
        state: 0
        for state in PREFLIGHT_STATES
    }

    for document in rendered_documents:
        state = str(
            document.get("state") or ""
        ).strip()

        if state in counts:
            counts[state] += 1

    return {
        "provider": str(provider or "").strip().lower(),
        "documents": rendered_documents,
        "summary": {
            "selected": len(rendered_documents),
            **counts,
        },
    }
