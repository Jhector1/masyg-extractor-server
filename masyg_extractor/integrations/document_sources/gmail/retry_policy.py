from __future__ import annotations

from collections.abc import Mapping
from typing import Any


TERMINAL_INGESTION_ERRORS = frozenset(
    {
        "unsupported document type",
        "unsupported file type",
        "empty file",
    }
)


def primary_ingestion_failure(
    result: Mapping[str, Any] | None,
) -> tuple[str, str]:
    payload = dict(result or {})
    failures = payload.get("failures")

    if isinstance(failures, list) and failures:
        first = failures[0]
        if isinstance(first, Mapping):
            error = str(first.get("error") or "").strip()
            stage = str(first.get("stage") or "").strip()
            if error:
                return error, stage

    return (
        str(payload.get("error") or "Canonical document ingestion failed").strip(),
        str(payload.get("stage") or "").strip(),
    )


def classify_ingestion_failure(
    result: Mapping[str, Any] | None,
) -> tuple[str, str, str]:
    error, stage = primary_ingestion_failure(result)
    normalized_error = error.casefold()

    if normalized_error in TERMINAL_INGESTION_ERRORS:
        return "terminal", error, stage

    if (
        stage.casefold() == "document classification"
        and normalized_error == "unsupported document type"
    ):
        return "terminal", error, stage

    return "retryable", error, stage


def retry_delay_seconds(
    attempt_count: int,
    *,
    base_seconds: int = 300,
    max_seconds: int = 3600,
) -> int:
    attempt = max(1, int(attempt_count or 1))
    delay = int(base_seconds) * (2 ** (attempt - 1))
    return min(max(1, delay), max(1, int(max_seconds)))
