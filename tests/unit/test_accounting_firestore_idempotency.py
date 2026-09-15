from __future__ import annotations

from google.api_core.exceptions import AlreadyExists

from masyg_extractor.integrations.accounting.shared.firestore_repository import (
    QuickBooksFirestoreService,
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

    def get(self):
        return FakeSnapshot(self.data)

    def set(self, data, merge=False):
        if merge and self.data is not None:
            self.data.update(data)
        else:
            self.data = dict(data)

    def delete(self):
        self.data = None


def make_repository():
    repository = object.__new__(QuickBooksFirestoreService)
    repository.user_id = "user-1"
    repository.integration = "xero"

    refs = {}

    def get_ref(record_type, group_id, transaction_id):
        key = (record_type, group_id, transaction_id)
        return refs.setdefault(key, FakeDocumentReference())

    repository._get_transaction_doc_ref = get_ref
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
