from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from masyg_extractor.integrations.accounting.shared.reconciliation_lookup import (
    execute_provider_reconciliation_lookup,
)


ROOT = Path(__file__).resolve().parents[2]


class FakeRepo:
    def __init__(
        self,
        record,
        *,
        token=None,
        read_error=None,
        token_error=None,
    ):
        self.record = record
        self.token = (
            token
            if token is not None
            else {
                "accessToken": "mock-token",
                "tenant_id": "mock-tenant",
                "realmId": "mock-realm",
            }
        )
        self.read_error = read_error
        self.token_error = token_error
        self.read_calls = []
        self.token_calls = 0

    def get_record(
        self,
        record_type,
        group_id,
        transaction_id,
    ):
        self.read_calls.append(
            (
                record_type,
                group_id,
                transaction_id,
            )
        )

        if self.read_error is not None:
            raise self.read_error

        return self.record

    def get_integration_token(self):
        self.token_calls += 1

        if self.token_error is not None:
            raise self.token_error

        return self.token

    # Any reconciliation attempt to mutate durable state is a test
    # failure immediately.
    def claim_record(self, *args, **kwargs):
        raise AssertionError(
            "lookup executor must not claim"
        )

    def release_record_claim(self, *args, **kwargs):
        raise AssertionError(
            "lookup executor must not release"
        )

    def mark_record_uncertain(self, *args, **kwargs):
        raise AssertionError(
            "lookup executor must not mutate uncertainty"
        )

    def finalize_record(self, *args, **kwargs):
        raise AssertionError(
            "lookup executor must not finalize"
        )

    def store_record(self, *args, **kwargs):
        raise AssertionError(
            "lookup executor must not store"
        )

    def store_document(self, *args, **kwargs):
        raise AssertionError(
            "lookup executor must not store"
        )


class FakeClient:
    def __init__(
        self,
        response=None,
        *,
        error=None,
    ):
        self.response = (
            {}
            if response is None
            else response
        )
        self.error = error
        self.calls = []

    async def request(
        self,
        token,
        endpoint,
        method="POST",
        payload=None,
        params=None,
        **kwargs,
    ):
        self.calls.append(
            {
                "token": token,
                "endpoint": endpoint,
                "method": method,
                "payload": payload,
                "params": params,
                "kwargs": kwargs,
            }
        )

        if self.error is not None:
            raise self.error

        return self.response


def run_lookup(
    *,
    provider,
    intent,
    record,
    response=None,
    repo=None,
    client=None,
    record_type="invoices",
    now=None,
):
    repo = repo or FakeRepo(record)
    client = client or FakeClient(response)

    result = asyncio.run(
        execute_provider_reconciliation_lookup(
            provider=provider,
            intent=intent,
            record_type=record_type,
            group_id="group-1",
            transaction_id="file-1",
            repo=repo,
            client=client,
            now=now,
        )
    )

    return result, repo, client


def sending(number="Inv-123"):
    return {
        "status": "sending",
        "claimToken": "claim-1",
        "providerDocumentNumber": number,
        "docNumber": number,
        "dispatchPreparedAt":
            "2026-09-15T01:00:00Z",
    }


def uncertain(number="Inv-123"):
    return {
        "status": "uncertain",
        "claimToken": "claim-1",
        "providerDocumentNumber": number,
        "docNumber": number,
    }


def test_quickbooks_found_uses_one_exact_get():
    evidence, repo, client = run_lookup(
        provider="quickbooks",
        intent="create_ar_invoice",
        record=sending(),
        response={
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
    assert evidence.provider_document_number == "Inv-123"

    assert repo.read_calls == [
        (
            "invoices",
            "group-1",
            "file-1",
        )
    ]

    assert repo.token_calls == 1
    assert len(client.calls) == 1

    call = client.calls[0]

    assert call["endpoint"] == "query"
    assert call["method"] == "GET"
    assert call["payload"] is None

    assert call["params"] == {
        "query": (
            "SELECT Id, DocNumber FROM Invoice "
            "WHERE DocNumber = 'Inv-123' "
            "STARTPOSITION 1 MAXRESULTS 2"
        )
    }


def test_quickbooks_sales_receipt_found_uses_sales_receipt_query():
    evidence, _, client = run_lookup(
        provider="quickbooks",
        intent="create_sales_receipt",
        record=sending("REC-123"),
        record_type="salesreceipts",
        response={
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

    assert len(client.calls) == 1

    assert (
        "FROM SalesReceipt "
        in client.calls[0]["params"]["query"]
    )


def test_xero_ar_found_uses_one_exact_get():
    evidence, repo, client = run_lookup(
        provider="xero",
        intent="create_ar_invoice",
        record=uncertain(),
        response={
            "Invoices": [
                {
                    "InvoiceID": "xe-1",
                    "InvoiceNumber": "Inv-123",
                    "Type": "ACCREC",
                }
            ]
        },
    )

    assert evidence.outcome == "found"
    assert evidence.provider_document_id == "xe-1"

    assert repo.token_calls == 1
    assert len(client.calls) == 1

    call = client.calls[0]

    assert call["endpoint"] == "Invoices"
    assert call["method"] == "GET"
    assert call["payload"] is None
    assert call["params"] == {
        "where": 'InvoiceNumber=="Inv-123"',
    }


def test_xero_ap_found_requires_accpay():
    evidence, _, client = run_lookup(
        provider="xero",
        intent="create_ap_bill",
        record=uncertain(),
        record_type="bills",
        response={
            "Invoices": [
                {
                    "InvoiceID": "xe-b1",
                    "InvoiceNumber": "Inv-123",
                    "Type": "ACCPAY",
                }
            ]
        },
    )

    assert evidence.outcome == "found"
    assert len(client.calls) == 1


def test_xero_ap_wrong_type_is_indeterminate():
    evidence, _, client = run_lookup(
        provider="xero",
        intent="create_ap_bill",
        record=uncertain(),
        record_type="bills",
        response={
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
    assert len(client.calls) == 1


@pytest.mark.parametrize(
    "provider,intent,response",
    (
        (
            "quickbooks",
            "create_ar_invoice",
            {
                "QueryResponse": {
                    "Invoice": [],
                },
            },
        ),
        (
            "xero",
            "create_ar_invoice",
            {
                "Invoices": [],
            },
        ),
    ),
)
def test_valid_empty_lookup_returns_absent_without_mutation(
    provider,
    intent,
    response,
):
    evidence, _, client = run_lookup(
        provider=provider,
        intent=intent,
        record=uncertain(),
        response=response,
    )

    assert evidence.outcome == "absent"
    assert len(client.calls) == 1


def test_quickbooks_missing_expected_entity_key_is_indeterminate():
    evidence, _, client = run_lookup(
        provider="quickbooks",
        intent="create_ar_invoice",
        record=uncertain(),
        response={
            "QueryResponse": {},
        },
    )

    assert evidence.outcome == "indeterminate"
    assert len(client.calls) == 1


def test_quickbooks_explicit_empty_expected_entity_is_absent():
    evidence, _, client = run_lookup(
        provider="quickbooks",
        intent="create_ar_invoice",
        record=uncertain(),
        response={
            "QueryResponse": {
                "Invoice": [],
            },
        },
    )

    assert evidence.outcome == "absent"
    assert len(client.calls) == 1


@pytest.mark.parametrize(
    "record",
    (
        None,
        {},
        {
            "status": "sending",
        },
        {
            "status": "succeeded",
            "providerDocumentNumber": "Inv-123",
        },
        {
            "status": "none",
            "providerDocumentNumber": "Inv-123",
        },
    ),
)
def test_missing_or_non_recovery_identity_never_calls_provider(record):
    evidence, repo, client = run_lookup(
        provider="quickbooks",
        intent="create_ar_invoice",
        record=record,
        response={
            "QueryResponse": {
                "Invoice": []
            }
        },
    )

    assert evidence.outcome == "indeterminate"
    assert repo.token_calls == 0
    assert client.calls == []


def test_durable_read_failure_is_indeterminate_without_provider_call():
    repo = FakeRepo(
        None,
        read_error=RuntimeError(
            "firestore unavailable"
        ),
    )

    client = FakeClient(
        {
            "QueryResponse": {}
        }
    )

    evidence, _, _ = run_lookup(
        provider="quickbooks",
        intent="create_ar_invoice",
        record=None,
        repo=repo,
        client=client,
    )

    assert evidence.outcome == "indeterminate"
    assert repo.token_calls == 0
    assert client.calls == []


def test_token_failure_is_indeterminate_without_provider_call():
    repo = FakeRepo(
        sending(),
        token_error=RuntimeError(
            "token unavailable"
        ),
    )

    client = FakeClient(
        {
            "QueryResponse": {}
        }
    )

    evidence, _, _ = run_lookup(
        provider="quickbooks",
        intent="create_ar_invoice",
        record=sending(),
        repo=repo,
        client=client,
    )

    assert evidence.outcome == "indeterminate"
    assert repo.token_calls == 1
    assert client.calls == []


@pytest.mark.parametrize(
    "provider,intent",
    (
        (
            "quickbooks",
            "create_ar_invoice",
        ),
        (
            "xero",
            "create_ar_invoice",
        ),
    ),
)
def test_provider_exception_is_indeterminate(
    provider,
    intent,
):
    client = FakeClient(
        error=RuntimeError(
            "provider transport failure"
        )
    )

    evidence, _, client = run_lookup(
        provider=provider,
        intent=intent,
        record=uncertain(),
        client=client,
    )

    assert evidence.outcome == "indeterminate"
    assert len(client.calls) == 1
    assert client.calls[0]["method"] == "GET"


@pytest.mark.parametrize(
    "provider,intent,response",
    (
        (
            "quickbooks",
            "create_ar_invoice",
            {
                "error": "QuickBooks failed",
            },
        ),
        (
            "xero",
            "create_ar_invoice",
            {
                "error": "Xero failed",
                "status_code": 502,
                "document_errors": [],
            },
        ),
    ),
)
def test_provider_error_envelope_is_indeterminate(
    provider,
    intent,
    response,
):
    evidence, _, client = run_lookup(
        provider=provider,
        intent=intent,
        record=uncertain(),
        response=response,
    )

    assert evidence.outcome == "indeterminate"
    assert len(client.calls) == 1


def test_multiple_matches_remain_indeterminate():
    evidence, _, client = run_lookup(
        provider="quickbooks",
        intent="create_ar_invoice",
        record=uncertain(),
        response={
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
    )

    assert evidence.outcome == "indeterminate"
    assert len(client.calls) == 1


def test_executor_source_has_no_accounting_mutation_operations():
    source = (
        ROOT
        / "masyg_extractor/integrations/accounting/"
          "shared/reconciliation_lookup.py"
    ).read_text()

    forbidden = (
        "claim_record(",
        "release_record_claim(",
        "mark_record_uncertain(",
        "finalize_record(",
        "prepare_provider_dispatch(",
        "mark_provider_dispatch_started(",
        "store_record(",
        "store_document(",
        ".delete(",
    )

    for token in forbidden:
        assert token not in source


def test_executor_source_has_no_provider_write_method():
    source = (
        ROOT
        / "masyg_extractor/integrations/accounting/"
          "shared/reconciliation_lookup.py"
    ).read_text()

    assert '"POST"' not in source
    assert '"PUT"' not in source
    assert '"DELETE"' not in source

    assert (
        'lookup_request.method != "GET"'
        in source
    )

def test_tokenless_current_claim_never_calls_provider():
    record = uncertain()
    record.pop("claimToken")

    evidence, repo, client = run_lookup(
        provider="quickbooks",
        intent="create_ar_invoice",
        record=record,
        response={
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

    assert evidence.outcome == "indeterminate"
    assert evidence.observed_claim_token is None
    assert client.calls == []
    assert repo.token_calls == 0


def test_recent_sending_second_gate_never_calls_provider():
    from datetime import datetime, timezone

    record = sending()
    record["claimedAt"] = (
        "2026-09-15T14:55:00+00:00"
    )

    evidence, repo, client = run_lookup(
        provider="quickbooks",
        intent="create_ar_invoice",
        record=record,
        response={
            "QueryResponse": {
                "Invoice": [
                    {
                        "Id": "qb-1",
                        "DocNumber": "Inv-123",
                    }
                ]
            }
        },
        now=datetime(
            2026,
            9,
            15,
            15,
            0,
            tzinfo=timezone.utc,
        ),
    )

    assert evidence.outcome == "indeterminate"
    assert (
        evidence.observed_claim_token
        == "claim-1"
    )
    assert client.calls == []
    assert repo.token_calls == 0


def test_found_evidence_carries_observed_claim_generation():
    evidence, repo, client = run_lookup(
        provider="quickbooks",
        intent="create_ar_invoice",
        record=uncertain(),
        response={
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
    assert (
        evidence.observed_claim_token
        == "claim-1"
    )
    assert len(client.calls) == 1
    assert client.calls[0]["method"] == "GET"
