from __future__ import annotations

from pathlib import Path

import pytest

from masyg_extractor.integrations.accounting.shared.reconciliation import (
    build_provider_lookup_request,
    classify_provider_lookup_response,
    reconciliation_decision,
    reconciliation_document_number,
    resolve_provider_reconciliation_spec,
)


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    (
        "provider",
        "intent",
        "resource",
        "number_field",
        "type_field",
        "type_value",
    ),
    (
        (
            "quickbooks",
            "create_ar_invoice",
            "Invoice",
            "DocNumber",
            None,
            None,
        ),
        (
            "quickbooks",
            "create_sales_receipt",
            "SalesReceipt",
            "DocNumber",
            None,
            None,
        ),
        (
            "xero",
            "create_ar_invoice",
            "Invoices",
            "InvoiceNumber",
            "Type",
            "ACCREC",
        ),
        (
            "xero",
            "create_ap_bill",
            "Invoices",
            "InvoiceNumber",
            "Type",
            "ACCPAY",
        ),
    ),
)
def test_provider_specific_reconciliation_identity(
    provider,
    intent,
    resource,
    number_field,
    type_field,
    type_value,
):
    spec = resolve_provider_reconciliation_spec(
        provider,
        intent,
    )

    assert spec.provider_resource == resource
    assert spec.provider_number_field == number_field
    assert spec.provider_type_field == type_field
    assert spec.provider_type_value == type_value

    # A4 does not permit a negative lookup to silently reopen
    # accounting creation.
    assert spec.absence_can_unlock is False


def test_unsupported_provider_intent_is_rejected():
    with pytest.raises(
        ValueError,
        match="Unsupported accounting provider/intent",
    ):
        resolve_provider_reconciliation_spec(
            "quickbooks",
            "create_ap_bill",
        )


def test_found_is_the_only_outcome_that_can_finalize():
    result = reconciliation_decision("found")

    assert result.finalize_succeeded is True
    assert result.release_claim is False
    assert result.allow_create is False
    assert result.display_state == "already_created"


@pytest.mark.parametrize(
    "outcome",
    (
        "absent",
        "indeterminate",
    ),
)
def test_non_found_outcomes_remain_blocked(outcome):
    result = reconciliation_decision(outcome)

    assert result.finalize_succeeded is False
    assert result.release_claim is False
    assert result.allow_create is False
    assert result.display_state == "needs_verification"


def test_unknown_outcome_fails_closed():
    with pytest.raises(
        ValueError,
        match="Unsupported reconciliation outcome",
    ):
        reconciliation_decision("retry")  # type: ignore[arg-type]


def test_current_stale_claim_without_correlation_number_is_unverifiable():
    assert (
        reconciliation_document_number(
            {
                "status": "sending",
                "claimedAt": "2026-09-15T01:00:00Z",
                "action": "create_ar_invoice",
            }
        )
        is None
    )


def test_future_sending_claim_can_use_durable_provider_number():
    assert (
        reconciliation_document_number(
            {
                "status": "sending",
                "providerDocumentNumber": "Inv-123",
            }
        )
        == "Inv-123"
    )


def test_uncertain_record_accepts_legacy_doc_number_when_present():
    assert (
        reconciliation_document_number(
            {
                "status": "uncertain",
                "docNumber": "INV-OLD-42",
            }
        )
        == "INV-OLD-42"
    )


@pytest.mark.parametrize(
    "record",
    (
        None,
        {},
        {
            "status": "succeeded",
            "providerDocumentNumber": "INV-1",
        },
        {
            "status": "none",
            "providerDocumentNumber": "INV-1",
        },
    ),
)
def test_non_recovery_records_do_not_request_reconciliation_identity(
    record,
):
    assert reconciliation_document_number(record) is None


def test_reconciliation_policy_owner_has_no_provider_io():
    source = (
        ROOT
        / "masyg_extractor/integrations/accounting/"
          "shared/reconciliation.py"
    ).read_text()

    forbidden = (
        "httpx",
        "requests.",
        "QuickBooksClientAdapter",
        "XeroClientAdapter",
        "xero_request(",
        ".request(",
        "release_record_claim(",
        "claim_record(",
        "finalize_record(",
    )

    for token in forbidden:
        assert token not in source


def test_quickbooks_lookup_request_uses_exact_doc_number_query():
    request = build_provider_lookup_request(
        "quickbooks",
        "create_ar_invoice",
        "Inv-123",
    )

    assert request.provider == "quickbooks"
    assert request.endpoint == "query"
    assert request.method == "GET"
    assert request.params == {
        "query": (
            "SELECT Id, DocNumber FROM Invoice "
            "WHERE DocNumber = 'Inv-123' "
            "STARTPOSITION 1 MAXRESULTS 2"
        )
    }


def test_quickbooks_sales_receipt_lookup_uses_sales_receipt_resource():
    request = build_provider_lookup_request(
        "quickbooks",
        "create_sales_receipt",
        "REC-123",
    )

    assert (
        "FROM SalesReceipt "
        in request.params["query"]
    )

    assert (
        "WHERE DocNumber = 'REC-123'"
        in request.params["query"]
    )


def test_quickbooks_lookup_escapes_sql_literal():
    request = build_provider_lookup_request(
        "quickbooks",
        "create_ar_invoice",
        "Inv-'123",
    )

    assert (
        "WHERE DocNumber = 'Inv-''123'"
        in request.params["query"]
    )


@pytest.mark.parametrize(
    (
        "intent",
        "number",
    ),
    (
        (
            "create_ar_invoice",
            "Inv-123",
        ),
        (
            "create_ap_bill",
            "Inv-456",
        ),
    ),
)
def test_xero_lookup_request_uses_invoice_number_where_only(
    intent,
    number,
):
    request = build_provider_lookup_request(
        "xero",
        intent,
        number,
    )

    assert request.provider == "xero"
    assert request.endpoint == "Invoices"
    assert request.method == "GET"
    assert request.params == {
        "where": (
            f'InvoiceNumber=="{number}"'
        )
    }


def test_xero_lookup_rejects_unproven_where_quote_encoding():
    with pytest.raises(
        ValueError,
        match="cannot be represented safely",
    ):
        build_provider_lookup_request(
            "xero",
            "create_ar_invoice",
            'Inv-"123',
        )


@pytest.mark.parametrize(
    "number",
    (
        "",
        "   ",
    ),
)
def test_lookup_request_requires_durable_number(number):
    with pytest.raises(
        ValueError,
        match="provider document number is required",
    ):
        build_provider_lookup_request(
            "quickbooks",
            "create_ar_invoice",
            number,
        )


def test_quickbooks_exact_single_match_is_found():
    evidence = classify_provider_lookup_response(
        "quickbooks",
        "create_ar_invoice",
        "Inv-123",
        {
            "QueryResponse": {
                "Invoice": [
                    {
                        "Id": "qb-1",
                        "DocNumber": "Inv-123",
                    }
                ]
            }
        },
    )

    assert evidence.outcome == "found"
    assert evidence.provider_document_id == "qb-1"
    assert (
        evidence.provider_document_number
        == "Inv-123"
    )


def test_quickbooks_sales_receipt_exact_single_match_is_found():
    evidence = classify_provider_lookup_response(
        "quickbooks",
        "create_sales_receipt",
        "REC-123",
        {
            "QueryResponse": {
                "SalesReceipt": [
                    {
                        "Id": "qb-r1",
                        "DocNumber": "REC-123",
                    }
                ]
            }
        },
    )

    assert evidence.outcome == "found"
    assert evidence.provider_document_id == "qb-r1"


@pytest.mark.parametrize(
    "intent,resource,number",
    (
        (
            "create_ar_invoice",
            "Invoice",
            "Inv-123",
        ),
        (
            "create_sales_receipt",
            "SalesReceipt",
            "REC-123",
        ),
    ),
)
def test_quickbooks_explicit_empty_query_is_absent_but_still_blocked(
    intent,
    resource,
    number,
):
    evidence = classify_provider_lookup_response(
        "quickbooks",
        intent,
        number,
        {
            "QueryResponse": {
                resource: [],
            },
        },
    )

    assert evidence.outcome == "absent"

    decision = reconciliation_decision(
        evidence.outcome
    )

    assert decision.release_claim is False
    assert decision.allow_create is False
    assert decision.finalize_succeeded is False


@pytest.mark.parametrize(
    "intent,query_response",
    (
        (
            "create_ar_invoice",
            {},
        ),
        (
            "create_sales_receipt",
            {
                "Invoice": [],
            },
        ),
    ),
)
def test_quickbooks_missing_expected_entity_key_is_indeterminate(
    intent,
    query_response,
):
    number = (
        "REC-123"
        if intent == "create_sales_receipt"
        else "Inv-123"
    )

    evidence = classify_provider_lookup_response(
        "quickbooks",
        intent,
        number,
        {
            "QueryResponse": query_response,
        },
    )

    assert evidence.outcome == "indeterminate"

    decision = reconciliation_decision(
        evidence.outcome
    )

    assert decision.release_claim is False
    assert decision.allow_create is False
    assert decision.finalize_succeeded is False


@pytest.mark.parametrize(
    "response",
    (
        None,
        {},
        {
            "error": "transport failed",
        },
        {
            "QueryResponse": None,
        },
        {
            "QueryResponse": {
                "Invoice": {},
            }
        },
        {
            "QueryResponse": {
                "Invoice": [
                    {
                        "Id": "qb-1",
                        "DocNumber": "WRONG",
                    }
                ]
            }
        },
        {
            "QueryResponse": {
                "Invoice": [
                    {
                        "Id": "",
                        "DocNumber": "Inv-123",
                    }
                ]
            }
        },
        {
            "QueryResponse": {
                "Invoice": [
                    {
                        "Id": "qb-1",
                        "DocNumber": "Inv-123",
                    },
                    {
                        "Id": "qb-2",
                        "DocNumber": "Inv-123",
                    },
                ]
            }
        },
    ),
)
def test_quickbooks_suspicious_lookup_is_indeterminate(
    response,
):
    evidence = classify_provider_lookup_response(
        "quickbooks",
        "create_ar_invoice",
        "Inv-123",
        response,
    )

    assert evidence.outcome == "indeterminate"

    decision = reconciliation_decision(
        evidence.outcome
    )

    assert decision.release_claim is False
    assert decision.allow_create is False
    assert decision.finalize_succeeded is False


@pytest.mark.parametrize(
    (
        "intent",
        "expected_type",
    ),
    (
        (
            "create_ar_invoice",
            "ACCREC",
        ),
        (
            "create_ap_bill",
            "ACCPAY",
        ),
    ),
)
def test_xero_exact_single_match_is_found(
    intent,
    expected_type,
):
    evidence = classify_provider_lookup_response(
        "xero",
        intent,
        "Inv-123",
        {
            "Invoices": [
                {
                    "InvoiceID": "xe-1",
                    "InvoiceNumber": "Inv-123",
                    "Type": expected_type,
                }
            ]
        },
    )

    assert evidence.outcome == "found"
    assert evidence.provider_document_id == "xe-1"
    assert (
        evidence.provider_document_number
        == "Inv-123"
    )


def test_xero_valid_empty_lookup_is_absent_but_still_blocked():
    evidence = classify_provider_lookup_response(
        "xero",
        "create_ar_invoice",
        "Inv-123",
        {
            "Invoices": [],
        },
    )

    assert evidence.outcome == "absent"

    decision = reconciliation_decision(
        evidence.outcome
    )

    assert decision.release_claim is False
    assert decision.allow_create is False
    assert decision.finalize_succeeded is False


@pytest.mark.parametrize(
    "response",
    (
        None,
        {},
        {
            "error": "Xero service unavailable",
            "status_code": 502,
            "document_errors": [],
        },
        {
            "error": "Xero authorization expired",
            "status_code": 401,
            "document_errors": [],
        },
        {
            "error": "Xero rate limit reached",
            "status_code": 429,
            "document_errors": [],
        },
        {
            "Invoices": None,
        },
        {
            "Invoices": [
                {
                    "InvoiceID": "xe-1",
                    "InvoiceNumber": "WRONG",
                    "Type": "ACCREC",
                }
            ]
        },
        {
            "Invoices": [
                {
                    "InvoiceID": "",
                    "InvoiceNumber": "Inv-123",
                    "Type": "ACCREC",
                }
            ]
        },
        {
            "Invoices": [
                {
                    "InvoiceID": "xe-1",
                    "InvoiceNumber": "Inv-123",
                    "Type": "ACCPAY",
                }
            ]
        },
        {
            "Invoices": [
                {
                    "InvoiceID": "xe-1",
                    "InvoiceNumber": "Inv-123",
                    "Type": "ACCREC",
                },
                {
                    "InvoiceID": "xe-2",
                    "InvoiceNumber": "Inv-123",
                    "Type": "ACCREC",
                },
            ]
        },
    ),
)
def test_xero_suspicious_lookup_is_indeterminate(
    response,
):
    evidence = classify_provider_lookup_response(
        "xero",
        "create_ar_invoice",
        "Inv-123",
        response,
    )

    assert evidence.outcome == "indeterminate"

    decision = reconciliation_decision(
        evidence.outcome
    )

    assert decision.release_claim is False
    assert decision.allow_create is False
    assert decision.finalize_succeeded is False


def test_xero_ap_result_must_be_accpay_not_accrec():
    evidence = classify_provider_lookup_response(
        "xero",
        "create_ap_bill",
        "Inv-123",
        {
            "Invoices": [
                {
                    "InvoiceID": "xe-1",
                    "InvoiceNumber": "Inv-123",
                    "Type": "ACCREC",
                }
            ]
        },
    )

    assert evidence.outcome == "indeterminate"


def test_lookup_contract_performs_no_provider_or_persistence_io():
    source = (
        ROOT
        / "masyg_extractor/integrations/accounting/"
          "shared/reconciliation.py"
    ).read_text()

    forbidden = (
        "httpx",
        "requests.",
        "QuickBooksClientAdapter",
        "XeroClientAdapter",
        "self.client.request",
        "await ",
        "claim_record(",
        "release_record_claim(",
        "finalize_record(",
        "mark_record_uncertain(",
    )

    for token in forbidden:
        assert token not in source
