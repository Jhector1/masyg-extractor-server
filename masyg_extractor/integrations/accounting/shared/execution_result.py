from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


def _count(value: Any) -> int | None:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 0
    ):
        return None

    return value


def summarize_accounting_provider_result(
    result: Any,
    dispatched_identities: Iterable[Mapping[str, Any]],
) -> dict[str, int] | None:
    """
    Reduce the canonical provider operation envelope to trustworthy
    terminal document counts.

    The provider services already own success/failure determination.
    This helper only verifies that their normalized result:
      - covers every dispatched file exactly once
      - contains terminal succeeded/failed states only
      - has a summary consistent with those per-document states

    Unknown or malformed provider result shapes fail closed by
    returning None. No provider or persistence access occurs here.
    """

    if not isinstance(result, Mapping):
        return None

    raw_results = result.get("results")
    raw_summary = result.get("summary")

    if (
        not isinstance(raw_results, list)
        or not isinstance(raw_summary, Mapping)
    ):
        return None

    expected: set[str] = set()

    for identity in dispatched_identities:
        if not isinstance(identity, Mapping):
            return None

        file_id = str(
            identity.get("file_id") or ""
        ).strip()

        if not file_id or file_id in expected:
            return None

        expected.add(file_id)

    if not expected:
        return None

    seen: set[str] = set()
    succeeded = 0
    failed = 0

    for raw_result in raw_results:
        if not isinstance(raw_result, Mapping):
            return None

        document_id = str(
            raw_result.get("document_id") or ""
        ).strip()

        status = str(
            raw_result.get("status") or ""
        ).strip().lower()

        if (
            not document_id
            or document_id not in expected
            or document_id in seen
            or status not in {"succeeded", "failed"}
        ):
            return None

        seen.add(document_id)

        if status == "succeeded":
            succeeded += 1
        else:
            failed += 1

    if seen != expected:
        return None

    total = _count(raw_summary.get("total"))
    completed = _count(
        raw_summary.get("completed")
    )
    summary_succeeded = _count(
        raw_summary.get("succeeded")
    )
    summary_failed = _count(
        raw_summary.get("failed")
    )

    expected_total = len(expected)

    if (
        total is None
        or completed is None
        or summary_succeeded is None
        or summary_failed is None
        or total != expected_total
        or completed != expected_total
        or summary_succeeded != succeeded
        or summary_failed != failed
        or succeeded + failed != expected_total
    ):
        return None

    return {
        "total": expected_total,
        "completed": expected_total,
        "succeeded": succeeded,
        "failed": failed,
    }
