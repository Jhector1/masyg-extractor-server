from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable


BulkSender = Callable[
    [list[Any], float],
    Awaitable[dict[str, Any]],
]


async def run_single_via_bulk(
    document: Any,
    share_progress: float,
    *,
    send_bulk: BulkSender,
    repo: Any,
    record_type: str,
) -> dict[str, Any] | str:
    """
    Preserve the legacy single-send HTTP surface while delegating provider
    creation to the canonical atomic bulk owner.

    Successful modern records return their provider document ID, matching
    the historical single-send success contract. Failed bulk results are
    reduced to the historical {"error": "..."} shape.
    """
    result = await send_bulk(
        [document],
        share_progress,
    )

    group_id = str(
        getattr(document, "group_id", "") or ""
    ).strip()
    transaction_id = str(
        getattr(document, "transaction_id", "") or ""
    ).strip()

    if group_id and transaction_id:
        record = await asyncio.to_thread(
            repo.get_record,
            record_type,
            group_id,
            transaction_id,
        )

        if (
            isinstance(record, dict)
            and record.get("status") == "succeeded"
        ):
            provider_document_id = record.get(
                "providerDocumentId"
            )

            if provider_document_id not in (
                None,
                "",
            ):
                return str(provider_document_id)

    if isinstance(result, dict):
        results = result.get("results")

        if (
            isinstance(results, list)
            and results
            and isinstance(results[0], dict)
        ):
            first = results[0]

            if first.get("status") == "failed":
                error = str(
                    first.get("error")
                    or first.get("message")
                    or "Accounting operation failed."
                ).strip()

                return {
                    "error": (
                        error
                        or "Accounting operation failed."
                    )
                }

    return result
