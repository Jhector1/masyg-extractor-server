from pathlib import Path

from masyg_extractor.integrations.accounting.shared.execution_result import (
    summarize_accounting_provider_result,
)


def identities(*file_ids: str):
    return [
        {
            "group_id": "group-1",
            "file_id": file_id,
        }
        for file_id in file_ids
    ]


def envelope(*statuses: tuple[str, str]):
    succeeded = sum(
        1
        for _, status in statuses
        if status == "succeeded"
    )
    failed = sum(
        1
        for _, status in statuses
        if status == "failed"
    )

    return {
        "operation_id": "operation-1",
        "provider": "quickbooks",
        "action": "create-invoice",
        "results": [
            {
                "document_id": document_id,
                "filename": document_id,
                "status": status,
                "progress": 100.0,
                "message": (
                    "Created successfully"
                    if status == "succeeded"
                    else ""
                ),
                "error": (
                    ""
                    if status == "succeeded"
                    else "Provider rejected document."
                ),
            }
            for document_id, status in statuses
        ],
        "overall_progress": 100.0,
        "summary": {
            "total": len(statuses),
            "completed": len(statuses),
            "succeeded": succeeded,
            "failed": failed,
        },
    }


def test_all_succeeded():
    result = summarize_accounting_provider_result(
        envelope(
            ("file-1", "succeeded"),
            ("file-2", "succeeded"),
        ),
        identities("file-1", "file-2"),
    )

    assert result == {
        "total": 2,
        "completed": 2,
        "succeeded": 2,
        "failed": 0,
    }


def test_all_failed():
    result = summarize_accounting_provider_result(
        envelope(
            ("file-1", "failed"),
            ("file-2", "failed"),
        ),
        identities("file-1", "file-2"),
    )

    assert result == {
        "total": 2,
        "completed": 2,
        "succeeded": 0,
        "failed": 2,
    }


def test_partial_success_failure():
    result = summarize_accounting_provider_result(
        envelope(
            ("file-1", "succeeded"),
            ("file-2", "failed"),
            ("file-3", "succeeded"),
        ),
        identities(
            "file-1",
            "file-2",
            "file-3",
        ),
    )

    assert result == {
        "total": 3,
        "completed": 3,
        "succeeded": 2,
        "failed": 1,
    }


def test_missing_document_fails_closed():
    result = summarize_accounting_provider_result(
        envelope(
            ("file-1", "succeeded"),
        ),
        identities(
            "file-1",
            "file-2",
        ),
    )

    assert result is None


def test_duplicate_result_fails_closed():
    raw = envelope(
        ("file-1", "succeeded"),
        ("file-2", "failed"),
    )

    raw["results"][1]["document_id"] = "file-1"

    assert (
        summarize_accounting_provider_result(
            raw,
            identities(
                "file-1",
                "file-2",
            ),
        )
        is None
    )


def test_nonterminal_status_fails_closed():
    raw = envelope(
        ("file-1", "succeeded"),
    )

    raw["results"][0]["status"] = "running"

    assert (
        summarize_accounting_provider_result(
            raw,
            identities("file-1"),
        )
        is None
    )


def test_inconsistent_summary_fails_closed():
    raw = envelope(
        ("file-1", "failed"),
    )

    raw["summary"]["failed"] = 0
    raw["summary"]["succeeded"] = 1

    assert (
        summarize_accounting_provider_result(
            raw,
            identities("file-1"),
        )
        is None
    )


def test_legacy_unknown_shape_does_not_invent_success():
    assert (
        summarize_accounting_provider_result(
            {"completed": 1},
            identities("file-1"),
        )
        is None
    )


def test_execute_router_uses_verified_provider_outcome():
    source = Path(
        "masyg_extractor/integrations/accounting/shared/"
        "status_router.py"
    ).read_text()

    assert (
        "summarize_accounting_provider_result("
        in source
    )

    assert (
        'execution_entry["outcome"]'
        in source
    )

    assert (
        '"status": "completed"'
        in source
    )
