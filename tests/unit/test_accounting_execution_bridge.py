from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from masyg_extractor.integrations.accounting.core.models import (
    Customer,
    Document,
)
from masyg_extractor.integrations.accounting.shared import (
    execution_bridge as bridge_module,
)
from masyg_extractor.integrations.accounting.shared.execution_bridge import (
    AccountingExecutionBridge,
    get_accounting_execution_bridge_spec,
)


ROOT = Path(__file__).resolve().parents[2]


EXPECTED = {
    (
        "quickbooks",
        "create_ar_invoice",
    ): {
        "context_doc_type": "Invoice",
        "service_doc_type": "Invoice",
        "doc_number_prefix": "Inv",
        "progress_log_key":
            "quickbooks-invoice-progress",
        "progress_shape": "quickbooks",
        "invoice_status": None,
    },
    (
        "quickbooks",
        "create_sales_receipt",
    ): {
        "context_doc_type": "SalesReceipt",
        "service_doc_type": "SalesReceipt",
        "doc_number_prefix": "REC",
        "progress_log_key":
            "quickbooks-invoice-progress",
        "progress_shape": "quickbooks",
        "invoice_status": None,
    },
    (
        "xero",
        "create_ar_invoice",
    ): {
        "context_doc_type": "Invoices",
        "service_doc_type": "Invoices",
        "doc_number_prefix": "Inv",
        "progress_log_key":
            "xero-invoice-progress",
        "progress_shape": "xero",
        "invoice_status": "ACCREC",
    },
    (
        "xero",
        "create_ap_bill",
    ): {
        "context_doc_type": "Invoices",
        "service_doc_type": "Invoices",
        "doc_number_prefix": "Inv",
        "progress_log_key":
            "xero-invoice-progress",
        "progress_shape": "xero",
        "invoice_status": "ACCPAY",
    },
}


def make_document() -> Document:
    return Document(
        customer=Customer(
            id=None,
            name="Customer",
            transaction_id="file-1",
        ),
        items=[],
        date="2026-09-15",
        due_date="2026-09-30",
        transaction_id="file-1",
        group_id="group-1",
    )


@pytest.mark.parametrize(
    (
        "provider",
        "intent",
        "expected",
    ),
    [
        (
            provider,
            intent,
            expected,
        )
        for (
            provider,
            intent,
        ), expected in EXPECTED.items()
    ],
)
def test_execution_bridge_specs_match_existing_bulk_route_semantics(
    provider,
    intent,
    expected,
):
    spec = (
        get_accounting_execution_bridge_spec(
            provider,
            intent,
        )
    )

    assert spec.provider == provider
    assert spec.accounting_intent == intent

    for key, value in expected.items():
        assert getattr(spec, key) == value


def test_execution_bridge_rejects_unsupported_intent():
    with pytest.raises(KeyError):
        get_accounting_execution_bridge_spec(
            "quickbooks",
            "create_ap_bill",
        )

    with pytest.raises(KeyError):
        get_accounting_execution_bridge_spec(
            "xero",
            "create_sales_receipt",
        )


def test_provider_runtime_imports_are_not_module_level():
    source = (
        ROOT
        / (
            "masyg_extractor/integrations/accounting/"
            "shared/execution_bridge.py"
        )
    ).read_text()

    tree = ast.parse(source)

    forbidden_module_fragments = (
        ".accounting.quickbooks.",
        ".accounting.xero.",
        ".shared.firestore_repository",
    )

    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""

            assert not any(
                fragment in module
                for fragment
                in forbidden_module_fragments
            )

        elif isinstance(node, ast.Import):
            for alias in node.names:
                assert not any(
                    fragment in alias.name
                    for fragment
                    in forbidden_module_fragments
                )


def test_bridge_builder_constructs_quickbooks_owner_without_sending(
    monkeypatch,
):
    events = []

    class FakeRepo:
        def __init__(
            self,
            *,
            user_id,
            integration,
        ):
            events.append(
                (
                    "repo",
                    user_id,
                    integration,
                )
            )

    class FakeClient:
        def __init__(self, context):
            self.context = context

            events.append(
                (
                    "client",
                    context.doct_type,
                )
            )

    class FakeService:
        def __init__(
            self,
            *,
            doc_type,
            doc_number_prefix,
            context,
            repo,
            client,
        ):
            self.doc_type = doc_type
            self.doc_number_prefix = (
                doc_number_prefix
            )
            self.context = context
            self.repo = repo
            self.client = client

            events.append(
                (
                    "service",
                    doc_type,
                    doc_number_prefix,
                )
            )

    def fake_document_factory(
        transaction_id,
        group_id,
        details,
    ):
        raise AssertionError(
            "Document factory must not run during bridge construction."
        )

    monkeypatch.setattr(
        bridge_module,
        "_load_quickbooks_runtime",
        lambda: SimpleNamespace(
            repo_factory=FakeRepo,
            client_factory=FakeClient,
            service_factory=FakeService,
            document_factory=(
                fake_document_factory
            ),
        ),
    )

    request = SimpleNamespace(
        session={
            "client_id": "client-1",
        }
    )

    bridge = (
        bridge_module
        .build_accounting_execution_bridge(
            request=request,
            user_id="user-1",
            provider="quickbooks",
            accounting_intent=(
                "create_ar_invoice"
            ),
        )
    )

    assert bridge.spec.provider == "quickbooks"
    assert bridge.service.doc_type == "Invoice"
    assert (
        bridge.service.doc_number_prefix
        == "Inv"
    )
    assert (
        bridge.service.context.user_id
        == "user-1"
    )
    assert (
        bridge.service.context.client_id
        == "client-1"
    )
    assert (
        bridge.service.context.doct_type
        == "Invoice"
    )
    assert (
        bridge.document_factory
        is fake_document_factory
    )

    assert events == [
        (
            "repo",
            "user-1",
            "quickbooks",
        ),
        (
            "client",
            "Invoice",
        ),
        (
            "service",
            "Invoice",
            "Inv",
        ),
    ]


@pytest.mark.parametrize(
    (
        "intent",
        "invoice_status",
    ),
    [
        (
            "create_ar_invoice",
            "ACCREC",
        ),
        (
            "create_ap_bill",
            "ACCPAY",
        ),
    ],
)
def test_bridge_builder_constructs_xero_owner_without_sending(
    monkeypatch,
    intent,
    invoice_status,
):
    class FakeRepo:
        def __init__(
            self,
            *,
            user_id,
            integration,
        ):
            self.user_id = user_id
            self.integration = integration

    class FakeClient:
        def __init__(self, context):
            self.context = context

    class FakeService:
        def __init__(
            self,
            *,
            doc_type,
            doc_number_prefix,
            context,
            repo,
            client,
        ):
            self.doc_type = doc_type
            self.doc_number_prefix = (
                doc_number_prefix
            )
            self.context = context
            self.repo = repo
            self.client = client

    def fake_document_factory(
        transaction_id,
        group_id,
        details,
    ):
        raise AssertionError(
            "Document factory must not run during bridge construction."
        )

    monkeypatch.setattr(
        bridge_module,
        "_load_xero_runtime",
        lambda: SimpleNamespace(
            repo_factory=FakeRepo,
            client_factory=FakeClient,
            service_factory=FakeService,
            document_factory=(
                fake_document_factory
            ),
        ),
    )

    request = SimpleNamespace(
        session={
            "client_id": "client-x",
        }
    )

    bridge = (
        bridge_module
        .build_accounting_execution_bridge(
            request=request,
            user_id="user-x",
            provider="xero",
            accounting_intent=intent,
        )
    )

    assert bridge.spec.provider == "xero"
    assert bridge.spec.invoice_status == (
        invoice_status
    )
    assert bridge.service.doc_type == "Invoices"
    assert (
        bridge.service.doc_number_prefix
        == "Inv"
    )
    assert (
        bridge.service.context.doct_type
        == "Invoices"
    )
    assert (
        bridge.document_factory
        is fake_document_factory
    )

    assert set(bridge.progress) == {
        "creating_items",
        "creating_contacts",
        "creating_invoices",
    }


def test_bridge_requires_authenticated_execution_context():
    request = SimpleNamespace(
        session={
            "client_id": "client-1",
        }
    )

    with pytest.raises(
        ValueError,
        match="User ID is required",
    ):
        bridge_module.build_accounting_execution_bridge(
            request=request,
            user_id="",
            provider="quickbooks",
            accounting_intent=(
                "create_ar_invoice"
            ),
        )

    request = SimpleNamespace(
        session={}
    )

    with pytest.raises(
        ValueError,
        match="Client ID not found",
    ):
        bridge_module.build_accounting_execution_bridge(
            request=request,
            user_id="user-1",
            provider="quickbooks",
            accounting_intent=(
                "create_ar_invoice"
            ),
        )


def test_quickbooks_bridge_send_delegates_to_existing_bulk_owner():
    calls = []

    class FakeProgress:
        async def safe_emit_progress(
            self,
            value,
        ):
            calls.append(
                (
                    "progress",
                    value,
                )
            )
            return 50.0

    class FakeService:
        async def send_document_in_bulk(
            self,
            documents,
            share_progress,
        ):
            calls.append(
                (
                    "send",
                    documents,
                    share_progress,
                )
            )
            return {
                "provider": "quickbooks",
            }

    bridge = AccountingExecutionBridge(
        spec=(
            get_accounting_execution_bridge_spec(
                "quickbooks",
                "create_ar_invoice",
            )
        ),
        service=FakeService(),
        document_factory=lambda *_: None,
        progress_logger=FakeProgress(),
        progress={
            "creating_item": 0.0,
            "creating_customer": 0.0,
            "creating_invoice": 0.0,
        },
    )

    document = make_document()

    result = asyncio.run(
        bridge.send_documents(
            [document]
        )
    )

    assert result == {
        "provider": "quickbooks",
    }

    assert calls[0][0] == "progress"

    assert calls[1] == (
        "send",
        [document],
        50.0,
    )


@pytest.mark.parametrize(
    (
        "intent",
        "expected_status",
    ),
    [
        (
            "create_ar_invoice",
            "ACCREC",
        ),
        (
            "create_ap_bill",
            "ACCPAY",
        ),
    ],
)
def test_xero_bridge_send_preserves_invoice_status(
    intent,
    expected_status,
):
    calls = []

    class FakeProgress:
        def calculate_overall_progress(
            self,
            value,
        ):
            calls.append(
                (
                    "calculate",
                    value,
                )
            )
            return 0.0

        async def safe_emit_progress(
            self,
            value,
        ):
            calls.append(
                (
                    "progress",
                    value,
                )
            )
            return 0.0

    class FakeService:
        async def send_document_in_bulk(
            self,
            documents,
            share_progress,
            *,
            invoice_status,
        ):
            calls.append(
                (
                    "send",
                    documents,
                    share_progress,
                    invoice_status,
                )
            )
            return {
                "provider": "xero",
            }

    bridge = AccountingExecutionBridge(
        spec=(
            get_accounting_execution_bridge_spec(
                "xero",
                intent,
            )
        ),
        service=FakeService(),
        document_factory=lambda *_: None,
        progress_logger=FakeProgress(),
        progress={
            "creating_items": 0.0,
            "creating_contacts": 0.0,
            "creating_invoices": 0.0,
        },
    )

    document = make_document()

    result = asyncio.run(
        bridge.send_documents(
            [document]
        )
    )

    assert result == {
        "provider": "xero",
    }

    assert calls[-1] == (
        "send",
        [document],
        0.0,
        expected_status,
    )


def test_bridge_never_reintroduces_legacy_payload_reconstruction():
    source = (
        ROOT
        / (
            "masyg_extractor/integrations/accounting/"
            "shared/execution_bridge.py"
        )
    ).read_text()

    assert "request.json(" not in source
    assert "txn_id_full" not in source
    assert "key.strip()" not in source
    assert "txn_id.strip()" not in source


def test_bridge_delegates_provider_write_to_existing_document_services():
    source = (
        ROOT
        / (
            "masyg_extractor/integrations/accounting/"
            "shared/execution_bridge.py"
        )
    ).read_text()

    assert (
        ".send_document_in_bulk("
        in source
    )

    forbidden = (
        "claim_record(",
        "finalize_record(",
        "mark_record_uncertain(",
        "release_record_claim(",
        "client.request(",
        ".request(",
    )

    for token in forbidden:
        assert token not in source
