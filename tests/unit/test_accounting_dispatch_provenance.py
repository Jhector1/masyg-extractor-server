from __future__ import annotations

from pathlib import Path

import pytest

from masyg_extractor.integrations.accounting.shared import (
    firestore_repository as firestore_repository_module,
)
from masyg_extractor.integrations.accounting.shared.firestore_repository import (
    QuickBooksFirestoreService,
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
        *,
        transaction=None,
    ):
        return FakeSnapshot(
            self.owner.data
        )


class FakeTransaction:
    def update(
        self,
        doc_ref,
        payload,
    ):
        doc_ref.owner.store_document(
            doc_ref,
            payload,
            merge=True,
        )


class FakeDb:
    def __init__(self, owner):
        self.owner = owner

    def transaction(self):
        return FakeTransaction()


class FakeRepositoryOwner:
    def __init__(self, data):
        self.data = (
            None
            if data is None
            else dict(data)
        )
        self.writes = []
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


DEFAULT_CLAIM_TOKEN = "claim-1"


def _ensure_owned_claim(repo):
    if (
        repo.data is not None
        and str(
            repo.data.get("status") or ""
        ).strip().lower()
        == "sending"
    ):
        repo.data.setdefault(
            "claimToken",
            DEFAULT_CLAIM_TOKEN,
        )


def prepare(
    repo,
    number="INV-123",
    claim_token=DEFAULT_CLAIM_TOKEN,
):
    _ensure_owned_claim(repo)

    return QuickBooksFirestoreService.prepare_provider_dispatch(
        repo,
        "invoices",
        "group-1",
        "file-1",
        claim_token=claim_token,
        provider_document_number=number,
    )


def mark_started(
    repo,
    claim_token=DEFAULT_CLAIM_TOKEN,
):
    _ensure_owned_claim(repo)

    return QuickBooksFirestoreService.mark_provider_dispatch_started(
        repo,
        "invoices",
        "group-1",
        "file-1",
        claim_token=claim_token,
    )


def test_preparation_persists_both_canonical_and_compat_numbers():
    repo = FakeRepositoryOwner(
        {
            "status": "sending",
            "claimedAt": "2026-09-15T01:00:00Z",
        }
    )

    assert prepare(repo) is True

    assert repo.data["status"] == "sending"
    assert repo.data["docNumber"] == "INV-123"
    assert (
        repo.data["providerDocumentNumber"]
        == "INV-123"
    )
    assert repo.data["dispatchPreparedAt"]

    assert "providerDispatchStartedAt" not in repo.data

    assert len(repo.writes) == 1
    assert repo.writes[0][1] is True


def test_preparation_never_creates_missing_claim():
    repo = FakeRepositoryOwner(None)

    assert prepare(repo) is False
    assert repo.writes == []


@pytest.mark.parametrize(
    "status",
    (
        "none",
        "uncertain",
        "succeeded",
    ),
)
def test_preparation_only_enriches_sending_claim(status):
    repo = FakeRepositoryOwner(
        {
            "status": status,
        }
    )

    assert prepare(repo) is False
    assert repo.writes == []


def test_preparation_rejects_empty_provider_number():
    repo = FakeRepositoryOwner(
        {
            "status": "sending",
        }
    )

    with pytest.raises(
        ValueError,
        match="provider_document_number is required",
    ):
        prepare(repo, "   ")

    assert repo.writes == []


def test_preparation_never_replaces_conflicting_identity():
    repo = FakeRepositoryOwner(
        {
            "status": "sending",
            "providerDocumentNumber": "INV-OLD",
            "docNumber": "INV-OLD",
        }
    )

    assert prepare(
        repo,
        "INV-NEW",
    ) is False

    assert (
        repo.data["providerDocumentNumber"]
        == "INV-OLD"
    )
    assert repo.data["docNumber"] == "INV-OLD"
    assert repo.writes == []


def test_preparation_is_idempotent_for_same_identity():
    repo = FakeRepositoryOwner(
        {
            "status": "sending",
            "providerDocumentNumber": "INV-123",
            "docNumber": "INV-123",
            "dispatchPreparedAt":
                "2026-09-15T01:10:00Z",
        }
    )

    assert prepare(repo) is True

    assert (
        repo.data["dispatchPreparedAt"]
        == "2026-09-15T01:10:00Z"
    )


def test_dispatch_started_requires_durable_number():
    repo = FakeRepositoryOwner(
        {
            "status": "sending",
        }
    )

    assert mark_started(repo) is False
    assert repo.writes == []


def test_dispatch_started_requires_sending_claim():
    repo = FakeRepositoryOwner(
        {
            "status": "uncertain",
            "providerDocumentNumber": "INV-123",
        }
    )

    assert mark_started(repo) is False
    assert repo.writes == []


def test_dispatch_started_preserves_sending_status():
    repo = FakeRepositoryOwner(
        {
            "status": "sending",
            "providerDocumentNumber": "INV-123",
            "docNumber": "INV-123",
            "dispatchPreparedAt":
                "2026-09-15T01:10:00Z",
        }
    )

    assert mark_started(repo) is True

    assert repo.data["status"] == "sending"
    assert repo.data["providerDispatchStartedAt"]
    assert len(repo.writes) == 1
    assert repo.writes[0][1] is True


def test_dispatch_started_is_idempotent():
    repo = FakeRepositoryOwner(
        {
            "status": "sending",
            "providerDocumentNumber": "INV-123",
            "providerDispatchStartedAt":
                "2026-09-15T01:20:00Z",
        }
    )

    assert mark_started(repo) is True

    assert (
        repo.data["providerDispatchStartedAt"]
        == "2026-09-15T01:20:00Z"
    )
    assert repo.writes == []


def test_quickbooks_durable_order_precedes_provider_request():
    source = (
        ROOT
        / "masyg_extractor/integrations/accounting/"
          "quickbooks/services/document_service.py"
    ).read_text()

    claim = source.index(
        "self.repo.claim_record"
    )

    number = source.index(
        "doc_number = generate_doc_number(",
        claim,
    )

    prepare = source.index(
        "self.repo.prepare_provider_dispatch",
        number,
    )

    payload_number = source.index(
        '"DocNumber": doc_number',
        prepare,
    )

    durable_started = source.index(
        "self.repo.mark_provider_dispatch_started",
        payload_number,
    )

    memory_started = source.index(
        "provider_started_bids.update(",
        durable_started,
    )

    provider_request = source.index(
        "await self.client.request(",
        memory_started,
    )

    assert (
        claim
        < number
        < prepare
        < payload_number
        < durable_started
        < memory_started
        < provider_request
    )


def test_xero_durable_order_precedes_provider_request():
    source = (
        ROOT
        / "masyg_extractor/integrations/accounting/"
          "xero/services/document_service.py"
    ).read_text()

    claim = source.index(
        "self.repo.claim_record"
    )

    number = source.index(
        "doc_number = generate_doc_number(",
        claim,
    )

    prepare = source.index(
        "self.repo.prepare_provider_dispatch",
        number,
    )

    payload_number = source.index(
        '"InvoiceNumber": doc_number',
        prepare,
    )

    durable_started = source.index(
        "self.repo.mark_provider_dispatch_started",
        payload_number,
    )

    memory_started = source.index(
        "provider_started_transaction_ids.update(",
        durable_started,
    )

    provider_request = source.index(
        "xero_response = await self.client.request(",
        memory_started,
    )

    assert (
        claim
        < number
        < prepare
        < payload_number
        < durable_started
        < memory_started
        < provider_request
    )


def test_repository_provenance_methods_have_no_provider_io():
    source = (
        ROOT
        / "masyg_extractor/integrations/accounting/"
          "shared/firestore_repository.py"
    ).read_text()

    start = source.index(
        "    def prepare_provider_dispatch("
    )

    end = source.index(
        "    def finalize_record(",
        start,
    )

    provenance = source[start:end]

    forbidden = (
        "httpx",
        "requests.",
        ".request(",
        "QuickBooksClientAdapter",
        "XeroClientAdapter",
        "release_record_claim(",
        "finalize_record(",
    )

    for token in forbidden:
        assert token not in provenance


def test_repository_dispatch_provenance_writers_are_transactional():
    source = (
        ROOT
        / "masyg_extractor/integrations/accounting/"
          "shared/firestore_repository.py"
    ).read_text()

    prepare_start = source.index(
        "    def prepare_provider_dispatch("
    )

    prepare_end = source.index(
        "    def mark_provider_dispatch_started(",
        prepare_start,
    )

    started_start = prepare_end

    started_end = source.index(
        "    def finalize_reconciled_record(",
        started_start,
    )

    prepare = source[
        prepare_start:prepare_end
    ]

    started = source[
        started_start:started_end
    ]

    for block in (
        prepare,
        started,
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

        assert "txn.update(" in block
        assert "self.store_document(" not in block
        assert "snapshot = doc_ref.get()" not in block

    assert (
        'current.get("status") != "sending"'
        in prepare
    )

    assert (
        'current.get("status") != "sending"'
        in started
    )

    assert (
        '"providerDocumentNumber"'
        in prepare
    )

    assert (
        '"dispatchPreparedAt"'
        in prepare
    )

    assert (
        '"dispatchPreparedAt"'
        in started
    )

    assert (
        '"providerDispatchStartedAt"'
        in started
    )

def test_preparation_rejects_mismatched_claim_generation():
    repo = FakeRepositoryOwner(
        {
            "status": "sending",
            "claimToken": "generation-b",
        }
    )

    before = dict(repo.data)

    assert prepare(
        repo,
        claim_token="generation-a",
    ) is False

    assert repo.data == before
    assert repo.writes == []


def test_dispatch_started_rejects_mismatched_claim_generation():
    repo = FakeRepositoryOwner(
        {
            "status": "sending",
            "claimToken": "generation-b",
            "providerDocumentNumber": "INV-123",
            "docNumber": "INV-123",
            "dispatchPreparedAt":
                "2026-09-15T01:00:00Z",
        }
    )

    before = dict(repo.data)

    assert mark_started(
        repo,
        claim_token="generation-a",
    ) is False

    assert repo.data == before
    assert repo.writes == []


def test_provider_execution_lifecycle_calls_are_generation_bound():
    import ast
    from collections import Counter

    targets = {
        "claim_record",
        "prepare_provider_dispatch",
        "mark_provider_dispatch_started",
        "finalize_record",
        "mark_record_uncertain",
        "release_record_claim",
    }

    expected_counts = {
        "quickbooks": {
            "claim_record": 1,
            "prepare_provider_dispatch": 1,
            "mark_provider_dispatch_started": 1,
            "finalize_record": 1,
            "mark_record_uncertain": 3,
            "release_record_claim": 4,
        },
        "xero": {
            "claim_record": 1,
            "prepare_provider_dispatch": 1,
            "mark_provider_dispatch_started": 1,
            "finalize_record": 1,
            "mark_record_uncertain": 3,
            "release_record_claim": 5,
        },
    }

    for provider in (
        "quickbooks",
        "xero",
    ):
        service = (
            ROOT
            / "masyg_extractor/integrations/accounting"
            / provider
            / "services/document_service.py"
        )

        service_source = service.read_text()
        tree = ast.parse(service_source)

        lifecycle_calls = []

        for node in ast.walk(tree):
            if not isinstance(
                node,
                ast.Call,
            ):
                continue

            if not (
                isinstance(
                    node.func,
                    ast.Attribute,
                )
                and node.func.attr == "to_thread"
                and node.args
                and isinstance(
                    node.args[0],
                    ast.Attribute,
                )
                and node.args[0].attr
                in targets
            ):
                continue

            lifecycle_calls.append(
                node
            )

            keyword_names = {
                keyword.arg
                for keyword
                in node.keywords
            }

            assert (
                "claim_token"
                in keyword_names
            ), (
                provider,
                node.lineno,
                node.args[0].attr,
            )

        counts = Counter(
            node.args[0].attr
            for node in lifecycle_calls
        )

        assert (
            dict(counts)
            == expected_counts[provider]
        )


def test_claim_generation_stays_out_of_provider_payloads():
    import ast

    for (
        provider,
        token_map,
    ) in (
        (
            "quickbooks",
            "claim_tokens_by_bid",
        ),
        (
            "xero",
            "claim_tokens_by_transaction_id",
        ),
    ):
        service = (
            ROOT
            / "masyg_extractor/integrations/accounting"
            / provider
            / "services/document_service.py"
        )

        service_source = service.read_text()
        tree = ast.parse(service_source)

        assert "import uuid" in service_source
        assert (
            service_source.count(
                "uuid.uuid4().hex"
            )
            == 1
        )
        assert token_map in service_source

        # Durable field naming belongs to the repository only.
        assert '"claimToken"' not in service_source

        request_calls = []

        for node in ast.walk(tree):
            if not isinstance(
                node,
                ast.Call,
            ):
                continue

            if not (
                isinstance(
                    node.func,
                    ast.Attribute,
                )
                and node.func.attr == "request"
            ):
                continue

            request_calls.append(node)

        assert request_calls

        for call in request_calls:
            for keyword in call.keywords:
                if keyword.arg != "payload":
                    continue

                payload_source = (
                    ast.get_source_segment(
                        service_source,
                        keyword.value,
                    )
                    or ""
                )

                assert "claim_token" not in payload_source
                assert "claim_tokens_by_" not in payload_source


def test_provider_success_reporting_requires_durable_finalize():
    for provider in (
        "quickbooks",
        "xero",
    ):
        service = (
            ROOT
            / "masyg_extractor/integrations/accounting"
            / provider
            / "services/document_service.py"
        ).read_text()

        finalized = service.index(
            "finalized = await asyncio.to_thread("
        )

        guard = service.index(
            "if not finalized:",
            finalized,
        )

        success = service.index(
            "await operation_progress.succeeded(",
            guard,
        )

        assert finalized < guard < success
