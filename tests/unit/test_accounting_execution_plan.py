from pathlib import Path

import pytest

from masyg_extractor.integrations.accounting.shared.execution_plan import (
    build_accounting_execution_plan,
)


def ready(
    file_id: str,
    *,
    provider: str = "xero",
    intent: str = "create_ap_bill",
    document_type: str = "vendor_bill",
):
    return {
        "provider": provider,
        "group_id": "group-1",
        "file_id": file_id,
        "document_type": document_type,
        "accounting_intent": intent,
        "state": "ready",
        "reason": None,
        "durable_status": {
            "status": "none",
        },
    }


def blocked(
    file_id: str,
    state: str,
    *,
    provider: str = "xero",
    intent: str | None = "create_ap_bill",
    document_type: str | None = "vendor_bill",
):
    return {
        "provider": provider,
        "group_id": "group-1",
        "file_id": file_id,
        "document_type": document_type,
        "accounting_intent": intent,
        "state": state,
        "reason": f"{state} reason",
        "durable_status": None,
    }


def test_plan_allows_only_ready_documents():
    documents = [
        ready("ready-1"),
        blocked("sending-1", "sending"),
        blocked("success-1", "succeeded"),
        blocked("uncertain-1", "uncertain"),
        blocked(
            "unsupported-1",
            "unsupported",
            intent="create_estimate",
            document_type="quote_estimate",
        ),
        blocked(
            "missing-1",
            "unavailable",
            intent=None,
            document_type=None,
        ),
    ]

    result = build_accounting_execution_plan(
        provider="xero",
        documents=documents,
    )

    assert result["selected"] == 6
    assert result["executable"] == 1
    assert result["blocked"] == 5

    assert result["groups"] == [
        {
            "provider": "xero",
            "accounting_intent":
                "create_ap_bill",
            "count": 1,
            "documents": [
                {
                    "group_id": "group-1",
                    "file_id": "ready-1",
                    "document_type":
                        "vendor_bill",
                    "accounting_intent":
                        "create_ap_bill",
                }
            ],
        }
    ]

    assert [
        row["state"]
        for row in result[
            "blocked_documents"
        ]
    ] == [
        "sending",
        "succeeded",
        "uncertain",
        "unsupported",
        "unavailable",
    ]


def test_plan_groups_ready_documents_by_canonical_intent():
    documents = [
        ready(
            "invoice-1",
            provider="quickbooks",
            intent="create_ar_invoice",
            document_type="sales_invoice",
        ),
        ready(
            "receipt-1",
            provider="quickbooks",
            intent="create_sales_receipt",
            document_type="sales_receipt",
        ),
        ready(
            "invoice-2",
            provider="quickbooks",
            intent="create_ar_invoice",
            document_type="sales_invoice",
        ),
    ]

    result = build_accounting_execution_plan(
        provider="quickbooks",
        documents=documents,
    )

    assert result["selected"] == 3
    assert result["executable"] == 3
    assert result["blocked"] == 0

    assert [
        (
            group["accounting_intent"],
            group["count"],
            [
                document["file_id"]
                for document
                in group["documents"]
            ],
        )
        for group in result["groups"]
    ] == [
        (
            "create_ar_invoice",
            2,
            [
                "invoice-1",
                "invoice-2",
            ],
        ),
        (
            "create_sales_receipt",
            1,
            [
                "receipt-1",
            ],
        ),
    ]


def test_plan_rejects_duplicate_canonical_identity():
    with pytest.raises(
        ValueError,
        match="Duplicate canonical",
    ):
        build_accounting_execution_plan(
            provider="xero",
            documents=[
                ready("same"),
                ready("same"),
            ],
        )


def test_plan_rejects_provider_mismatch():
    with pytest.raises(
        ValueError,
        match="provider",
    ):
        build_accounting_execution_plan(
            provider="xero",
            documents=[
                ready(
                    "file-1",
                    provider="quickbooks",
                )
            ],
        )


def test_plan_rejects_unknown_state():
    document = ready("file-1")
    document["state"] = "invented"

    with pytest.raises(
        ValueError,
        match="Unexpected accounting preflight state",
    ):
        build_accounting_execution_plan(
            provider="xero",
            documents=[document],
        )


def test_ready_document_requires_canonical_meaning():
    document = ready("file-1")
    document["accounting_intent"] = None

    with pytest.raises(
        ValueError,
        match="canonical accounting meaning",
    ):
        build_accounting_execution_plan(
            provider="xero",
            documents=[document],
        )


def test_execution_plan_route_reuses_current_preflight_and_is_read_only():
    source = Path(
        "masyg_extractor/integrations/accounting/shared/status_router.py"
    ).read_text()

    owner = Path(
        "masyg_extractor/integrations/accounting/shared/execution_plan.py"
    ).read_text()

    assert (
        '@router.post("/execution-plan")'
        in source
    )

    assert (
        "post_accounting_batch_preflight("
        in source
    )

    assert (
        "build_accounting_execution_plan("
        in source
    )

    forbidden = (
        "QuickBooksClientAdapter",
        "XeroClientAdapter",
        "send-invoice-in-bulk",
        "send-receipt-in-bulk",
        "send-salereceipt-in-bulk",
        "claim_record",
        "finalize_record",
        "mark_record_uncertain",
        "release_record_claim",
    )

    for token in forbidden:
        assert token not in owner
