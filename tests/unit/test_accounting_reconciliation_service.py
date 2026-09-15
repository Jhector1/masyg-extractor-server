from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest

from masyg_extractor.integrations.accounting.shared.reconciliation_service import (
    verify_accounting_status,
)


ROOT = Path(__file__).resolve().parents[2]

NOW = datetime(
    2026,
    9,
    15,
    15,
    0,
    0,
    tzinfo=timezone.utc,
)


class FakeRepo:
    def __init__(
        self,
        records=None,
        *,
        token=None,
        read_error=None,
        token_error=None,
        finalize_result=None,
    ):
        self.records = {
            key: dict(value)
            for key, value in (
                records or {}
            ).items()
        }

        self.token = (
            token
            if token is not None
            else {
                "accessToken": "mock-token",
                "realmId": "mock-realm",
                "tenant_id": "mock-tenant",
            }
        )

        self.read_error = read_error
        self.token_error = token_error
        self.finalize_result = finalize_result

        self.get_calls = []
        self.token_calls = 0
        self.finalizer_calls = []

    def get_record(
        self,
        record_type,
        group_id,
        transaction_id,
    ):
        self.get_calls.append(
            (
                record_type,
                group_id,
                transaction_id,
            )
        )

        if self.read_error is not None:
            raise self.read_error

        value = self.records.get(
            (
                record_type,
                group_id,
                transaction_id,
            )
        )

        if value is None:
            return None

        return dict(value)

    def get_integration_token(self):
        self.token_calls += 1

        if self.token_error is not None:
            raise self.token_error

        return self.token

    def finalize_reconciled_record(
        self,
        record_type,
        group_id,
        transaction_id,
        *,
        claim_token,
        provider,
        intent,
        provider_document_id,
        provider_document_number,
    ):
        self.finalizer_calls.append(
            {
                "record_type": record_type,
                "group_id": group_id,
                "transaction_id": transaction_id,
                "provider": provider,
                "intent": intent,
                "provider_document_id":
                    provider_document_id,
                "provider_document_number":
                    provider_document_number,
            }
        )

        key = (
            record_type,
            group_id,
            transaction_id,
        )

        current = self.records.get(key)

        if self.finalize_result is not None:
            return self.finalize_result

        if current is None:
            return False

        status = str(
            current.get("status") or ""
        ).strip().lower()

        if status not in {
            "sending",
            "uncertain",
        }:
            return False

        current_claim_token = str(
            current.get("claimToken")
            or ""
        ).strip()

        if (
            current_claim_token
            != str(
                claim_token or ""
            ).strip()
        ):
            return False

        current_provider = str(
            current.get("integration") or ""
        ).strip().lower()

        if current_provider != str(
            provider or ""
        ).strip().lower():
            return False

        current_action = str(
            current.get("action") or ""
        ).strip()

        if (
            current_action
            and current_action != intent
        ):
            return False

        current_number = str(
            current.get("providerDocumentNumber")
            or current.get("docNumber")
            or ""
        ).strip()

        if (
            current_number
            != provider_document_number
        ):
            return False

        updated = dict(current)

        updated.update(
            {
                "status": "succeeded",
                "providerDocumentId":
                    provider_document_id,
                "providerDocumentNumber":
                    provider_document_number,
                "docNumber":
                    provider_document_number,
                "claimToken": None,
                "completedAt":
                    "2026-09-15T15:00:00Z",
                "reconciledAt":
                    "2026-09-15T15:00:00Z",
                "lastError": None,
            }
        )

        self.records[key] = updated

        return True

    # Verify Status has no authority to use these.
    def release_record_claim(
        self,
        *args,
        **kwargs,
    ):
        raise AssertionError(
            "Verify Status must never release"
        )

    def claim_record(
        self,
        *args,
        **kwargs,
    ):
        raise AssertionError(
            "Verify Status must never claim"
        )

    def mark_record_uncertain(
        self,
        *args,
        **kwargs,
    ):
        raise AssertionError(
            "Verify Status must never mark uncertain"
        )


class FakeClient:
    def __init__(
        self,
        response=None,
        *,
        error=None,
        on_request=None,
    ):
        self.response = (
            {}
            if response is None
            else response
        )

        self.error = error
        self.on_request = on_request
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

        if self.on_request is not None:
            self.on_request()

        if self.error is not None:
            raise self.error

        return self.response


def qb_record(
    *,
    status,
    claimed_at=None,
    number="Inv-123",
):
    record = {
        "status": status,
        "integration": "quickbooks",
        "action": "create_ar_invoice",
        "group_id": "group-1",
        "transactionId": "file-1",
    }

    if status in {
        "sending",
        "uncertain",
    }:
        record["claimToken"] = "claim-1"

    if claimed_at is not None:
        record["claimedAt"] = claimed_at

    if number is not None:
        record["providerDocumentNumber"] = number
        record["docNumber"] = number

    return record


def xero_record(
    *,
    status,
    claimed_at=None,
    number="Inv-123",
    action="create_ar_invoice",
):
    record = {
        "status": status,
        "integration": "xero",
        "action": action,
        "group_id": "group-1",
        "transactionId": "file-1",
    }

    if status in {
        "sending",
        "uncertain",
    }:
        record["claimToken"] = "claim-1"

    if claimed_at is not None:
        record["claimedAt"] = claimed_at

    if number is not None:
        record["providerDocumentNumber"] = number
        record["docNumber"] = number

    return record


def run_verify(
    *,
    repo,
    client,
    provider="quickbooks",
    intent="create_ar_invoice",
):
    return asyncio.run(
        verify_accounting_status(
            provider=provider,
            intent=intent,
            group_id="group-1",
            file_id="file-1",
            repo=repo,
            client=client,
            now=NOW,
        )
    )


def test_none_is_not_eligible_and_never_calls_provider():
    repo = FakeRepo()
    client = FakeClient()

    result = run_verify(
        repo=repo,
        client=client,
    )

    assert result.disposition == "not_eligible"
    assert result.lookup_outcome is None
    assert result.reconciled is False

    assert client.calls == []
    assert repo.token_calls == 0
    assert repo.finalizer_calls == []


def test_succeeded_is_already_created_without_provider_call():
    repo = FakeRepo(
        {
            (
                "invoices",
                "group-1",
                "file-1",
            ): {
                **qb_record(
                    status="succeeded",
                ),
                "providerDocumentId": "qb-1",
                "completedAt":
                    "2026-09-15T14:00:00Z",
            }
        }
    )

    client = FakeClient()

    result = run_verify(
        repo=repo,
        client=client,
    )

    assert result.disposition == "already_created"
    assert result.lookup_outcome is None
    assert result.reconciled is False

    assert client.calls == []
    assert repo.finalizer_calls == []


def test_recent_sending_is_processing_without_provider_call():
    repo = FakeRepo(
        {
            (
                "invoices",
                "group-1",
                "file-1",
            ): qb_record(
                status="sending",
                claimed_at=
                    "2026-09-15T14:45:01Z",
            )
        }
    )

    client = FakeClient()

    result = run_verify(
        repo=repo,
        client=client,
    )

    assert result.disposition == "processing"
    assert result.lookup_outcome is None
    assert result.reconciled is False

    assert (
        result.durable_status[
            "recovery_required"
        ]
        is False
    )

    assert client.calls == []
    assert repo.token_calls == 0
    assert repo.finalizer_calls == []


def test_exact_stale_boundary_is_verify_eligible():
    repo = FakeRepo(
        {
            (
                "invoices",
                "group-1",
                "file-1",
            ): qb_record(
                status="sending",
                claimed_at=
                    "2026-09-15T14:30:00Z",
            )
        }
    )

    client = FakeClient(
        {
            "QueryResponse": {
                "Invoice": [],
            },
        }
    )

    result = run_verify(
        repo=repo,
        client=client,
    )

    assert result.disposition == "needs_verification"
    assert result.lookup_outcome == "absent"

    assert len(client.calls) == 1
    assert client.calls[0]["method"] == "GET"

    assert (
        result.durable_status[
            "recovery_required"
        ]
        is True
    )


@pytest.mark.parametrize(
    "claimed_at",
    (
        None,
        "",
        "not-a-timestamp",
        "2026-09-15T16:00:00Z",
    ),
)
def test_unverifiable_sending_is_verify_eligible(
    claimed_at,
):
    repo = FakeRepo(
        {
            (
                "invoices",
                "group-1",
                "file-1",
            ): qb_record(
                status="sending",
                claimed_at=claimed_at,
            )
        }
    )

    client = FakeClient(
        {
            "QueryResponse": {
                "Invoice": [],
            },
        }
    )

    result = run_verify(
        repo=repo,
        client=client,
    )

    assert result.disposition == "needs_verification"
    assert result.lookup_outcome == "absent"

    assert len(client.calls) == 1
    assert client.calls[0]["method"] == "GET"


def test_stale_sending_without_durable_number_never_calls_provider():
    repo = FakeRepo(
        {
            (
                "invoices",
                "group-1",
                "file-1",
            ): qb_record(
                status="sending",
                claimed_at=
                    "2026-09-15T14:00:00Z",
                number=None,
            )
        }
    )

    client = FakeClient()

    result = run_verify(
        repo=repo,
        client=client,
    )

    assert result.disposition == "needs_verification"
    assert result.lookup_outcome == "indeterminate"

    assert client.calls == []
    assert repo.token_calls == 0
    assert repo.finalizer_calls == []


def test_uncertain_is_verify_eligible_without_age_requirement():
    repo = FakeRepo(
        {
            (
                "invoices",
                "group-1",
                "file-1",
            ): qb_record(
                status="uncertain",
            )
        }
    )

    client = FakeClient(
        {
            "QueryResponse": {
                "Invoice": [],
            },
        }
    )

    result = run_verify(
        repo=repo,
        client=client,
    )

    assert result.disposition == "needs_verification"
    assert result.lookup_outcome == "absent"

    assert len(client.calls) == 1
    assert repo.finalizer_calls == []


def test_quickbooks_found_reconciles_then_reports_already_created():
    repo = FakeRepo(
        {
            (
                "invoices",
                "group-1",
                "file-1",
            ): qb_record(
                status="uncertain",
            )
        }
    )

    client = FakeClient(
        {
            "QueryResponse": {
                "Invoice": [
                    {
                        "Id": "qb-found",
                        "DocNumber": "Inv-123",
                    }
                ]
            }
        }
    )

    result = run_verify(
        repo=repo,
        client=client,
    )

    assert result.disposition == "already_created"
    assert result.lookup_outcome == "found"
    assert result.reconciled is True

    assert len(client.calls) == 1
    assert client.calls[0]["method"] == "GET"

    assert len(repo.finalizer_calls) == 1

    assert (
        result.durable_status["status"]
        == "succeeded"
    )

    assert (
        result.durable_status[
            "provider_document_id"
        ]
        == "qb-found"
    )


def test_xero_found_reconciles_with_type_verified():
    repo = FakeRepo(
        {
            (
                "bills",
                "group-1",
                "file-1",
            ): xero_record(
                status="uncertain",
                action="create_ap_bill",
            )
        }
    )

    client = FakeClient(
        {
            "Invoices": [
                {
                    "InvoiceID": "xe-found",
                    "InvoiceNumber": "Inv-123",
                    "Type": "ACCPAY",
                }
            ]
        }
    )

    result = run_verify(
        repo=repo,
        client=client,
        provider="xero",
        intent="create_ap_bill",
    )

    assert result.disposition == "already_created"
    assert result.lookup_outcome == "found"
    assert result.reconciled is True

    assert len(client.calls) == 1
    assert client.calls[0]["method"] == "GET"

    assert (
        result.durable_status["status"]
        == "succeeded"
    )


@pytest.mark.parametrize(
    (
        "provider",
        "intent",
        "response",
    ),
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
def test_absent_never_mutates_or_unlocks(
    provider,
    intent,
    response,
):
    record = (
        qb_record(
            status="uncertain",
        )
        if provider == "quickbooks"
        else xero_record(
            status="uncertain",
        )
    )

    repo = FakeRepo(
        {
            (
                "invoices",
                "group-1",
                "file-1",
            ): record
        }
    )

    client = FakeClient(response)

    result = run_verify(
        repo=repo,
        client=client,
        provider=provider,
        intent=intent,
    )

    assert result.disposition == "needs_verification"
    assert result.lookup_outcome == "absent"
    assert result.reconciled is False

    assert len(client.calls) == 1
    assert repo.finalizer_calls == []

    stored = repo.records[
        (
            "invoices",
            "group-1",
            "file-1",
        )
    ]

    assert stored["status"] == "uncertain"


def test_provider_exception_is_indeterminate_without_mutation():
    repo = FakeRepo(
        {
            (
                "invoices",
                "group-1",
                "file-1",
            ): qb_record(
                status="uncertain",
            )
        }
    )

    client = FakeClient(
        error=RuntimeError(
            "provider timeout"
        )
    )

    result = run_verify(
        repo=repo,
        client=client,
    )

    assert result.disposition == "needs_verification"
    assert result.lookup_outcome == "indeterminate"
    assert result.reconciled is False

    assert len(client.calls) == 1
    assert repo.finalizer_calls == []


def test_found_but_finalizer_refusal_remains_needs_verification():
    repo = FakeRepo(
        {
            (
                "invoices",
                "group-1",
                "file-1",
            ): qb_record(
                status="uncertain",
            )
        },
        finalize_result=False,
    )

    client = FakeClient(
        {
            "QueryResponse": {
                "Invoice": [
                    {
                        "Id": "qb-found",
                        "DocNumber": "Inv-123",
                    }
                ]
            }
        }
    )

    result = run_verify(
        repo=repo,
        client=client,
    )

    assert result.disposition == "needs_verification"
    assert result.lookup_outcome == "found"
    assert result.reconciled is False

    assert (
        result.durable_status["status"]
        == "uncertain"
    )


def test_concurrent_success_is_accepted_only_after_canonical_reread():
    key = (
        "invoices",
        "group-1",
        "file-1",
    )

    repo = FakeRepo(
        {
            key: qb_record(
                status="uncertain",
            )
        }
    )

    def concurrently_succeed():
        current = dict(
            repo.records[key]
        )

        current.update(
            {
                "status": "succeeded",
                "providerDocumentId":
                    "qb-concurrent",
                "providerDocumentNumber":
                    "Inv-123",
                "completedAt":
                    "2026-09-15T14:59:59Z",
            }
        )

        repo.records[key] = current

    client = FakeClient(
        {
            "QueryResponse": {
                "Invoice": [
                    {
                        "Id": "qb-concurrent",
                        "DocNumber": "Inv-123",
                    }
                ]
            }
        },
        on_request=concurrently_succeed,
    )

    result = run_verify(
        repo=repo,
        client=client,
    )

    # The FOUND finalizer refuses because the state was already
    # succeeded by the concurrent writer. Canonical durable state,
    # not its boolean return value, is the final authority.
    assert result.reconciled is False
    assert result.lookup_outcome == "found"
    assert result.disposition == "already_created"

    assert (
        result.durable_status["status"]
        == "succeeded"
    )


def test_legacy_uncertain_xero_record_remains_blocked_without_lookup():
    repo = FakeRepo(
        {
            (
                "invoicess",
                "group-1",
                "file-1-0",
            ): {
                **xero_record(
                    status="uncertain",
                ),
                "transactionId": "file-1-0",
            }
        }
    )

    client = FakeClient(
        {
            "Invoices": [
                {
                    "InvoiceID": "xe-legacy",
                    "InvoiceNumber": "Inv-123",
                    "Type": "ACCREC",
                }
            ]
        }
    )

    result = run_verify(
        repo=repo,
        client=client,
        provider="xero",
        intent="create_ar_invoice",
    )

    assert result.disposition == "needs_verification"
    assert result.lookup_outcome == "indeterminate"

    assert result.durable_status["legacy"] is True

    assert client.calls == []
    assert repo.token_calls == 0
    assert repo.finalizer_calls == []


def test_unsupported_provider_intent_never_calls_provider():
    repo = FakeRepo()
    client = FakeClient()

    result = run_verify(
        repo=repo,
        client=client,
        provider="quickbooks",
        intent="create_ap_bill",
    )

    assert result.disposition == "not_eligible"
    assert result.durable_status is None

    assert client.calls == []
    assert repo.token_calls == 0
    assert repo.finalizer_calls == []


def test_invalid_identity_never_calls_provider():
    repo = FakeRepo()
    client = FakeClient()

    result = asyncio.run(
        verify_accounting_status(
            provider="quickbooks",
            intent="create_ar_invoice",
            group_id="",
            file_id="file-1",
            repo=repo,
            client=client,
            now=NOW,
        )
    )

    assert result.disposition == "not_eligible"
    assert client.calls == []


def test_verify_service_has_no_release_retry_or_provider_write_authority():
    source = (
        ROOT
        / "masyg_extractor/integrations/accounting/"
          "shared/reconciliation_service.py"
    ).read_text()

    forbidden = (
        "release_record_claim(",
        "claim_record(",
        "mark_record_uncertain(",
        "prepare_provider_dispatch(",
        "mark_provider_dispatch_started(",
        '"POST"',
        '"PUT"',
        '"DELETE"',
    )

    for token in forbidden:
        assert token not in source


def test_eligibility_gate_precedes_lookup_executor():
    source = (
        ROOT
        / "masyg_extractor/integrations/accounting/"
          "shared/reconciliation_service.py"
    ).read_text()

    durable_read = source.index(
        "initial_status = await _read_status("
    )

    gate = source.index(
        "if not _eligible_for_provider_verification(",
        durable_read,
    )

    lookup = source.index(
        "await execute_provider_reconciliation_lookup(",
        gate,
    )

    assert durable_read < gate < lookup


def test_found_finalization_precedes_canonical_status_reread():
    source = (
        ROOT
        / "masyg_extractor/integrations/accounting/"
          "shared/reconciliation_service.py"
    ).read_text()

    found_guard = source.index(
        'if evidence.outcome != "found":'
    )

    mutation = source.index(
        "await apply_found_reconciliation(",
        found_guard,
    )

    reread = source.index(
        "final_status = await _read_status(",
        mutation,
    )

    assert found_guard < mutation < reread

def test_found_from_stale_generation_cannot_finalize_replacement_claim():
    key = (
        "invoices",
        "group-1",
        "file-1",
    )

    initial = qb_record(
        status="uncertain",
    )
    initial["claimToken"] = "generation-a"

    repo = FakeRepo(
        {
            key: initial,
        }
    )

    def replace_generation():
        replacement = qb_record(
            status="sending",
            claimed_at=NOW.isoformat(),
        )
        replacement[
            "claimToken"
        ] = "generation-b"

        repo.records[key] = replacement

    client = FakeClient(
        response={
            "QueryResponse": {
                "Invoice": [
                    {
                        "Id": "qb-old",
                        "DocNumber": "Inv-123",
                    }
                ]
            }
        },
        on_request=replace_generation,
    )

    result = run_verify(
        repo=repo,
        client=client,
    )

    assert len(client.calls) == 1
    assert len(repo.finalizer_calls) == 1

    current = repo.records[key]

    assert current["status"] == "sending"
    assert (
        current["claimToken"]
        == "generation-b"
    )
    assert "providerDocumentId" not in current

    assert result.reconciled is False


def test_verify_rechecks_current_generation_before_provider_get():
    key = (
        "invoices",
        "group-1",
        "file-1",
    )

    initial = qb_record(
        status="sending",
        claimed_at="2026-09-15T01:00:00Z",
    )
    initial["claimToken"] = "generation-a"

    replacement = qb_record(
        status="sending",
        claimed_at=NOW.isoformat(),
    )
    replacement["claimToken"] = "generation-b"

    class ReplacingRepo(FakeRepo):
        def get_record(
            self,
            record_type,
            group_id,
            transaction_id,
        ):
            result = super().get_record(
                record_type,
                group_id,
                transaction_id,
            )

            if len(self.get_calls) == 1:
                self.records[key] = dict(
                    replacement
                )

            return result

    repo = ReplacingRepo(
        {
            key: initial,
        }
    )

    client = FakeClient(
        response={
            "QueryResponse": {
                "Invoice": [
                    {
                        "Id": "qb-old",
                        "DocNumber": "Inv-123",
                    }
                ]
            }
        },
    )

    result = run_verify(
        repo=repo,
        client=client,
    )

    assert client.calls == []
    assert repo.token_calls == 0
    assert repo.finalizer_calls == []

    current = repo.records[key]

    assert current["status"] == "sending"
    assert (
        current["claimToken"]
        == "generation-b"
    )

    assert result.reconciled is False
    assert (
        result.lookup_outcome
        == "indeterminate"
    )
