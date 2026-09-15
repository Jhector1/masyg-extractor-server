from __future__ import annotations

from google.api_core.exceptions import AlreadyExists
import pytest

from masyg_extractor.integrations.accounting.shared import (
    firestore_repository as firestore_repository_module,
)
from masyg_extractor.integrations.accounting.shared.firestore_repository import (
    QuickBooksFirestoreService,
)


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
        self.exists = data is not None

    def to_dict(self):
        if self._data is None:
            return None
        return dict(self._data)


class FakeDocumentReference:
    def __init__(self):
        self.data = None
        self.path = "fake/accounting/record"

    def create(self, data):
        if self.data is not None:
            raise AlreadyExists("already exists")
        self.data = dict(data)

    def get(
        self,
        *args,
        **kwargs,
    ):
        return FakeSnapshot(self.data)

    def set(self, data, merge=False):
        if merge and self.data is not None:
            self.data.update(data)
        else:
            self.data = dict(data)

    def delete(self):
        self.data = None


class FakeTransaction:
    def update(
        self,
        reference,
        data,
    ):
        reference.set(
            data,
            merge=True,
        )

    def delete(
        self,
        reference,
    ):
        reference.delete()


class FakeDb:
    def transaction(self):
        return FakeTransaction()


def make_repository():
    repository = object.__new__(QuickBooksFirestoreService)
    repository.user_id = "user-1"
    repository.integration = "xero"
    repository.db = FakeDb()

    refs = {}

    def get_ref(record_type, group_id, transaction_id):
        key = (record_type, group_id, transaction_id)
        return refs.setdefault(key, FakeDocumentReference())

    repository._get_transaction_doc_ref = get_ref

    default_claim_token = "claim-1"

    for method_name in (
        "claim_record",
        "prepare_provider_dispatch",
        "mark_provider_dispatch_started",
        "finalize_record",
        "mark_record_uncertain",
        "release_record_claim",
    ):
        method = getattr(
            repository,
            method_name,
        )

        def with_claim_token(
            *args,
            _method=method,
            **kwargs,
        ):
            kwargs.setdefault(
                "claim_token",
                default_claim_token,
            )

            return _method(
                *args,
                **kwargs,
            )

        setattr(
            repository,
            method_name,
            with_claim_token,
        )

    return repository, refs


def test_claim_record_is_atomic_for_same_accounting_identity():
    repository, refs = make_repository()

    assert repository.claim_record(
        "bills",
        "group-1",
        "file-1",
        {"action": "create_ap_bill"},
    ) is True

    assert repository.claim_record(
        "bills",
        "group-1",
        "file-1",
        {"action": "create_ap_bill"},
    ) is False

    record = refs[("bills", "group-1", "file-1")].data

    assert record["status"] == "sending"
    assert record["integration"] == "xero"
    assert record["group_id"] == "group-1"
    assert record["transactionId"] == "file-1"
    assert record["action"] == "create_ap_bill"
    assert record["claimedAt"]
    assert record["claimToken"] == "claim-1"


def test_record_type_remains_part_of_accounting_identity():
    repository, _ = make_repository()

    assert repository.claim_record(
        "invoices",
        "group-1",
        "file-1",
    ) is True

    assert repository.claim_record(
        "bills",
        "group-1",
        "file-1",
    ) is True


def test_finalize_record_preserves_identity_and_marks_success():
    repository, _ = make_repository()

    assert repository.claim_record(
        "bills",
        "group-1",
        "file-1",
    ) is True

    repository.finalize_record(
        "bills",
        "group-1",
        "file-1",
        {
            "providerDocumentId": "xero-123",
            "docNumber": "BILL-42",
        },
    )

    record = repository.get_record(
        "bills",
        "group-1",
        "file-1",
    )

    assert record is not None
    assert record["status"] == "succeeded"
    assert record["integration"] == "xero"
    assert record["group_id"] == "group-1"
    assert record["transactionId"] == "file-1"
    assert record["providerDocumentId"] == "xero-123"
    assert record["docNumber"] == "BILL-42"
    assert record["completedAt"]
    assert record["claimToken"] is None


def test_release_only_removes_an_inflight_claim():
    repository, _ = make_repository()

    assert repository.claim_record(
        "invoices",
        "group-1",
        "file-1",
    ) is True

    assert repository.release_record_claim(
        "invoices",
        "group-1",
        "file-1",
    ) is True

    assert repository.get_record(
        "invoices",
        "group-1",
        "file-1",
    ) is None

    assert repository.claim_record(
        "invoices",
        "group-1",
        "file-1",
    ) is True

    repository.finalize_record(
        "invoices",
        "group-1",
        "file-1",
        {"providerDocumentId": "qb-123"},
    )

    assert repository.release_record_claim(
        "invoices",
        "group-1",
        "file-1",
    ) is False

    assert repository.get_record(
        "invoices",
        "group-1",
        "file-1",
    )["status"] == "succeeded"


def test_uncertain_provider_result_keeps_duplicate_barrier():
    repository, _ = make_repository()

    assert repository.claim_record(
        "bills",
        "group-1",
        "file-1",
    ) is True

    repository.mark_record_uncertain(
        "bills",
        "group-1",
        "file-1",
        error="provider timed out",
    )

    assert repository.claim_record(
        "bills",
        "group-1",
        "file-1",
    ) is False

    record = repository.get_record(
        "bills",
        "group-1",
        "file-1",
    )

    assert record["status"] == "uncertain"
    assert record["lastError"] == "provider timed out"

    # Never delete an ambiguous provider outcome automatically.
    assert repository.release_record_claim(
        "bills",
        "group-1",
        "file-1",
    ) is False


def test_prepare_provider_dispatch_is_atomic_and_idempotent():
    repository, _ = make_repository()

    assert repository.claim_record(
        "invoices",
        "group-1",
        "file-1",
    ) is True

    assert repository.prepare_provider_dispatch(
        "invoices",
        "group-1",
        "file-1",
        provider_document_number="Inv-123",
    ) is True

    first = repository.get_record(
        "invoices",
        "group-1",
        "file-1",
    )

    assert first["status"] == "sending"
    assert first["docNumber"] == "Inv-123"
    assert (
        first["providerDocumentNumber"]
        == "Inv-123"
    )
    assert first["dispatchPreparedAt"]

    prepared_at = first[
        "dispatchPreparedAt"
    ]

    assert repository.prepare_provider_dispatch(
        "invoices",
        "group-1",
        "file-1",
        provider_document_number="Inv-123",
    ) is True

    second = repository.get_record(
        "invoices",
        "group-1",
        "file-1",
    )

    assert (
        second["dispatchPreparedAt"]
        == prepared_at
    )


def test_prepare_provider_dispatch_rejects_conflicting_number():
    repository, _ = make_repository()

    assert repository.claim_record(
        "invoices",
        "group-1",
        "file-1",
    ) is True

    assert repository.prepare_provider_dispatch(
        "invoices",
        "group-1",
        "file-1",
        provider_document_number="Inv-123",
    ) is True

    assert repository.prepare_provider_dispatch(
        "invoices",
        "group-1",
        "file-1",
        provider_document_number="Inv-999",
    ) is False

    record = repository.get_record(
        "invoices",
        "group-1",
        "file-1",
    )

    assert record["docNumber"] == "Inv-123"
    assert (
        record["providerDocumentNumber"]
        == "Inv-123"
    )


def test_prepare_provider_dispatch_refuses_terminal_record():
    repository, _ = make_repository()

    assert repository.claim_record(
        "invoices",
        "group-1",
        "file-1",
    ) is True

    repository.finalize_record(
        "invoices",
        "group-1",
        "file-1",
        {
            "providerDocumentId": "qb-1",
        },
    )

    assert repository.prepare_provider_dispatch(
        "invoices",
        "group-1",
        "file-1",
        provider_document_number="Inv-123",
    ) is False

    record = repository.get_record(
        "invoices",
        "group-1",
        "file-1",
    )

    assert record["status"] == "succeeded"
    assert "dispatchPreparedAt" not in record


def test_mark_provider_dispatch_started_requires_prepare():
    repository, _ = make_repository()

    assert repository.claim_record(
        "invoices",
        "group-1",
        "file-1",
    ) is True

    assert repository.mark_provider_dispatch_started(
        "invoices",
        "group-1",
        "file-1",
    ) is False

    record = repository.get_record(
        "invoices",
        "group-1",
        "file-1",
    )

    assert (
        "providerDispatchStartedAt"
        not in record
    )


def test_mark_provider_dispatch_started_is_atomic_and_idempotent():
    repository, _ = make_repository()

    assert repository.claim_record(
        "invoices",
        "group-1",
        "file-1",
    ) is True

    assert repository.prepare_provider_dispatch(
        "invoices",
        "group-1",
        "file-1",
        provider_document_number="Inv-123",
    ) is True

    assert repository.mark_provider_dispatch_started(
        "invoices",
        "group-1",
        "file-1",
    ) is True

    first = repository.get_record(
        "invoices",
        "group-1",
        "file-1",
    )

    assert first["status"] == "sending"
    assert first["providerDispatchStartedAt"]

    started_at = first[
        "providerDispatchStartedAt"
    ]

    assert repository.mark_provider_dispatch_started(
        "invoices",
        "group-1",
        "file-1",
    ) is True

    second = repository.get_record(
        "invoices",
        "group-1",
        "file-1",
    )

    assert (
        second["providerDispatchStartedAt"]
        == started_at
    )


def test_mark_provider_dispatch_started_refuses_terminal_record():
    repository, _ = make_repository()

    assert repository.claim_record(
        "invoices",
        "group-1",
        "file-1",
    ) is True

    assert repository.prepare_provider_dispatch(
        "invoices",
        "group-1",
        "file-1",
        provider_document_number="Inv-123",
    ) is True

    repository.finalize_record(
        "invoices",
        "group-1",
        "file-1",
        {
            "providerDocumentId": "qb-1",
            "providerDocumentNumber":
                "Inv-123",
        },
    )

    assert repository.mark_provider_dispatch_started(
        "invoices",
        "group-1",
        "file-1",
    ) is False

    record = repository.get_record(
        "invoices",
        "group-1",
        "file-1",
    )

    assert record["status"] == "succeeded"
    assert (
        "providerDispatchStartedAt"
        not in record
    )

def test_claim_generation_blocks_stale_actor_after_reclaim():
    repository, _ = make_repository()

    assert repository.claim_record(
        "invoices",
        "group-1",
        "file-1",
        claim_token="generation-a",
    ) is True

    assert repository.release_record_claim(
        "invoices",
        "group-1",
        "file-1",
        claim_token="generation-a",
    ) is True

    assert repository.claim_record(
        "invoices",
        "group-1",
        "file-1",
        claim_token="generation-b",
    ) is True

    assert repository.prepare_provider_dispatch(
        "invoices",
        "group-1",
        "file-1",
        claim_token="generation-b",
        provider_document_number="Inv-B",
    ) is True

    assert repository.prepare_provider_dispatch(
        "invoices",
        "group-1",
        "file-1",
        claim_token="generation-a",
        provider_document_number="Inv-A",
    ) is False

    assert repository.mark_provider_dispatch_started(
        "invoices",
        "group-1",
        "file-1",
        claim_token="generation-a",
    ) is False

    assert repository.mark_record_uncertain(
        "invoices",
        "group-1",
        "file-1",
        claim_token="generation-a",
        error="late stale actor",
    ) is False

    assert repository.finalize_record(
        "invoices",
        "group-1",
        "file-1",
        {
            "providerDocumentId":
                "stale-provider-id",
        },
        claim_token="generation-a",
    ) is False

    assert repository.release_record_claim(
        "invoices",
        "group-1",
        "file-1",
        claim_token="generation-a",
    ) is False

    current = repository.get_record(
        "invoices",
        "group-1",
        "file-1",
    )

    assert current["status"] == "sending"
    assert current["claimToken"] == "generation-b"
    assert current["docNumber"] == "Inv-B"
    assert (
        current["providerDocumentNumber"]
        == "Inv-B"
    )

    assert "providerDocumentId" not in current
    assert "uncertainAt" not in current

    assert repository.mark_provider_dispatch_started(
        "invoices",
        "group-1",
        "file-1",
        claim_token="generation-b",
    ) is True


def test_tokenless_legacy_claim_fails_closed_for_actor_mutations():
    repository, refs = make_repository()

    ref = repository._get_transaction_doc_ref(
        "invoices",
        "group-1",
        "file-1",
    )

    ref.data = {
        "status": "sending",
        "integration": "xero",
        "group_id": "group-1",
        "transactionId": "file-1",
        "providerDocumentNumber": "Inv-1",
        "docNumber": "Inv-1",
        "dispatchPreparedAt":
            "2026-09-15T01:00:00Z",
    }

    assert repository.prepare_provider_dispatch(
        "invoices",
        "group-1",
        "file-1",
        claim_token="generation-a",
        provider_document_number="Inv-1",
    ) is False

    assert repository.mark_provider_dispatch_started(
        "invoices",
        "group-1",
        "file-1",
        claim_token="generation-a",
    ) is False

    assert repository.mark_record_uncertain(
        "invoices",
        "group-1",
        "file-1",
        claim_token="generation-a",
        error="late actor",
    ) is False

    assert repository.finalize_record(
        "invoices",
        "group-1",
        "file-1",
        {
            "providerDocumentId": "provider-1",
        },
        claim_token="generation-a",
    ) is False

    assert repository.release_record_claim(
        "invoices",
        "group-1",
        "file-1",
        claim_token="generation-a",
    ) is False

    current = refs[
        (
            "invoices",
            "group-1",
            "file-1",
        )
    ].data

    assert current["status"] == "sending"
    assert "claimToken" not in current
    assert "providerDispatchStartedAt" not in current
    assert "providerDocumentId" not in current
