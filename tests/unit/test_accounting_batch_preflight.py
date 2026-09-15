from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from masyg_extractor.integrations.accounting.shared.batch_preflight import (
    preflight_accounting_document,
    summarize_accounting_preflight,
    unavailable_accounting_preflight_result,
)


class FakeRepo:
    def __init__(self, records=None):
        self.records = records or {}
        self.calls = []

    def get_record(
        self,
        record_type,
        group_id,
        transaction_id,
    ):
        key = (
            record_type,
            group_id,
            transaction_id,
        )
        self.calls.append(key)
        return self.records.get(key)


def handoff(
    *,
    intent="create_ap_bill",
    document_type="vendor_bill",
    group_id="group-1",
    file_id="file-1",
):
    return {
        "group_id": group_id,
        "file_id": file_id,
        "document_type": document_type,
        "accounting_intent": intent,
    }


def test_xero_ap_bill_without_record_is_ready():
    repo = FakeRepo()

    result = preflight_accounting_document(
        repo,
        provider="xero",
        handoff=handoff(),
    )

    assert result["state"] == "ready"
    assert result["accounting_intent"] == "create_ap_bill"
    assert result["durable_status"]["status"] == "none"

    assert repo.calls[0] == (
        "bills",
        "group-1",
        "file-1",
    )


def test_stale_sending_maps_to_needs_verification_bucket():
    repo = FakeRepo(
        {
            (
                "bills",
                "group-1",
                "file-1",
            ): {
                "status": "sending",
                "claimedAt": "2026-09-15T01:45:00Z",
            },
        }
    )

    result = preflight_accounting_document(
        repo,
        provider="xero",
        handoff=handoff(),
        now=datetime(
            2026,
            9,
            15,
            2,
            15,
            0,
            tzinfo=timezone.utc,
        ),
    )

    assert result["state"] == "uncertain"
    assert (
        result["durable_status"]["status"]
        == "sending"
    )
    assert (
        result["durable_status"][
            "recovery_required"
        ]
        is True
    )
    assert (
        result["durable_status"][
            "recovery_reason"
        ]
        == "stale_sending"
    )

    # Never turn a stale sending claim into ready.
    assert result["state"] != "ready"


def test_recent_sending_remains_processing():
    repo = FakeRepo(
        {
            (
                "bills",
                "group-1",
                "file-1",
            ): {
                "status": "sending",
                "claimedAt": "2026-09-15T01:45:01Z",
            },
        }
    )

    result = preflight_accounting_document(
        repo,
        provider="xero",
        handoff=handoff(),
        now=datetime(
            2026,
            9,
            15,
            2,
            15,
            0,
            tzinfo=timezone.utc,
        ),
    )

    assert result["state"] == "sending"
    assert (
        result["durable_status"][
            "recovery_required"
        ]
        is False
    )


def test_historical_xero_bill_is_succeeded_not_ready():
    repo = FakeRepo(
        {
            (
                "invoicess",
                "group-1",
                "file-1-0",
            ): {
                "docNumber": "OLD-101",
            },
        }
    )

    result = preflight_accounting_document(
        repo,
        provider="xero",
        handoff=handoff(),
    )

    assert result["state"] == "succeeded"
    assert result["durable_status"]["legacy"] is True
    assert (
        result["durable_status"][
            "provider_document_number"
        ]
        == "OLD-101"
    )


def test_quickbooks_ap_bill_is_unsupported_without_repo_lookup():
    repo = FakeRepo()

    result = preflight_accounting_document(
        repo,
        provider="quickbooks",
        handoff=handoff(),
    )

    assert result["state"] == "unsupported"
    assert result["durable_status"] is None
    assert repo.calls == []


def test_quickbooks_ar_invoice_can_be_ready():
    repo = FakeRepo()

    result = preflight_accounting_document(
        repo,
        provider="quickbooks",
        handoff=handoff(
            intent="create_ar_invoice",
            document_type="sales_invoice",
        ),
    )

    assert result["state"] == "ready"
    assert result["durable_status"]["status"] == "none"


def test_unavailable_document_preserves_canonical_identity():
    result = (
        unavailable_accounting_preflight_result(
            provider="xero",
            group_id="group-7",
            file_id="file-9",
            reason="Document not found.",
        )
    )

    assert result == {
        "provider": "xero",
        "group_id": "group-7",
        "file_id": "file-9",
        "document_type": None,
        "accounting_intent": None,
        "state": "unavailable",
        "reason": "Document not found.",
        "durable_status": None,
    }


def test_summary_counts_mixed_batch_without_hiding_documents():
    documents = [
        {
            "state": "ready",
            "file_id": "1",
        },
        {
            "state": "ready",
            "file_id": "2",
        },
        {
            "state": "succeeded",
            "file_id": "3",
        },
        {
            "state": "unsupported",
            "file_id": "4",
        },
        {
            "state": "uncertain",
            "file_id": "5",
        },
    ]

    result = summarize_accounting_preflight(
        provider="xero",
        documents=documents,
    )

    assert result["summary"] == {
        "selected": 5,
        "ready": 2,
        "sending": 0,
        "succeeded": 1,
        "uncertain": 1,
        "unsupported": 1,
        "unavailable": 0,
    }

    assert len(result["documents"]) == 5


def test_preflight_router_is_read_only_and_reuses_shared_owners():
    source = Path(
        "masyg_extractor/integrations/accounting/shared/status_router.py"
    ).read_text()

    assert '@router.post("/preflight")' in source

    assert (
        "resolve_accounting_document_handoff"
        in source
    )

    assert (
        "preflight_accounting_document"
        in source
    )

    assert (
        '.collection("groups")'
        in source
    )

    assert (
        '.collection("files")'
        in source
    )

    forbidden = (
        "QuickBooksClientAdapter",
        "XeroClientAdapter",
        "send-invoice-in-bulk",
        "send-receipt-in-bulk",
        "send-salereceipt-in-bulk",
    )

    for token in forbidden:
        assert token not in source
