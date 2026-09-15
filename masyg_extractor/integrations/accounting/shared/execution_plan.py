from __future__ import annotations

from typing import Any, Iterable, Mapping

from masyg_extractor.integrations.accounting.shared.batch_preflight import (
    PREFLIGHT_STATES,
)


BLOCKED_EXECUTION_STATES = (
    "sending",
    "succeeded",
    "uncertain",
    "unsupported",
    "unavailable",
)


def _clean(value: Any) -> str:
    return str(value or "").strip()


def build_accounting_execution_plan(
    *,
    provider: str,
    documents: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """
    Convert canonical preflight results into an execution plan.

    This function is intentionally pure:
      - no Firestore access
      - no provider access
      - no mutation
      - no send/create calls

    Only documents currently classified as `ready` may enter
    executable groups. Every other state remains explicitly blocked.
    """

    provider = _clean(provider).lower()

    if not provider:
        raise ValueError(
            "Accounting provider is required."
        )

    rendered_documents = [
        dict(document)
        for document in documents
    ]

    groups_by_intent: dict[
        str,
        list[dict[str, str]],
    ] = {}

    blocked_documents: list[
        dict[str, Any]
    ] = []

    seen_identities: set[
        tuple[str, str]
    ] = set()

    for document in rendered_documents:
        document_provider = _clean(
            document.get("provider")
        ).lower()

        group_id = _clean(
            document.get("group_id")
        )

        file_id = _clean(
            document.get("file_id")
        )

        document_type = _clean(
            document.get("document_type")
        )

        accounting_intent = _clean(
            document.get("accounting_intent")
        )

        state = _clean(
            document.get("state")
        )

        reason = document.get("reason")

        if document_provider != provider:
            raise ValueError(
                "Preflight provider does not match "
                "the execution-plan provider."
            )

        if not group_id or not file_id:
            raise ValueError(
                "Canonical document identity is required."
            )

        identity = (
            group_id,
            file_id,
        )

        if identity in seen_identities:
            raise ValueError(
                "Duplicate canonical document identity."
            )

        seen_identities.add(identity)

        if state not in PREFLIGHT_STATES:
            raise ValueError(
                "Unexpected accounting preflight state."
            )

        if state == "ready":
            if (
                not document_type
                or not accounting_intent
            ):
                raise ValueError(
                    "Ready accounting document is missing "
                    "canonical accounting meaning."
                )

            groups_by_intent.setdefault(
                accounting_intent,
                [],
            ).append(
                {
                    "group_id": group_id,
                    "file_id": file_id,
                    "document_type": document_type,
                    "accounting_intent":
                        accounting_intent,
                }
            )

            continue

        blocked_documents.append(
            {
                "group_id": group_id,
                "file_id": file_id,
                "document_type":
                    document_type or None,
                "accounting_intent":
                    accounting_intent or None,
                "state": state,
                "reason": (
                    str(reason).strip()
                    if reason is not None
                    else None
                ),
            }
        )

    groups = [
        {
            "provider": provider,
            "accounting_intent":
                accounting_intent,
            "count": len(documents_for_intent),
            "documents":
                documents_for_intent,
        }
        for (
            accounting_intent,
            documents_for_intent,
        ) in groups_by_intent.items()
    ]

    executable = sum(
        group["count"]
        for group in groups
    )

    return {
        "provider": provider,
        "selected": len(
            rendered_documents
        ),
        "executable": executable,
        "blocked": len(
            blocked_documents
        ),
        "groups": groups,
        "blocked_documents":
            blocked_documents,
    }
