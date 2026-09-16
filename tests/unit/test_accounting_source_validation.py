from __future__ import annotations

from pathlib import Path

import pytest

from masyg_extractor.integrations.accounting.shared.batch_preflight import (
    preflight_accounting_document,
)
from masyg_extractor.integrations.accounting.shared.source_validation import (
    require_accounting_customer_name,
    validate_accounting_source_document,
)


class FakeRepo:
    def __init__(self):
        self.calls = []

    def get_record(
        self,
        record_type,
        group_id,
        transaction_id,
    ):
        self.calls.append(
            (
                record_type,
                group_id,
                transaction_id,
            )
        )
        return None


def quickbooks_ar_handoff():
    return {
        "group_id": "group-1",
        "file_id": "file-1",
        "document_type": "sales_invoice",
        "accounting_intent": "create_ar_invoice",
    }


@pytest.mark.parametrize(
    "source",
    (
        {},
        {"customer_name": None},
        {"customer_name": ""},
        {"customer_name": "   "},
    ),
)
def test_required_customer_name_rejects_missing_or_blank(
    source,
):
    with pytest.raises(
        ValueError,
        match=(
            "Customer name is required "
            "for accounting execution"
        ),
    ):
        require_accounting_customer_name(
            source
        )


def test_required_customer_name_returns_trimmed_value():
    assert (
        require_accounting_customer_name(
            {
                "customer_name":
                    "  Sandbox Customer  ",
            }
        )
        == "Sandbox Customer"
    )


@pytest.mark.parametrize(
    "intent",
    (
        "create_ar_invoice",
        "create_ap_bill",
        "create_sales_receipt",
    ),
)
def test_current_document_creation_intents_require_customer_name(
    intent,
):
    with pytest.raises(
        ValueError,
        match="Customer name is required",
    ):
        validate_accounting_source_document(
            {},
            accounting_intent=intent,
        )


def test_preflight_blocks_missing_customer_name_before_execution_plan():
    repo = FakeRepo()

    result = preflight_accounting_document(
        repo,
        provider="quickbooks",
        handoff=quickbooks_ar_handoff(),
        source_document={
            "documentType":
                "sales_invoice",
            "line_items": [],
        },
    )

    assert result["state"] == "unavailable"

    assert (
        result["reason"]
        == (
            "Customer name is required "
            "for accounting execution."
        )
    )

    assert (
        result["durable_status"]["status"]
        == "none"
    )

    assert repo.calls == [
        (
            "invoices",
            "group-1",
            "file-1",
        )
    ]


def test_preflight_keeps_valid_source_ready():
    repo = FakeRepo()

    result = preflight_accounting_document(
        repo,
        provider="quickbooks",
        handoff=quickbooks_ar_handoff(),
        source_document={
            "documentType":
                "sales_invoice",
            "customer_name":
                "Sandbox Customer",
            "line_items": [],
        },
    )

    assert result["state"] == "ready"
    assert result["reason"] is None


def test_unsupported_capability_remains_unsupported_before_source_validation():
    repo = FakeRepo()

    result = preflight_accounting_document(
        repo,
        provider="quickbooks",
        handoff={
            "group_id":
                "group-1",
            "file_id":
                "file-1",
            "document_type":
                "vendor_bill",
            "accounting_intent":
                "create_ap_bill",
        },
        source_document={},
    )

    assert result["state"] == "unsupported"
    assert result["durable_status"] is None
    assert repo.calls == []


def test_preflight_route_passes_real_source_into_shared_owner():
    source = Path(
        "masyg_extractor/integrations/accounting/"
        "shared/status_router.py"
    ).read_text()

    assert (
        "source_document=source_document"
        in source
    )

    forbidden = (
        "QuickBooksClientAdapter",
        "XeroClientAdapter",
    )

    for token in forbidden:
        assert token not in source


def test_provider_factories_use_shared_required_name_guard():
    quickbooks = Path(
        "masyg_extractor/integrations/accounting/"
        "quickbooks/route_helper.py"
    ).read_text()

    xero = Path(
        "masyg_extractor/integrations/accounting/"
        "xero/route_helper.py"
    ).read_text()

    for source in (
        quickbooks,
        xero,
    ):
        assert (
            "require_accounting_customer_name"
            in source
        )

        assert (
            'remove_non_alphanumeric(details.get("customer_name"))'
            not in source
        )
