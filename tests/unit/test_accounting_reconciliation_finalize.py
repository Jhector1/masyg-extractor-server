from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from masyg_extractor.integrations.accounting.shared import (
    firestore_repository as firestore_repository_module,
)
from masyg_extractor.integrations.accounting.shared.firestore_repository import (
    QuickBooksFirestoreService,
)
from masyg_extractor.integrations.accounting.shared.reconciliation import (
    ProviderLookupEvidence,
)
from masyg_extractor.integrations.accounting.shared.reconciliation_finalize import (
    apply_found_reconciliation,
)


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _identity_firestore_transactional(
    monkeypatch,
):
    monkeypatch.setattr(
        firestore_repository_module.firestore,
        "transactional",
        lambda function: function,
    )


class FakeSnapshot:
    def __init__(self, data):
        self._data = data

    @property
    def exists(self):
        return self._data is not None

    def to_dict(self):
        if self._data is None:
            return {}

        return dict(self._data)


class FakeDocumentReference:
    def __init__(self, owner):
        self.owner = owner

    def get(
        self,
        *args,
        **kwargs,
    ):
        self.owner.read_count += 1

        if kwargs.get("transaction") is not None:
            self.owner.transaction_reads += 1

        return FakeSnapshot(
            self.owner.data
        )


class FakeTransaction:
    def __init__(self, owner):
        self.owner = owner

    def update(
        self,
        doc_ref,
        payload,
    ):
        self.owner.transaction_updates += 1

        self.owner.store_document(
            doc_ref,
            payload,
            merge=True,
        )

    def delete(
        self,
        _doc_ref,
    ):
        self.owner.transaction_deletes += 1
        self.owner.data = None


class FakeDb:
    def __init__(self, owner):
        self.owner = owner

    def transaction(self):
        self.owner.transaction_count += 1

        return FakeTransaction(
            self.owner
        )


class FakeRepositoryOwner:
    def __init__(self, data):
        self.data = (
            None
            if data is None
            else dict(data)
        )
        self.writes = []
        self.read_count = 0
        self.finalizer_calls = []

        self.transaction_count = 0
        self.transaction_reads = 0
        self.transaction_updates = 0
        self.transaction_deletes = 0

        self.db = FakeDb(self)

    def _get_transaction_doc_ref(
        self,
        record_type,
        group_id,
        transaction_id,
    ):
        self.lookup = (
            record_type,
            group_id,
            transaction_id,
        )

        return FakeDocumentReference(self)

    def store_document(
        self,
        _doc_ref,
        payload,
        *,
        merge=False,
    ):
        self.writes.append(
            (
                dict(payload),
                merge,
            )
        )

        if self.data is None:
            self.data = {}

        if merge:
            self.data.update(payload)
        else:
            self.data = dict(payload)

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

        return (
            QuickBooksFirestoreService
            .finalize_reconciled_record(
                self,
                record_type,
                group_id,
                transaction_id,
                claim_token=claim_token,
                provider=provider,
                intent=intent,
                provider_document_id=(
                    provider_document_id
                ),
                provider_document_number=(
                    provider_document_number
                ),
            )
        )

    # Any accidental non-reconciliation mutation must fail loudly.
    def claim_record(self, *args, **kwargs):
        raise AssertionError(
            "reconciliation must not claim"
        )

    def release_record_claim(self, *args, **kwargs):
        raise AssertionError(
            "reconciliation must not release"
        )

    def mark_record_uncertain(self, *args, **kwargs):
        raise AssertionError(
            "reconciliation must not mark uncertain"
        )


def durable(
    *,
    status="sending",
    provider="quickbooks",
    action="create_ar_invoice",
    number="Inv-123",
):
    record = {
        "status": status,
        "integration": provider,
        "action": action,
        "group_id": "group-1",
        "transactionId": "file-1",
        "providerDocumentNumber": number,
        "docNumber": number,
        "claimedAt": "2026-09-15T01:00:00Z",
        "lastError": "previous ambiguous outcome",
    }

    if status in {
        "sending",
        "uncertain",
    }:
        record["claimToken"] = "claim-1"

    return record


def found(
    *,
    provider_id="provider-1",
    number="Inv-123",
):
    return ProviderLookupEvidence(
        outcome="found",
        provider_document_id=provider_id,
        provider_document_number=number,

        observed_claim_token="claim-1",)


def apply(
    repo,
    evidence,
    *,
    provider="quickbooks",
    intent="create_ar_invoice",
    record_type="invoices",
):
    return asyncio.run(
        apply_found_reconciliation(
            provider=provider,
            intent=intent,
            record_type=record_type,
            group_id="group-1",
            transaction_id="file-1",
            evidence=evidence,
            repo=repo,
        )
    )


def test_found_finalizes_sending_claim():
    repo = FakeRepositoryOwner(
        durable(
            status="sending",
        )
    )

    assert apply(
        repo,
        found(
            provider_id="qb-1",
        ),
    ) is True

    assert repo.read_count == 1
    assert len(repo.finalizer_calls) == 1
    assert len(repo.writes) == 1

    assert repo.data["status"] == "succeeded"
    assert repo.data["docNumber"] == "Inv-123"
    assert (
        repo.data["providerDocumentNumber"]
        == "Inv-123"
    )
    assert repo.data["providerDocumentId"] == "qb-1"
    assert repo.data["completedAt"]
    assert repo.data["reconciledAt"]
    assert repo.data["lastError"] is None


def test_found_finalizes_uncertain_claim():
    repo = FakeRepositoryOwner(
        durable(
            status="uncertain",
        )
    )

    assert apply(
        repo,
        found(
            provider_id="qb-2",
        ),
    ) is True

    assert repo.data["status"] == "succeeded"
    assert repo.data["providerDocumentId"] == "qb-2"


def test_xero_found_finalizes_matching_ap_bill():
    repo = FakeRepositoryOwner(
        durable(
            status="uncertain",
            provider="xero",
            action="create_ap_bill",
        )
    )

    assert apply(
        repo,
        found(
            provider_id="xe-1",
        ),
        provider="xero",
        intent="create_ap_bill",
        record_type="bills",
    ) is True

    assert repo.data["status"] == "succeeded"
    assert repo.data["providerDocumentId"] == "xe-1"


@pytest.mark.parametrize(
    "outcome",
    (
        "absent",
        "indeterminate",
    ),
)
def test_non_found_evidence_never_reaches_mutation_owner(
    outcome,
):
    repo = FakeRepositoryOwner(
        durable()
    )

    evidence = ProviderLookupEvidence(
        outcome=outcome,
    )

    assert apply(
        repo,
        evidence,
    ) is False

    assert repo.finalizer_calls == []
    assert repo.read_count == 0
    assert repo.writes == []


@pytest.mark.parametrize(
    "evidence",
    (
        ProviderLookupEvidence(
            outcome="found",
            provider_document_id=None,
            provider_document_number="Inv-123",
        ),
        ProviderLookupEvidence(
            outcome="found",
            provider_document_id="qb-1",
            provider_document_number=None,
        ),
        ProviderLookupEvidence(
            outcome="found",
            provider_document_id="",
            provider_document_number="Inv-123",
        ),
        ProviderLookupEvidence(
            outcome="found",
            provider_document_id="qb-1",
            provider_document_number="",
        ),
    ),
)
def test_incomplete_found_evidence_never_mutates(
    evidence,
):
    repo = FakeRepositoryOwner(
        durable()
    )

    assert apply(
        repo,
        evidence,
    ) is False

    assert repo.finalizer_calls == []
    assert repo.writes == []


@pytest.mark.parametrize(
    "status",
    (
        "succeeded",
        "none",
        "",
    ),
)
def test_repository_refuses_non_recovery_status(status):
    repo = FakeRepositoryOwner(
        durable(
            status=status,
        )
    )

    assert apply(
        repo,
        found(),
    ) is False

    assert repo.read_count == 1
    assert repo.writes == []


def test_repository_refuses_missing_record():
    repo = FakeRepositoryOwner(None)

    assert apply(
        repo,
        found(),
    ) is False

    assert repo.read_count == 1
    assert repo.writes == []


def test_repository_refuses_provider_mismatch():
    repo = FakeRepositoryOwner(
        durable(
            provider="xero",
        )
    )

    assert apply(
        repo,
        found(),
        provider="quickbooks",
    ) is False

    assert repo.writes == []


def test_repository_refuses_action_mismatch():
    repo = FakeRepositoryOwner(
        durable(
            action="create_sales_receipt",
        )
    )

    assert apply(
        repo,
        found(),
        intent="create_ar_invoice",
    ) is False

    assert repo.writes == []


def test_missing_legacy_action_does_not_block_otherwise_exact_match():
    record = durable()
    record.pop("action")

    repo = FakeRepositoryOwner(record)

    assert apply(
        repo,
        found(),
    ) is True

    assert repo.data["status"] == "succeeded"


def test_repository_refuses_provider_number_mismatch():
    repo = FakeRepositoryOwner(
        durable(
            number="Inv-ORIGINAL",
        )
    )

    assert apply(
        repo,
        found(
            number="Inv-DIFFERENT",
        ),
    ) is False

    assert repo.writes == []


def test_repository_refuses_empty_provider_id():
    repo = FakeRepositoryOwner(
        durable()
    )

    result = (
        QuickBooksFirestoreService
        .finalize_reconciled_record(
            repo,
            "invoices",
            "group-1",
            "file-1",
            provider="quickbooks",
            intent="create_ar_invoice",
            provider_document_id="",
            provider_document_number="Inv-123",

            claim_token="claim-1",)
    )

    assert result is False
    assert repo.read_count == 0
    assert repo.writes == []


def test_repository_refuses_empty_provider_number():
    repo = FakeRepositoryOwner(
        durable()
    )

    result = (
        QuickBooksFirestoreService
        .finalize_reconciled_record(
            repo,
            "invoices",
            "group-1",
            "file-1",
            provider="quickbooks",
            intent="create_ar_invoice",
            provider_document_id="qb-1",
            provider_document_number="",

            claim_token="claim-1",)
    )

    assert result is False
    assert repo.read_count == 0
    assert repo.writes == []


def test_repository_accepts_doc_number_compatibility_field():
    record = durable()
    record.pop("providerDocumentNumber")

    repo = FakeRepositoryOwner(record)

    assert apply(
        repo,
        found(),
    ) is True

    assert (
        repo.data["providerDocumentNumber"]
        == "Inv-123"
    )


def test_repository_write_preserves_duplicate_barrier_as_succeeded():
    repo = FakeRepositoryOwner(
        durable(
            status="uncertain",
        )
    )

    assert apply(
        repo,
        found(),
    ) is True

    payload, merge = repo.writes[0]

    assert merge is True
    assert payload["status"] == "succeeded"

    assert "status" in payload
    assert payload["status"] != "sending"
    assert payload["status"] != "uncertain"


def test_unsupported_provider_intent_never_calls_repository():
    repo = FakeRepositoryOwner(
        durable()
    )

    assert apply(
        repo,
        found(),
        provider="quickbooks",
        intent="create_ap_bill",
    ) is False

    assert repo.finalizer_calls == []
    assert repo.writes == []


def test_repository_exception_fails_closed():
    class ExplodingRepo:
        def finalize_reconciled_record(
            self,
            *args,
            **kwargs,
        ):
            raise RuntimeError(
                "firestore unavailable"
            )

    assert (
        asyncio.run(
            apply_found_reconciliation(
                provider="quickbooks",
                intent="create_ar_invoice",
                record_type="invoices",
                group_id="group-1",
                transaction_id="file-1",
                evidence=found(),
                repo=ExplodingRepo(),
            )
        )
        is False
    )


def test_finalization_owner_has_no_provider_io_or_release():
    source = (
        ROOT
        / "masyg_extractor/integrations/accounting/"
          "shared/reconciliation_finalize.py"
    ).read_text()

    forbidden = (
        "client.request",
        "httpx",
        "requests.",
        "release_record_claim(",
        "claim_record(",
        "mark_record_uncertain(",
        '"POST"',
        '"PUT"',
        '"DELETE"',
    )

    for token in forbidden:
        assert token not in source


def test_repository_finalizer_has_no_provider_io_or_release():
    source = (
        ROOT
        / "masyg_extractor/integrations/accounting/"
          "shared/firestore_repository.py"
    ).read_text()

    start = source.index(
        "    def finalize_reconciled_record("
    )

    end = source.index(
        "    def finalize_record(",
        start,
    )

    block = source[start:end]

    forbidden = (
        ".request(",
        "httpx",
        "requests.",
        "release_record_claim(",
        "claim_record(",
        "doc_ref.delete()",
        '"status": "sending"',
        '"status": "uncertain"',
    )

    for token in forbidden:
        assert token not in block

    assert '"status": "succeeded"' in block
    assert '"providerDocumentId": provider_id' in block
    import re

    assert re.search(
        (
            r'"providerDocumentNumber"'
            r'\s*:\s*'
            r'provider_number'
        ),
        block,
    )
    assert '"reconciledAt": completed_at' in block


def test_read_only_lookup_executor_remains_mutation_free():
    source = (
        ROOT
        / "masyg_extractor/integrations/accounting/"
          "shared/reconciliation_lookup.py"
    ).read_text()

    assert "finalize_reconciled_record" not in source
    assert "apply_found_reconciliation" not in source
    assert "finalize_record(" not in source


def test_reconciled_finalization_uses_transactional_read_and_update():
    repo = FakeRepositoryOwner(
        durable(
            status="sending",
        )
    )

    assert apply(
        repo,
        found(
            provider_id="qb-atomic",
        ),
    ) is True

    assert repo.transaction_count == 1
    assert repo.transaction_reads == 1
    assert repo.transaction_updates == 1
    assert repo.transaction_deletes == 0

    assert repo.data["status"] == "succeeded"
    assert (
        repo.data["providerDocumentId"]
        == "qb-atomic"
    )


def test_atomic_release_still_deletes_sending_claim():
    repo = FakeRepositoryOwner(
        durable(
            status="sending",
        )
    )

    repo.data["claimToken"] = "claim-1"

    result = (
        QuickBooksFirestoreService
        .release_record_claim(
            repo,
            "invoices",
            "group-1",
            "file-1",

            claim_token="claim-1",)
    )

    assert result is True
    assert repo.transaction_count == 1
    assert repo.transaction_reads == 1
    assert repo.transaction_deletes == 1
    assert repo.data is None


def test_atomic_release_never_deletes_succeeded_barrier():
    repo = FakeRepositoryOwner(
        durable(
            status="succeeded",
        )
    )

    result = (
        QuickBooksFirestoreService
        .release_record_claim(
            repo,
            "invoices",
            "group-1",
            "file-1",

            claim_token="claim-1",)
    )

    assert result is False
    assert repo.transaction_count == 1
    assert repo.transaction_reads == 1
    assert repo.transaction_deletes == 0
    assert repo.data["status"] == "succeeded"


def test_atomic_uncertain_transition_preserves_sending_semantics():
    repo = FakeRepositoryOwner(
        durable(
            status="sending",
        )
    )

    repo.data["claimToken"] = "claim-1"

    (
        QuickBooksFirestoreService
        .mark_record_uncertain(
            repo,
            "invoices",
            "group-1",
            "file-1",
            error="ambiguous provider outcome",

            claim_token="claim-1",)
    )

    assert repo.transaction_count == 1
    assert repo.transaction_reads == 1
    assert repo.transaction_updates == 1

    assert repo.data["status"] == "uncertain"
    assert (
        repo.data["lastError"]
        == "ambiguous provider outcome"
    )


def test_atomic_uncertain_transition_never_demotes_succeeded():
    repo = FakeRepositoryOwner(
        durable(
            status="succeeded",
        )
    )

    repo.data["providerDocumentId"] = "qb-1"

    (
        QuickBooksFirestoreService
        .mark_record_uncertain(
            repo,
            "invoices",
            "group-1",
            "file-1",
            error="late timeout",

            claim_token="claim-1",)
    )

    assert repo.transaction_count == 1
    assert repo.transaction_reads == 1
    assert repo.transaction_updates == 0

    assert repo.data["status"] == "succeeded"
    assert repo.data["providerDocumentId"] == "qb-1"


def test_outcome_repository_transitions_use_firestore_transactions():
    source = (
        ROOT
        / "masyg_extractor/integrations/accounting/"
          "shared/firestore_repository.py"
    ).read_text()

    finalizer_start = source.index(
        "    def finalize_reconciled_record("
    )

    finalizer_end = source.index(
        "    def finalize_record(",
        finalizer_start,
    )

    uncertain_start = source.index(
        "    def mark_record_uncertain("
    )

    uncertain_end = source.index(
        "    def release_record_claim(",
        uncertain_start,
    )

    release_start = uncertain_end

    release_end = source.index(
        "    # Specific transaction record methods",
        release_start,
    )

    finalizer = source[
        finalizer_start:finalizer_end
    ]

    uncertain = source[
        uncertain_start:uncertain_end
    ]

    release = source[
        release_start:release_end
    ]

    for block in (
        finalizer,
        uncertain,
        release,
    ):
        assert (
            "transaction = self.db.transaction()"
            in block
        )

        assert (
            "@firestore.transactional"
            in block
        )

        assert (
            "transaction=txn"
            in block
        )

        assert "self.store_document(" not in block

    assert "txn.update(" in finalizer
    assert "txn.update(" in uncertain
    assert "txn.delete(" in release

    assert (
        'current_status not in {'
        in finalizer
    )

    assert (
        'status not in {'
        in uncertain
    )

    assert (
        'data.get("status") != "sending"'
        in release
    )

def test_found_without_observed_generation_fails_closed():
    repo = FakeRepositoryOwner(
        durable(
            status="uncertain",
        )
    )

    evidence = ProviderLookupEvidence(
        outcome="found",
        provider_document_id="qb-1",
        provider_document_number="Inv-123",
        observed_claim_token=None,
    )

    assert apply(
        repo,
        evidence,
    ) is False

    assert repo.finalizer_calls == []
    assert repo.writes == []


def test_repository_reconciliation_rejects_stale_claim_generation():
    record = durable(
        status="uncertain",
    )
    record["claimToken"] = "generation-b"

    repo = FakeRepositoryOwner(record)

    before = dict(repo.data)

    result = (
        QuickBooksFirestoreService
        .finalize_reconciled_record(
            repo,
            "invoices",
            "group-1",
            "file-1",
            claim_token="generation-a",
            provider="quickbooks",
            intent="create_ar_invoice",
            provider_document_id="qb-1",
            provider_document_number="Inv-123",
        )
    )

    assert result is False
    assert repo.data == before
    assert repo.transaction_count == 1
    assert repo.transaction_reads == 1
    assert repo.transaction_updates == 0
    assert repo.writes == []


def test_repository_reconciliation_success_clears_claim_generation():
    repo = FakeRepositoryOwner(
        durable(
            status="uncertain",
        )
    )

    result = (
        QuickBooksFirestoreService
        .finalize_reconciled_record(
            repo,
            "invoices",
            "group-1",
            "file-1",
            claim_token="claim-1",
            provider="quickbooks",
            intent="create_ar_invoice",
            provider_document_id="qb-1",
            provider_document_number="Inv-123",
        )
    )

    assert result is True
    assert repo.data["status"] == "succeeded"
    assert repo.data["claimToken"] is None
    assert repo.transaction_updates == 1
