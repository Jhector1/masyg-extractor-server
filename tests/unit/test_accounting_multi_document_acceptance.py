import asyncio
import importlib.util
import sys
import types
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

from google.api_core.exceptions import AlreadyExists

from masyg_extractor.integrations.accounting.quickbooks.services import (
    document_service as quickbooks_document_module,
)
from masyg_extractor.integrations.accounting.quickbooks.services.document_service import (
    DocumentService as QuickBooksDocumentService,
)
from masyg_extractor.integrations.accounting.shared import (
    execution_bridge as bridge_module,
)
from masyg_extractor.integrations.accounting.shared import (
    firestore_repository as repository_module,
)
from masyg_extractor.integrations.accounting.shared import (
    operation_progress as operation_progress_module,
)
from masyg_extractor.integrations.accounting.shared import (
    status_router as router_module,
)
from masyg_extractor.integrations.accounting.shared.firestore_repository import (
    QuickBooksFirestoreService,
)


USER_ID = "user-1"
GROUP_ID = "group-1"

ROOT = Path(__file__).resolve().parents[2]

QUICKBOOKS_ROUTE_HELPER = (
    ROOT
    / "masyg_extractor"
    / "integrations"
    / "accounting"
    / "quickbooks"
    / "route_helper.py"
)

QUICKBOOKS_ADAPTER_MODULE = (
    "masyg_extractor.integrations.accounting."
    "quickbooks.adapter"
)


def load_real_quickbooks_document_factory():
    """
    Load the real canonical QuickBooks route_helper.py without
    importing its unused-at-materialization provider adapter.

    create_document itself does not use QuickBooksClientAdapter.
    """

    private_name = (
        "_masyg_a1_acceptance_"
        "quickbooks_route_helper"
    )

    original_adapter = sys.modules.get(
        QUICKBOOKS_ADAPTER_MODULE
    )

    original_private = sys.modules.get(
        private_name
    )

    adapter_stub = types.ModuleType(
        QUICKBOOKS_ADAPTER_MODULE
    )

    class InertQuickBooksClientAdapter:
        pass

    adapter_stub.QuickBooksClientAdapter = (
        InertQuickBooksClientAdapter
    )

    try:
        sys.modules[
            QUICKBOOKS_ADAPTER_MODULE
        ] = adapter_stub

        spec = (
            importlib.util
            .spec_from_file_location(
                private_name,
                QUICKBOOKS_ROUTE_HELPER,
            )
        )

        assert spec is not None
        assert spec.loader is not None

        module = (
            importlib.util
            .module_from_spec(spec)
        )

        sys.modules[
            private_name
        ] = module

        spec.loader.exec_module(module)

        factory = getattr(
            module,
            "create_document",
        )

        assert callable(factory)

        assert (
            Path(
                factory.__code__.co_filename
            ).resolve()
            == QUICKBOOKS_ROUTE_HELPER.resolve()
        )

        return factory

    finally:
        if original_adapter is None:
            sys.modules.pop(
                QUICKBOOKS_ADAPTER_MODULE,
                None,
            )
        else:
            sys.modules[
                QUICKBOOKS_ADAPTER_MODULE
            ] = original_adapter

        if original_private is None:
            sys.modules.pop(
                private_name,
                None,
            )
        else:
            sys.modules[
                private_name
            ] = original_private


def source_document(marker: str) -> dict:
    return {
        "documentType": "sales_invoice",
        "customer_name":
            f"Customer {marker}",
        "customer_email":
            f"{marker}@example.test",
        "date":
            "2026-09-15",
        "due_date":
            "2026-10-15",
        "invoice_number":
            marker,
        "line_items": [
            {
                "name":
                    f"Service {marker}",
                "item_name":
                    f"Service {marker}",
                "product_name":
                    f"Service {marker}",
                "description":
                    f"Service for {marker}",
                "quantity":
                    1,
                "unit_price":
                    100.0,
                "rate":
                    100.0,
                "amount":
                    100.0,
                "sku":
                    f"SKU-{marker}",
                "type":
                    "Service",
                "tax_code":
                    "NON",
                "account_code":
                    "4000",
            }
        ],
    }


def request():
    return SimpleNamespace(
        session={
            "client_id":
                "acceptance-client",
        },
        headers={},
    )


def contains_key(
    value,
    target: str,
) -> bool:
    if isinstance(
        value,
        dict,
    ):
        if target in value:
            return True

        return any(
            contains_key(
                item,
                target,
            )
            for item in value.values()
        )

    if isinstance(
        value,
        (
            list,
            tuple,
        ),
    ):
        return any(
            contains_key(
                item,
                target,
            )
            for item in value
        )

    return False


# ================================================================
# SOURCE-FILE FIRESTORE BOUNDARY
# ================================================================


class SourceSnapshot:
    def __init__(
        self,
        data,
    ):
        self._data = (
            None
            if data is None
            else deepcopy(data)
        )

        self.exists = (
            data is not None
        )

    def to_dict(self):
        if self._data is None:
            return None

        return deepcopy(
            self._data
        )


class SourceRef:
    def __init__(
        self,
        parts=(),
    ):
        self.parts = tuple(
            parts
        )

    def collection(
        self,
        name,
    ):
        return SourceRef(
            self.parts
            + (
                "collection",
                name,
            )
        )

    def document(
        self,
        name,
    ):
        return SourceRef(
            self.parts
            + (
                "document",
                name,
            )
        )


class SourceFirestoreClient:
    def collection(
        self,
        name,
    ):
        return SourceRef(
            (
                "collection",
                name,
            )
        )


# ================================================================
# TRANSACTION-CAPABLE DURABLE FIRESTORE BOUNDARY
# ================================================================


class DurableSnapshot:
    def __init__(
        self,
        data,
    ):
        self._data = data

        self.exists = (
            data is not None
        )

    def to_dict(self):
        if self._data is None:
            return None

        return dict(
            self._data
        )


class DurableDocumentReference:
    def __init__(
        self,
        store,
        key,
    ):
        self.store = store
        self.key = key
        self.path = "/".join(
            key
        )

    def create(
        self,
        data,
    ):
        if self.key in self.store:
            raise AlreadyExists(
                "already exists"
            )

        self.store[
            self.key
        ] = dict(
            data
        )

    def get(
        self,
        *args,
        **kwargs,
    ):
        return DurableSnapshot(
            self.store.get(
                self.key
            )
        )

    def set(
        self,
        data,
        merge=False,
    ):
        if (
            merge
            and self.key in self.store
        ):
            self.store[
                self.key
            ].update(
                dict(data)
            )
            return

        self.store[
            self.key
        ] = dict(
            data
        )

    def delete(
        self,
    ):
        self.store.pop(
            self.key,
            None,
        )


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
    def transaction(
        self,
    ):
        return FakeTransaction()


def make_shared_repository():
    repository = object.__new__(
        QuickBooksFirestoreService
    )

    repository.user_id = (
        USER_ID
    )

    repository.integration = (
        "quickbooks"
    )

    repository.db = FakeDb()

    store = {}
    refs = {}

    def get_ref(
        record_type,
        group_id,
        transaction_id,
    ):
        key = (
            record_type,
            group_id,
            transaction_id,
        )

        if key not in refs:
            refs[key] = (
                DurableDocumentReference(
                    store,
                    key,
                )
            )

        return refs[key]

    repository._get_transaction_doc_ref = (
        get_ref
    )

    repository.get_integration_token = (
        lambda:
            "fake-quickbooks-token"
    )

    return (
        repository,
        store,
        refs,
    )


# ================================================================
# PROVIDER AUXILIARY PREPARATION BOUNDARIES
# ================================================================


def with_entity_id(
    entity,
    value,
):
    try:
        entity.id = value
        return entity
    except Exception:
        pass

    model_copy = getattr(
        entity,
        "model_copy",
        None,
    )

    if callable(
        model_copy
    ):
        return model_copy(
            update={
                "id":
                    value,
            }
        )

    object.__setattr__(
        entity,
        "id",
        value,
    )

    return entity


class FakeCustomerService:
    def __init__(
        self,
        *_args,
        **_kwargs,
    ):
        pass

    async def create_customer_in_bulk(
        self,
        customers,
    ):
        return {
            key:
                with_entity_id(
                    customer,
                    (
                        "customer-"
                        + key
                    ),
                )
            for (
                key,
                customer,
            ) in customers.items()
        }


class FakeItemService:
    def __init__(
        self,
        *_args,
        **_kwargs,
    ):
        pass

    async def create_item_in_bulk(
        self,
        items,
    ):
        return {
            key: [
                with_entity_id(
                    item,
                    (
                        f"item-{key}-"
                        f"{index}"
                    ),
                )
                for (
                    index,
                    item,
                ) in enumerate(
                    item_list,
                    start=1,
                )
            ]
            for (
                key,
                item_list,
            ) in items.items()
        }


class FakeEntityHelper:
    def __init__(
        self,
        *_args,
        **_kwargs,
    ):
        pass


class FakeAuditLogService:
    def __init__(
        self,
        *_args,
        **_kwargs,
    ):
        pass

    def __getattr__(
        self,
        _name,
    ):
        def noop(
            *_args,
            **_kwargs,
        ):
            return None

        return noop


# ================================================================
# PROGRESS / SOCKET BOUNDARIES
# ================================================================


class FakeProgressLogger:
    CREATING_DOCUMENTS_WEIGHT = (
        50.0
    )

    def __init__(
        self,
        *_args,
        **_kwargs,
    ):
        pass

    @staticmethod
    def get_file_progress_dict():
        return {
            "creating_item":
                0.0,
            "creating_customer":
                0.0,
            "creating_invoice":
                0.0,
        }

    def calculate_overall_progress(
        self,
        _progress,
    ):
        return 0.0

    async def safe_emit_progress(
        self,
        _progress,
    ):
        return 0.0


class FakeSocket:
    async def emit(
        self,
        *_args,
        **_kwargs,
    ):
        return None


async def noop_log(
    *_args,
    **_kwargs,
):
    return None


# ================================================================
# FAKE PROVIDER HTTP
# ================================================================


class ProviderState:
    def __init__(
        self,
    ):
        self.posts = []
        self.gets = []

        self.ambiguous_number = (
            None
        )

        self.lookup_modes = {}


class FakeQuickBooksClient:
    def __init__(
        self,
        context,
        state,
    ):
        self.context = context
        self.state = state

    async def request(
        self,
        *args,
        **kwargs,
    ):
        method = str(
            kwargs.get(
                "method"
            )
            or ""
        ).upper()

        endpoint = kwargs.get(
            "endpoint"
        )

        if (
            endpoint is None
            and len(args) >= 2
        ):
            endpoint = args[1]

        if method == "POST":
            assert (
                endpoint
                == "batch"
            )

            payload = deepcopy(
                kwargs.get(
                    "payload"
                )
                or {}
            )

            assert not contains_key(
                payload,
                "claimToken",
            )

            assert not contains_key(
                payload,
                "claim_token",
            )

            requests = payload.get(
                "BatchItemRequest"
            )

            assert isinstance(
                requests,
                list,
            )

            assert (
                len(requests)
                == 3
            )

            self.state.posts.append(
                {
                    "endpoint":
                        endpoint,
                    "payload":
                        payload,
                }
            )

            success = requests[0]
            rejected = requests[1]
            ambiguous = requests[2]

            success_number = str(
                success[
                    "Invoice"
                ][
                    "DocNumber"
                ]
            )

            self.state.ambiguous_number = (
                str(
                    ambiguous[
                        "Invoice"
                    ][
                        "DocNumber"
                    ]
                )
            )

            return {
                "BatchItemResponse": [
                    {
                        "bId":
                            success[
                                "bId"
                            ],
                        "Invoice": {
                            "Id":
                                "qb-created-a",
                            "DocNumber":
                                success_number,
                        },
                    },
                    {
                        "bId":
                            rejected[
                                "bId"
                            ],
                        "Fault": {
                            "Error": [
                                {
                                    "Message":
                                        "Validation error",
                                    "Detail":
                                        (
                                            "Explicit provider "
                                            "rejection"
                                        ),
                                }
                            ]
                        },
                    },
                    # The third document deliberately has no
                    # BatchItemResponse row. Its provider dispatch
                    # started, so this is an ambiguous outcome.
                ]
            }

        if method == "GET":
            params = deepcopy(
                kwargs.get(
                    "params"
                )
                or {}
            )

            assert not contains_key(
                params,
                "claimToken",
            )

            assert not contains_key(
                params,
                "claim_token",
            )

            self.state.gets.append(
                {
                    "endpoint":
                        endpoint,
                    "params":
                        params,
                }
            )

            query = str(
                params.get(
                    "query"
                )
                or ""
            )

            number = None
            mode = None

            for (
                candidate_number,
                candidate_mode,
            ) in (
                self.state
                .lookup_modes
                .items()
            ):
                if (
                    candidate_number
                    in query
                ):
                    number = (
                        candidate_number
                    )
                    mode = (
                        candidate_mode
                    )
                    break

            if number is None:
                number = (
                    self.state
                    .ambiguous_number
                )

                assert number

                assert (
                    number
                    in query
                )

                mode = "found"

            if mode == "found":
                return {
                    "QueryResponse": {
                        "Invoice": [
                            {
                                "Id":
                                    "qb-reconciled-c",
                                "DocNumber":
                                    number,
                            }
                        ]
                    }
                }

            if mode == "absent":
                return {
                    "QueryResponse": {
                        "Invoice": [],
                    }
                }

            if mode == "indeterminate":
                return {
                    "QueryResponse": {},
                }

            raise AssertionError(
                (
                    "Unsupported fake lookup "
                    f"mode={mode!r}"
                )
            )

        raise AssertionError(
            (
                "Unexpected provider call: "
                f"method={method!r} "
                f"endpoint={endpoint!r}"
            )
        )


# ================================================================
# TEST ENVIRONMENT
# ================================================================


def install_environment(
    monkeypatch,
    *,
    sources,
    repo,
    provider_state,
):
    source_client = (
        SourceFirestoreClient()
    )

    real_document_factory = (
        load_real_quickbooks_document_factory()
    )

    assert (
        Path(
            real_document_factory
            .__code__
            .co_filename
        ).resolve()
        == (
            QUICKBOOKS_ROUTE_HELPER
            .resolve()
        )
    )

    async def fake_get_firestore_client():
        return source_client

    async def fake_document_get(
        ref,
    ):
        parts = ref.parts

        groups_index = (
            parts.index(
                "groups"
            )
        )

        files_index = (
            parts.index(
                "files"
            )
        )

        group_id = (
            parts[
                groups_index + 2
            ]
        )

        file_id = (
            parts[
                files_index + 2
            ]
        )

        return SourceSnapshot(
            sources.get(
                (
                    group_id,
                    file_id,
                )
            )
        )

    monkeypatch.setattr(
        router_module,
        "get_firestore_client",
        fake_get_firestore_client,
    )

    monkeypatch.setattr(
        router_module,
        "document_get",
        fake_document_get,
    )

    monkeypatch.setattr(
        router_module,
        "QuickBooksFirestoreService",
        lambda **_kwargs:
            repo,
    )

    runtime = (
        bridge_module
        .AccountingExecutionRuntime(
            repo_factory=(
                lambda **_kwargs:
                    repo
            ),
            client_factory=(
                lambda context:
                    FakeQuickBooksClient(
                        context,
                        provider_state,
                    )
            ),
            service_factory=(
                QuickBooksDocumentService
            ),
            document_factory=(
                real_document_factory
            ),
        )
    )

    def load_runtime(
        provider,
    ):
        assert (
            provider
            == "quickbooks"
        )

        return runtime

    monkeypatch.setattr(
        bridge_module,
        "_load_execution_runtime",
        load_runtime,
    )

    monkeypatch.setattr(
        bridge_module,
        "IntegrationsProgressLog",
        FakeProgressLogger,
    )

    monkeypatch.setattr(
        bridge_module,
        "LogManager",
        lambda:
            object(),
    )

    monkeypatch.setattr(
        quickbooks_document_module,
        "CustomerService",
        FakeCustomerService,
    )

    monkeypatch.setattr(
        quickbooks_document_module,
        "ItemService",
        FakeItemService,
    )

    monkeypatch.setattr(
        quickbooks_document_module,
        "EntityHelper",
        FakeEntityHelper,
    )

    monkeypatch.setattr(
        quickbooks_document_module,
        "AuditLogService",
        FakeAuditLogService,
    )

    monkeypatch.setattr(
        QuickBooksDocumentService,
        "_log",
        noop_log,
    )

    fake_socket = (
        FakeSocket()
    )

    monkeypatch.setattr(
        quickbooks_document_module,
        "sio",
        fake_socket,
    )

    monkeypatch.setattr(
        operation_progress_module,
        "sio",
        fake_socket,
    )

    # Preserve every real repository mutation body, but execute the
    # Firestore transaction decorator against our fake transaction.
    monkeypatch.setattr(
        repository_module.firestore,
        "transactional",
        lambda fn:
            fn,
    )

    snapshots = getattr(
        operation_progress_module,
        "_ACCOUNTING_OPERATION_SNAPSHOTS",
        None,
    )

    if hasattr(
        snapshots,
        "clear",
    ):
        snapshots.clear()


def seed_uncertain_claim(
    repo,
    *,
    file_id,
    number,
    token,
):
    claimed = (
        repo.claim_record(
            "invoices",
            GROUP_ID,
            file_id,
            {
                "integration":
                    "quickbooks",
                "action":
                    "create_ar_invoice",
                "group_id":
                    GROUP_ID,
                "transactionId":
                    file_id,
            },
            claim_token=token,
        )
    )

    assert claimed is True

    prepared = (
        repo.prepare_provider_dispatch(
            "invoices",
            GROUP_ID,
            file_id,
            claim_token=token,
            provider_document_number=(
                number
            ),
        )
    )

    assert prepared is True

    started = (
        repo.mark_provider_dispatch_started(
            "invoices",
            GROUP_ID,
            file_id,
            claim_token=token,
        )
    )

    assert started is True

    uncertain = (
        repo.mark_record_uncertain(
            "invoices",
            GROUP_ID,
            file_id,
            claim_token=token,
            error=(
                "Ambiguous provider "
                "outcome."
            ),
        )
    )

    assert uncertain is True


def forbid_verify_mutations(
    repo,
):
    def forbidden(
        *args,
        **kwargs,
    ):
        raise AssertionError(
            (
                "Verify non-found outcome "
                "must not mutate durable state"
            )
        )

    for method_name in (
        "claim_record",
        "prepare_provider_dispatch",
        "mark_provider_dispatch_started",
        "finalize_record",
        "mark_record_uncertain",
        "release_record_claim",
        "finalize_reconciled_record",
    ):
        setattr(
            repo,
            method_name,
            forbidden,
        )


def run(
    coroutine,
):
    return asyncio.run(
        coroutine
    )


def preflight_one(
    file_id,
):
    return (
        router_module
        .post_accounting_batch_preflight(
            {
                "provider":
                    "quickbooks",
                "documents": [
                    {
                        "group_id":
                            GROUP_ID,
                        "file_id":
                            file_id,
                    }
                ],
            },
            {
                "userId":
                    USER_ID,
            },
        )
    )


def verify_one(
    file_id,
):
    return (
        router_module
        .post_accounting_verify_status(
            request(),
            {
                "provider":
                    "quickbooks",
                "group_id":
                    GROUP_ID,
                "file_id":
                    file_id,
            },
            {
                "userId":
                    USER_ID,
            },
        )
    )


# ================================================================
# MIXED BATCH -> VERIFY FOUND
# ================================================================


def test_quickbooks_mixed_batch_then_verify_found(
    monkeypatch,
):
    sources = {
        (
            GROUP_ID,
            "file-a",
        ):
            source_document(
                "A"
            ),
        (
            GROUP_ID,
            "file-b",
        ):
            source_document(
                "B"
            ),
        (
            GROUP_ID,
            "file-c",
        ):
            source_document(
                "C"
            ),
    }

    (
        repo,
        _store,
        _refs,
    ) = (
        make_shared_repository()
    )

    provider_state = (
        ProviderState()
    )

    install_environment(
        monkeypatch,
        sources=sources,
        repo=repo,
        provider_state=(
            provider_state
        ),
    )

    execution = run(
        router_module
        .post_accounting_execution(
            request(),
            {
                "confirmed":
                    True,
                "provider":
                    "quickbooks",
                "documents": [
                    {
                        "group_id":
                            GROUP_ID,
                        "file_id":
                            "file-a",
                    },
                    {
                        "group_id":
                            GROUP_ID,
                        "file_id":
                            "file-b",
                    },
                    {
                        "group_id":
                            GROUP_ID,
                        "file_id":
                            "file-c",
                    },
                ],
            },
            {
                "userId":
                    USER_ID,
            },
        )
    )

    # ------------------------------------------------------------
    # Canonical execution orchestration
    # ------------------------------------------------------------

    assert (
        execution[
            "provider"
        ]
        == "quickbooks"
    )

    assert (
        execution[
            "confirmed"
        ]
        is True
    )

    assert (
        execution[
            "runtime_blocked"
        ]
        == []
    )

    assert (
        execution[
            "plan"
        ][
            "selected"
        ]
        == 3
    )

    assert (
        execution[
            "plan"
        ][
            "executable"
        ]
        == 3
    )

    assert (
        len(
            execution[
                "executions"
            ]
        )
        == 1
    )

    entry = (
        execution[
            "executions"
        ][0]
    )

    assert (
        entry[
            "accounting_intent"
        ]
        == "create_ar_invoice"
    )

    assert (
        entry[
            "status"
        ]
        == "completed"
    )

    assert (
        entry[
            "outcome"
        ]
        == {
            "total":
                3,
            "completed":
                3,
            "succeeded":
                1,
            "failed":
                2,
        }
    )

    assert not contains_key(
        execution,
        "claimToken",
    )

    assert not contains_key(
        execution,
        "claim_token",
    )

    # ------------------------------------------------------------
    # Provider POST capability safety
    # ------------------------------------------------------------

    assert (
        len(
            provider_state.posts
        )
        == 1
    )

    provider_post = (
        provider_state.posts[0]
    )

    assert not contains_key(
        provider_post[
            "payload"
        ],
        "claimToken",
    )

    assert not contains_key(
        provider_post[
            "payload"
        ],
        "claim_token",
    )

    # ------------------------------------------------------------
    # A = successful remote create -> durable success
    # ------------------------------------------------------------

    record_a = (
        repo.get_record(
            "invoices",
            GROUP_ID,
            "file-a",
        )
    )

    assert (
        record_a
        is not None
    )

    assert (
        record_a[
            "status"
        ]
        == "succeeded"
    )

    assert (
        record_a.get(
            "claimToken"
        )
        is None
    )

    # ------------------------------------------------------------
    # B = explicit provider rejection -> exact claim released
    # ------------------------------------------------------------

    record_b = (
        repo.get_record(
            "invoices",
            GROUP_ID,
            "file-b",
        )
    )

    assert (
        record_b
        is None
    )

    # ------------------------------------------------------------
    # C = provider started but omitted response -> uncertain
    # ------------------------------------------------------------

    record_c = (
        repo.get_record(
            "invoices",
            GROUP_ID,
            "file-c",
        )
    )

    assert (
        record_c
        is not None
    )

    assert (
        record_c[
            "status"
        ]
        == "uncertain"
    )

    c_generation = str(
        record_c.get(
            "claimToken"
        )
        or ""
    )

    assert c_generation

    assert (
        str(
            record_c.get(
                "providerDocumentNumber"
            )
            or ""
        )
        == (
            provider_state
            .ambiguous_number
        )
    )

    # ------------------------------------------------------------
    # Isolation / retry semantics
    # ------------------------------------------------------------

    preflight_b = run(
        preflight_one(
            "file-b"
        )
    )

    assert (
        preflight_b[
            "documents"
        ][0][
            "state"
        ]
        == "ready"
    )

    preflight_c = run(
        preflight_one(
            "file-c"
        )
    )

    assert (
        preflight_c[
            "documents"
        ][0][
            "state"
        ]
        == "uncertain"
    )

    assert not contains_key(
        preflight_c,
        "claimToken",
    )

    before_verify_a = (
        deepcopy(
            repo.get_record(
                "invoices",
                GROUP_ID,
                "file-a",
            )
        )
    )

    # ------------------------------------------------------------
    # Verify C -> provider GET FOUND -> same-generation CAS
    # ------------------------------------------------------------

    verify_result = run(
        verify_one(
            "file-c"
        )
    )

    assert (
        verify_result[
            "disposition"
        ]
        == "already_created"
    )

    assert (
        verify_result[
            "lookup_outcome"
        ]
        == "found"
    )

    assert (
        verify_result[
            "reconciled"
        ]
        is True
    )

    assert not contains_key(
        verify_result,
        "claimToken",
    )

    assert not contains_key(
        verify_result,
        "claim_token",
    )

    assert (
        len(
            provider_state.gets
        )
        == 1
    )

    provider_get = (
        provider_state.gets[0]
    )

    assert not contains_key(
        provider_get[
            "params"
        ],
        "claimToken",
    )

    assert not contains_key(
        provider_get[
            "params"
        ],
        "claim_token",
    )

    record_c_after = (
        repo.get_record(
            "invoices",
            GROUP_ID,
            "file-c",
        )
    )

    assert (
        record_c_after
        is not None
    )

    assert (
        record_c_after[
            "status"
        ]
        == "succeeded"
    )

    assert (
        record_c_after.get(
            "claimToken"
        )
        is None
    )

    assert (
        record_c_after[
            "providerDocumentId"
        ]
        == "qb-reconciled-c"
    )

    # Verify C may not change A.
    assert (
        repo.get_record(
            "invoices",
            GROUP_ID,
            "file-a",
        )
        == before_verify_a
    )

    # Verify C may not resurrect/reclaim B.
    assert (
        repo.get_record(
            "invoices",
            GROUP_ID,
            "file-b",
        )
        is None
    )

    # The original ambiguous generation was consumed only by C.
    assert c_generation


def test_quickbooks_verify_absent_preserves_uncertain_claim(
    monkeypatch,
):
    file_id = (
        "file-verify-absent"
    )

    number = (
        "Inv-VERIFY-ABSENT"
    )

    token = (
        "claim-verify-absent"
    )

    sources = {
        (
            GROUP_ID,
            file_id,
        ):
            source_document(
                "VERIFY-ABSENT"
            ),
    }

    (
        repo,
        _store,
        _refs,
    ) = (
        make_shared_repository()
    )

    provider_state = (
        ProviderState()
    )

    install_environment(
        monkeypatch,
        sources=sources,
        repo=repo,
        provider_state=(
            provider_state
        ),
    )

    seed_uncertain_claim(
        repo,
        file_id=file_id,
        number=number,
        token=token,
    )

    provider_state.lookup_modes[
        number
    ] = "absent"

    before = deepcopy(
        repo.get_record(
            "invoices",
            GROUP_ID,
            file_id,
        )
    )

    assert before is not None
    assert (
        before["status"]
        == "uncertain"
    )
    assert (
        before["claimToken"]
        == token
    )

    # From this point onward any durable mutation is forbidden.
    # ABSENT is evidence only. It is not release/retry authority.
    forbid_verify_mutations(
        repo
    )

    result = run(
        verify_one(
            file_id
        )
    )

    after = (
        repo.get_record(
            "invoices",
            GROUP_ID,
            file_id,
        )
    )

    assert (
        result[
            "disposition"
        ]
        == "needs_verification"
    )

    assert (
        result[
            "lookup_outcome"
        ]
        == "absent"
    )

    assert (
        result[
            "reconciled"
        ]
        is False
    )

    assert (
        after
        == before
    )

    assert (
        after["status"]
        == "uncertain"
    )

    assert (
        after["claimToken"]
        == token
    )

    # Verify may read the provider, but may not create/retry.
    assert (
        provider_state.posts
        == []
    )

    assert (
        len(
            provider_state.gets
        )
        == 1
    )

    assert not contains_key(
        provider_state.gets[0][
            "params"
        ],
        "claimToken",
    )

    assert not contains_key(
        provider_state.gets[0][
            "params"
        ],
        "claim_token",
    )

    assert not contains_key(
        result,
        "claimToken",
    )

    assert not contains_key(
        result,
        "claim_token",
    )

    # The exact uncertain generation remains duplicate-blocked.
    preflight = run(
        preflight_one(
            file_id
        )
    )

    assert (
        preflight[
            "documents"
        ][0][
            "state"
        ]
        == "uncertain"
    )

    assert not contains_key(
        preflight,
        "claimToken",
    )

    assert not contains_key(
        preflight,
        "claim_token",
    )


def test_quickbooks_verify_indeterminate_preserves_uncertain_claim(
    monkeypatch,
):
    file_id = (
        "file-verify-indeterminate"
    )

    number = (
        "Inv-VERIFY-INDETERMINATE"
    )

    token = (
        "claim-verify-indeterminate"
    )

    sources = {
        (
            GROUP_ID,
            file_id,
        ):
            source_document(
                "VERIFY-INDETERMINATE"
            ),
    }

    (
        repo,
        _store,
        _refs,
    ) = (
        make_shared_repository()
    )

    provider_state = (
        ProviderState()
    )

    install_environment(
        monkeypatch,
        sources=sources,
        repo=repo,
        provider_state=(
            provider_state
        ),
    )

    seed_uncertain_claim(
        repo,
        file_id=file_id,
        number=number,
        token=token,
    )

    provider_state.lookup_modes[
        number
    ] = "indeterminate"

    before = deepcopy(
        repo.get_record(
            "invoices",
            GROUP_ID,
            file_id,
        )
    )

    assert before is not None
    assert (
        before["status"]
        == "uncertain"
    )
    assert (
        before["claimToken"]
        == token
    )

    # INDETERMINATE carries even less authority than ABSENT:
    # no release, retry, recreate, or finalization is allowed.
    forbid_verify_mutations(
        repo
    )

    result = run(
        verify_one(
            file_id
        )
    )

    after = (
        repo.get_record(
            "invoices",
            GROUP_ID,
            file_id,
        )
    )

    assert (
        result[
            "disposition"
        ]
        == "needs_verification"
    )

    assert (
        result[
            "lookup_outcome"
        ]
        == "indeterminate"
    )

    assert (
        result[
            "reconciled"
        ]
        is False
    )

    assert (
        after
        == before
    )

    assert (
        after["status"]
        == "uncertain"
    )

    assert (
        after["claimToken"]
        == token
    )

    assert (
        provider_state.posts
        == []
    )

    assert (
        len(
            provider_state.gets
        )
        == 1
    )

    assert not contains_key(
        provider_state.gets[0][
            "params"
        ],
        "claimToken",
    )

    assert not contains_key(
        provider_state.gets[0][
            "params"
        ],
        "claim_token",
    )

    assert not contains_key(
        result,
        "claimToken",
    )

    assert not contains_key(
        result,
        "claim_token",
    )

    preflight = run(
        preflight_one(
            file_id
        )
    )

    assert (
        preflight[
            "documents"
        ][0][
            "state"
        ]
        == "uncertain"
    )

    assert not contains_key(
        preflight,
        "claimToken",
    )

    assert not contains_key(
        preflight,
        "claim_token",
    )


# ================================================================
# XERO ACCEPTANCE BOUNDARY
# ================================================================


XERO_ROUTE_HELPER = (
    ROOT
    / "masyg_extractor"
    / "integrations"
    / "accounting"
    / "xero"
    / "route_helper.py"
)

XERO_ADAPTER_MODULE = (
    "masyg_extractor.integrations.accounting."
    "xero.adapter"
)


def load_real_xero_document_factory():
    """
    Load the real canonical Xero route_helper.py while replacing only
    its unused-at-materialization module-level XeroClientAdapter import.
    """

    private_name = (
        "_masyg_a1_acceptance_"
        "xero_route_helper"
    )

    original_adapter = sys.modules.get(
        XERO_ADAPTER_MODULE
    )

    original_private = sys.modules.get(
        private_name
    )

    adapter_stub = types.ModuleType(
        XERO_ADAPTER_MODULE
    )

    class InertXeroClientAdapter:
        pass

    adapter_stub.XeroClientAdapter = (
        InertXeroClientAdapter
    )

    try:
        sys.modules[
            XERO_ADAPTER_MODULE
        ] = adapter_stub

        spec = (
            importlib.util
            .spec_from_file_location(
                private_name,
                XERO_ROUTE_HELPER,
            )
        )

        assert spec is not None
        assert spec.loader is not None

        module = (
            importlib.util
            .module_from_spec(spec)
        )

        sys.modules[
            private_name
        ] = module

        spec.loader.exec_module(
            module
        )

        factory = getattr(
            module,
            "create_document",
        )

        assert callable(factory)

        assert (
            Path(
                factory
                .__code__
                .co_filename
            ).resolve()
            == XERO_ROUTE_HELPER.resolve()
        )

        return factory

    finally:
        if original_adapter is None:
            sys.modules.pop(
                XERO_ADAPTER_MODULE,
                None,
            )
        else:
            sys.modules[
                XERO_ADAPTER_MODULE
            ] = original_adapter

        if original_private is None:
            sys.modules.pop(
                private_name,
                None,
            )
        else:
            sys.modules[
                private_name
            ] = original_private


class XeroProviderState:
    def __init__(
        self,
    ):
        self.posts = []
        self.gets = []

        self.ambiguous_number = (
            None
        )

        self.lookup_modes = {}


class FakeXeroClient:
    def __init__(
        self,
        context,
        state,
    ):
        self.context = context
        self.state = state

    async def request(
        self,
        *args,
        **kwargs,
    ):
        method = str(
            kwargs.get(
                "method"
            )
            or ""
        ).upper()

        endpoint = kwargs.get(
            "endpoint"
        )

        if (
            endpoint is None
            and len(args) >= 2
        ):
            endpoint = args[1]

        if method == "POST":
            assert (
                endpoint
                == "Invoices"
            )

            payload = deepcopy(
                kwargs.get(
                    "payload"
                )
                or {}
            )

            assert not contains_key(
                payload,
                "claimToken",
            )

            assert not contains_key(
                payload,
                "claim_token",
            )

            invoices = payload.get(
                "Invoices"
            )

            assert isinstance(
                invoices,
                list,
            )

            assert (
                len(invoices)
                == 3
            )

            for invoice in invoices:
                assert (
                    invoice["Type"]
                    == "ACCREC"
                )

            self.state.posts.append(
                {
                    "endpoint":
                        endpoint,
                    "payload":
                        payload,
                }
            )

            success = invoices[0]
            rejected = invoices[1]
            ambiguous = invoices[2]

            success_number = str(
                success[
                    "InvoiceNumber"
                ]
            )

            rejected_number = str(
                rejected[
                    "InvoiceNumber"
                ]
            )

            self.state.ambiguous_number = (
                str(
                    ambiguous[
                        "InvoiceNumber"
                    ]
                )
            )

            return {
                "Invoices": [
                    {
                        "InvoiceID":
                            "xe-created-a",
                        "InvoiceNumber":
                            success_number,
                        "Type":
                            "ACCREC",
                        "HasErrors":
                            False,
                        "ValidationErrors":
                            [],
                    },
                    {
                        "InvoiceNumber":
                            rejected_number,
                        "Type":
                            "ACCREC",
                        "HasErrors":
                            True,
                        "ValidationErrors": [
                            {
                                "Message":
                                    (
                                        "Explicit provider "
                                        "rejection"
                                    ),
                            }
                        ],
                    },
                    # Intentionally omit C. Its Xero dispatch has
                    # already started, so the missing result is
                    # ambiguous and must preserve its durable claim.
                ]
            }

        if method == "GET":
            params = deepcopy(
                kwargs.get(
                    "params"
                )
                or {}
            )

            payload = kwargs.get(
                "payload"
            )

            assert (
                endpoint
                == "Invoices"
            )

            assert (
                payload is None
            )

            assert not contains_key(
                params,
                "claimToken",
            )

            assert not contains_key(
                params,
                "claim_token",
            )

            self.state.gets.append(
                {
                    "endpoint":
                        endpoint,
                    "params":
                        params,
                }
            )

            where = str(
                params.get(
                    "where"
                )
                or ""
            )

            number = None
            mode = None

            for (
                candidate_number,
                candidate_mode,
            ) in (
                self.state
                .lookup_modes
                .items()
            ):
                if (
                    candidate_number
                    in where
                ):
                    number = (
                        candidate_number
                    )
                    mode = (
                        candidate_mode
                    )
                    break

            if number is None:
                number = (
                    self.state
                    .ambiguous_number
                )

                assert number

                mode = "found"

            assert params == {
                "where":
                    (
                        'InvoiceNumber=="'
                        + number
                        + '"'
                    ),
            }

            if mode == "found":
                return {
                    "Invoices": [
                        {
                            "InvoiceID":
                                "xe-reconciled-c",
                            "InvoiceNumber":
                                number,
                            "Type":
                                "ACCREC",
                        }
                    ]
                }

            if mode == "absent":
                return {
                    "Invoices": [],
                }

            if mode == "indeterminate":
                return {}

            raise AssertionError(
                (
                    "Unsupported Xero lookup "
                    f"mode={mode!r}"
                )
            )

        raise AssertionError(
            (
                "Unexpected Xero provider call: "
                f"method={method!r} "
                f"endpoint={endpoint!r}"
            )
        )


def install_xero_environment(
    monkeypatch,
    *,
    sources,
    repo,
    provider_state,
):
    from masyg_extractor.integrations.accounting.xero.services import (
        document_service as xero_document_module,
    )

    from masyg_extractor.integrations.accounting.xero.services.document_service import (
        DocumentService as XeroDocumentService,
    )

    source_client = (
        SourceFirestoreClient()
    )

    real_document_factory = (
        load_real_xero_document_factory()
    )

    assert (
        Path(
            real_document_factory
            .__code__
            .co_filename
        ).resolve()
        == XERO_ROUTE_HELPER.resolve()
    )

    repo.integration = "xero"

    repo.get_integration_token = (
        lambda:
            "fake-xero-token"
    )

    async def fake_get_firestore_client():
        return source_client

    async def fake_document_get(
        ref,
    ):
        parts = ref.parts

        groups_index = (
            parts.index(
                "groups"
            )
        )

        files_index = (
            parts.index(
                "files"
            )
        )

        group_id = (
            parts[
                groups_index + 2
            ]
        )

        file_id = (
            parts[
                files_index + 2
            ]
        )

        return SourceSnapshot(
            sources.get(
                (
                    group_id,
                    file_id,
                )
            )
        )

    monkeypatch.setattr(
        router_module,
        "get_firestore_client",
        fake_get_firestore_client,
    )

    monkeypatch.setattr(
        router_module,
        "document_get",
        fake_document_get,
    )

    monkeypatch.setattr(
        router_module,
        "QuickBooksFirestoreService",
        lambda **_kwargs:
            repo,
    )

    runtime = (
        bridge_module
        .AccountingExecutionRuntime(
            repo_factory=(
                lambda **_kwargs:
                    repo
            ),
            client_factory=(
                lambda context:
                    FakeXeroClient(
                        context,
                        provider_state,
                    )
            ),
            service_factory=(
                XeroDocumentService
            ),
            document_factory=(
                real_document_factory
            ),
        )
    )

    def load_runtime(
        provider,
    ):
        assert (
            provider
            == "xero"
        )

        return runtime

    monkeypatch.setattr(
        bridge_module,
        "_load_execution_runtime",
        load_runtime,
    )

    monkeypatch.setattr(
        bridge_module,
        "IntegrationsProgressLog",
        FakeProgressLogger,
    )

    # Xero bridge owns its initial progress shape separately.
    monkeypatch.setattr(
        bridge_module,
        "XeroIntegrationsProgressLog",
        FakeProgressLogger,
    )

    monkeypatch.setattr(
        bridge_module,
        "LogManager",
        lambda:
            object(),
    )

    monkeypatch.setattr(
        xero_document_module,
        "CustomerService",
        FakeCustomerService,
    )

    monkeypatch.setattr(
        xero_document_module,
        "ItemService",
        FakeItemService,
    )

    monkeypatch.setattr(
        xero_document_module,
        "EntityHelper",
        FakeEntityHelper,
    )

    monkeypatch.setattr(
        XeroDocumentService,
        "_log",
        noop_log,
    )

    fake_socket = (
        FakeSocket()
    )

    monkeypatch.setattr(
        xero_document_module,
        "sio",
        fake_socket,
    )

    monkeypatch.setattr(
        operation_progress_module,
        "sio",
        fake_socket,
    )

    monkeypatch.setattr(
        repository_module.firestore,
        "transactional",
        lambda fn:
            fn,
    )

    snapshots = getattr(
        operation_progress_module,
        "_ACCOUNTING_OPERATION_SNAPSHOTS",
        None,
    )

    if hasattr(
        snapshots,
        "clear",
    ):
        snapshots.clear()


def xero_preflight_one(
    file_id,
):
    return (
        router_module
        .post_accounting_batch_preflight(
            {
                "provider":
                    "xero",
                "documents": [
                    {
                        "group_id":
                            GROUP_ID,
                        "file_id":
                            file_id,
                    }
                ],
            },
            {
                "userId":
                    USER_ID,
            },
        )
    )


def xero_verify_one(
    file_id,
):
    return (
        router_module
        .post_accounting_verify_status(
            request(),
            {
                "provider":
                    "xero",
                "group_id":
                    GROUP_ID,
                "file_id":
                    file_id,
            },
            {
                "userId":
                    USER_ID,
            },
        )
    )


def test_xero_mixed_batch_then_verify_found(
    monkeypatch,
):
    sources = {
        (
            GROUP_ID,
            "xero-file-a",
        ):
            source_document(
                "XERO-A"
            ),
        (
            GROUP_ID,
            "xero-file-b",
        ):
            source_document(
                "XERO-B"
            ),
        (
            GROUP_ID,
            "xero-file-c",
        ):
            source_document(
                "XERO-C"
            ),
    }

    (
        repo,
        _store,
        _refs,
    ) = (
        make_shared_repository()
    )

    provider_state = (
        XeroProviderState()
    )

    install_xero_environment(
        monkeypatch,
        sources=sources,
        repo=repo,
        provider_state=(
            provider_state
        ),
    )

    execution = run(
        router_module
        .post_accounting_execution(
            request(),
            {
                "confirmed":
                    True,
                "provider":
                    "xero",
                "documents": [
                    {
                        "group_id":
                            GROUP_ID,
                        "file_id":
                            "xero-file-a",
                    },
                    {
                        "group_id":
                            GROUP_ID,
                        "file_id":
                            "xero-file-b",
                    },
                    {
                        "group_id":
                            GROUP_ID,
                        "file_id":
                            "xero-file-c",
                    },
                ],
            },
            {
                "userId":
                    USER_ID,
            },
        )
    )

    # ------------------------------------------------------------
    # Canonical execution orchestration
    # ------------------------------------------------------------

    assert (
        execution[
            "provider"
        ]
        == "xero"
    )

    assert (
        execution[
            "confirmed"
        ]
        is True
    )

    assert (
        execution[
            "runtime_blocked"
        ]
        == []
    )

    assert (
        execution[
            "plan"
        ][
            "selected"
        ]
        == 3
    )

    assert (
        execution[
            "plan"
        ][
            "executable"
        ]
        == 3
    )

    assert (
        len(
            execution[
                "executions"
            ]
        )
        == 1
    )

    entry = (
        execution[
            "executions"
        ][0]
    )

    assert (
        entry[
            "accounting_intent"
        ]
        == "create_ar_invoice"
    )

    assert (
        entry[
            "status"
        ]
        == "completed"
    )

    assert (
        entry[
            "outcome"
        ]
        == {
            "total":
                3,
            "completed":
                3,
            "succeeded":
                1,
            "failed":
                2,
        }
    )

    assert not contains_key(
        execution,
        "claimToken",
    )

    assert not contains_key(
        execution,
        "claim_token",
    )

    # ------------------------------------------------------------
    # Provider POST capability safety
    # ------------------------------------------------------------

    assert (
        len(
            provider_state.posts
        )
        == 1
    )

    provider_post = (
        provider_state.posts[0]
    )

    assert (
        provider_post[
            "endpoint"
        ]
        == "Invoices"
    )

    assert not contains_key(
        provider_post[
            "payload"
        ],
        "claimToken",
    )

    assert not contains_key(
        provider_post[
            "payload"
        ],
        "claim_token",
    )

    # ------------------------------------------------------------
    # A = provider success
    # ------------------------------------------------------------

    record_a = (
        repo.get_record(
            "invoices",
            GROUP_ID,
            "xero-file-a",
        )
    )

    assert (
        record_a
        is not None
    )

    assert (
        record_a[
            "status"
        ]
        == "succeeded"
    )

    assert (
        record_a.get(
            "claimToken"
        )
        is None
    )

    assert (
        record_a[
            "providerDocumentId"
        ]
        == "xe-created-a"
    )

    # ------------------------------------------------------------
    # B = explicit provider rejection
    # ------------------------------------------------------------

    record_b = (
        repo.get_record(
            "invoices",
            GROUP_ID,
            "xero-file-b",
        )
    )

    assert (
        record_b
        is None
    )

    # ------------------------------------------------------------
    # C = provider-started but omitted result
    # ------------------------------------------------------------

    record_c = (
        repo.get_record(
            "invoices",
            GROUP_ID,
            "xero-file-c",
        )
    )

    assert (
        record_c
        is not None
    )

    assert (
        record_c[
            "status"
        ]
        == "uncertain"
    )

    c_generation = str(
        record_c.get(
            "claimToken"
        )
        or ""
    )

    assert c_generation

    assert (
        str(
            record_c.get(
                "providerDocumentNumber"
            )
            or ""
        )
        == (
            provider_state
            .ambiguous_number
        )
    )

    # ------------------------------------------------------------
    # Retry / duplicate isolation
    # ------------------------------------------------------------

    preflight_b = run(
        xero_preflight_one(
            "xero-file-b"
        )
    )

    assert (
        preflight_b[
            "documents"
        ][0][
            "state"
        ]
        == "ready"
    )

    preflight_c = run(
        xero_preflight_one(
            "xero-file-c"
        )
    )

    assert (
        preflight_c[
            "documents"
        ][0][
            "state"
        ]
        == "uncertain"
    )

    assert not contains_key(
        preflight_c,
        "claimToken",
    )

    assert not contains_key(
        preflight_c,
        "claim_token",
    )

    before_verify_a = (
        deepcopy(
            repo.get_record(
                "invoices",
                GROUP_ID,
                "xero-file-a",
            )
        )
    )

    # ------------------------------------------------------------
    # Verify C FOUND
    # ------------------------------------------------------------

    verify_result = run(
        xero_verify_one(
            "xero-file-c"
        )
    )

    assert (
        verify_result[
            "disposition"
        ]
        == "already_created"
    )

    assert (
        verify_result[
            "lookup_outcome"
        ]
        == "found"
    )

    assert (
        verify_result[
            "reconciled"
        ]
        is True
    )

    assert not contains_key(
        verify_result,
        "claimToken",
    )

    assert not contains_key(
        verify_result,
        "claim_token",
    )

    assert (
        len(
            provider_state.gets
        )
        == 1
    )

    assert (
        provider_state.gets[0][
            "endpoint"
        ]
        == "Invoices"
    )

    assert not contains_key(
        provider_state.gets[0][
            "params"
        ],
        "claimToken",
    )

    assert not contains_key(
        provider_state.gets[0][
            "params"
        ],
        "claim_token",
    )

    record_c_after = (
        repo.get_record(
            "invoices",
            GROUP_ID,
            "xero-file-c",
        )
    )

    assert (
        record_c_after
        is not None
    )

    assert (
        record_c_after[
            "status"
        ]
        == "succeeded"
    )

    assert (
        record_c_after.get(
            "claimToken"
        )
        is None
    )

    assert (
        record_c_after[
            "providerDocumentId"
        ]
        == "xe-reconciled-c"
    )

    # Verify C must not modify sibling A.
    assert (
        repo.get_record(
            "invoices",
            GROUP_ID,
            "xero-file-a",
        )
        == before_verify_a
    )

    # Verify C must not resurrect B.
    assert (
        repo.get_record(
            "invoices",
            GROUP_ID,
            "xero-file-b",
        )
        is None
    )

    assert c_generation


def seed_xero_uncertain_claim(
    repo,
    *,
    file_id,
    number,
    token,
):
    claimed = (
        repo.claim_record(
            "invoices",
            GROUP_ID,
            file_id,
            {
                "integration":
                    "xero",
                "action":
                    "create_ar_invoice",
                "group_id":
                    GROUP_ID,
                "transactionId":
                    file_id,
            },
            claim_token=token,
        )
    )

    assert claimed is True

    prepared = (
        repo.prepare_provider_dispatch(
            "invoices",
            GROUP_ID,
            file_id,
            claim_token=token,
            provider_document_number=(
                number
            ),
        )
    )

    assert prepared is True

    started = (
        repo.mark_provider_dispatch_started(
            "invoices",
            GROUP_ID,
            file_id,
            claim_token=token,
        )
    )

    assert started is True

    uncertain = (
        repo.mark_record_uncertain(
            "invoices",
            GROUP_ID,
            file_id,
            claim_token=token,
            error=(
                "Ambiguous Xero provider "
                "outcome."
            ),
        )
    )

    assert uncertain is True


def test_xero_verify_absent_preserves_uncertain_claim(
    monkeypatch,
):
    file_id = (
        "xero-verify-absent"
    )

    number = (
        "Inv-XERO-VERIFY-ABSENT"
    )

    token = (
        "claim-xero-verify-absent"
    )

    sources = {
        (
            GROUP_ID,
            file_id,
        ):
            source_document(
                "XERO-VERIFY-ABSENT"
            ),
    }

    (
        repo,
        _store,
        _refs,
    ) = (
        make_shared_repository()
    )

    provider_state = (
        XeroProviderState()
    )

    install_xero_environment(
        monkeypatch,
        sources=sources,
        repo=repo,
        provider_state=(
            provider_state
        ),
    )

    seed_xero_uncertain_claim(
        repo,
        file_id=file_id,
        number=number,
        token=token,
    )

    provider_state.lookup_modes[
        number
    ] = "absent"

    before = deepcopy(
        repo.get_record(
            "invoices",
            GROUP_ID,
            file_id,
        )
    )

    assert before is not None

    assert (
        before[
            "status"
        ]
        == "uncertain"
    )

    assert (
        before[
            "claimToken"
        ]
        == token
    )

    # After the uncertain generation exists, ABSENT has no durable
    # mutation authority: no release, retry, create, or finalize.
    forbid_verify_mutations(
        repo
    )

    result = run(
        xero_verify_one(
            file_id
        )
    )

    after = (
        repo.get_record(
            "invoices",
            GROUP_ID,
            file_id,
        )
    )

    assert (
        result[
            "disposition"
        ]
        == "needs_verification"
    )

    assert (
        result[
            "lookup_outcome"
        ]
        == "absent"
    )

    assert (
        result[
            "reconciled"
        ]
        is False
    )

    assert (
        after
        == before
    )

    assert (
        after[
            "status"
        ]
        == "uncertain"
    )

    assert (
        after[
            "claimToken"
        ]
        == token
    )

    # Verify is provider-read-only.
    assert (
        provider_state.posts
        == []
    )

    assert (
        len(
            provider_state.gets
        )
        == 1
    )

    assert (
        provider_state.gets[0][
            "endpoint"
        ]
        == "Invoices"
    )

    assert (
        provider_state.gets[0][
            "params"
        ]
        == {
            "where":
                (
                    'InvoiceNumber=="'
                    + number
                    + '"'
                ),
        }
    )

    assert not contains_key(
        provider_state.gets[0][
            "params"
        ],
        "claimToken",
    )

    assert not contains_key(
        provider_state.gets[0][
            "params"
        ],
        "claim_token",
    )

    assert not contains_key(
        result,
        "claimToken",
    )

    assert not contains_key(
        result,
        "claim_token",
    )

    # The claim remains the duplicate barrier.
    preflight = run(
        xero_preflight_one(
            file_id
        )
    )

    assert (
        preflight[
            "documents"
        ][0][
            "state"
        ]
        == "uncertain"
    )

    assert not contains_key(
        preflight,
        "claimToken",
    )

    assert not contains_key(
        preflight,
        "claim_token",
    )


def test_xero_verify_indeterminate_preserves_uncertain_claim(
    monkeypatch,
):
    file_id = (
        "xero-verify-indeterminate"
    )

    number = (
        "Inv-XERO-VERIFY-INDETERMINATE"
    )

    token = (
        "claim-xero-verify-indeterminate"
    )

    sources = {
        (
            GROUP_ID,
            file_id,
        ):
            source_document(
                "XERO-VERIFY-INDETERMINATE"
            ),
    }

    (
        repo,
        _store,
        _refs,
    ) = (
        make_shared_repository()
    )

    provider_state = (
        XeroProviderState()
    )

    install_xero_environment(
        monkeypatch,
        sources=sources,
        repo=repo,
        provider_state=(
            provider_state
        ),
    )

    seed_xero_uncertain_claim(
        repo,
        file_id=file_id,
        number=number,
        token=token,
    )

    provider_state.lookup_modes[
        number
    ] = "indeterminate"

    before = deepcopy(
        repo.get_record(
            "invoices",
            GROUP_ID,
            file_id,
        )
    )

    assert before is not None

    assert (
        before[
            "status"
        ]
        == "uncertain"
    )

    assert (
        before[
            "claimToken"
        ]
        == token
    )

    # An indeterminate provider lookup carries no mutation authority.
    forbid_verify_mutations(
        repo
    )

    result = run(
        xero_verify_one(
            file_id
        )
    )

    after = (
        repo.get_record(
            "invoices",
            GROUP_ID,
            file_id,
        )
    )

    assert (
        result[
            "disposition"
        ]
        == "needs_verification"
    )

    assert (
        result[
            "lookup_outcome"
        ]
        == "indeterminate"
    )

    assert (
        result[
            "reconciled"
        ]
        is False
    )

    assert (
        after
        == before
    )

    assert (
        after[
            "status"
        ]
        == "uncertain"
    )

    assert (
        after[
            "claimToken"
        ]
        == token
    )

    assert (
        provider_state.posts
        == []
    )

    assert (
        len(
            provider_state.gets
        )
        == 1
    )

    assert (
        provider_state.gets[0][
            "endpoint"
        ]
        == "Invoices"
    )

    assert (
        provider_state.gets[0][
            "params"
        ]
        == {
            "where":
                (
                    'InvoiceNumber=="'
                    + number
                    + '"'
                ),
        }
    )

    assert not contains_key(
        provider_state.gets[0][
            "params"
        ],
        "claimToken",
    )

    assert not contains_key(
        provider_state.gets[0][
            "params"
        ],
        "claim_token",
    )

    assert not contains_key(
        result,
        "claimToken",
    )

    assert not contains_key(
        result,
        "claim_token",
    )

    preflight = run(
        xero_preflight_one(
            file_id
        )
    )

    assert (
        preflight[
            "documents"
        ][0][
            "state"
        ]
        == "uncertain"
    )

    assert not contains_key(
        preflight,
        "claimToken",
    )

    assert not contains_key(
        preflight,
        "claim_token",
    )
