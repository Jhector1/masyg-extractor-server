from __future__ import annotations

import asyncio
import ast
from pathlib import Path
from types import SimpleNamespace

from masyg_extractor.integrations.accounting.shared.single_send_compat import (
    run_single_via_bulk,
)


ROOT = Path(__file__).resolve().parents[2]


def source(relative: str) -> str:
    return (ROOT / relative).read_text()


class FakeRepo:
    def __init__(self, record=None):
        self.record = record
        self.reads = []

    def get_record(
        self,
        record_type,
        group_id,
        transaction_id,
    ):
        self.reads.append(
            (
                record_type,
                group_id,
                transaction_id,
            )
        )
        return self.record


def document():
    return SimpleNamespace(
        group_id="group-1",
        transaction_id="file-1",
    )


def test_single_compat_returns_provider_id_after_confirmed_success():
    repo = FakeRepo(
        {
            "status": "succeeded",
            "providerDocumentId": "provider-123",
        }
    )

    async def send_bulk(documents, share_progress):
        assert len(documents) == 1
        assert share_progress == 25
        return {
            "results": [
                {
                    "document_id": "file-1",
                    "status": "succeeded",
                    "error": None,
                }
            ]
        }

    result = asyncio.run(
        run_single_via_bulk(
            document(),
            25,
            send_bulk=send_bulk,
            repo=repo,
            record_type="invoices",
        )
    )

    assert result == "provider-123"
    assert repo.reads == [
        (
            "invoices",
            "group-1",
            "file-1",
        )
    ]


def test_single_compat_preserves_legacy_error_shape():
    repo = FakeRepo(None)

    async def send_bulk(documents, share_progress):
        return {
            "results": [
                {
                    "document_id": "file-1",
                    "status": "failed",
                    "message": None,
                    "error": "Already sent.",
                }
            ]
        }

    result = asyncio.run(
        run_single_via_bulk(
            document(),
            25,
            send_bulk=send_bulk,
            repo=repo,
            record_type="invoices",
        )
    )

    assert result == {
        "error": "Already sent.",
    }


def test_quickbooks_single_route_converges_to_atomic_bulk_owner():
    service = source(
        "masyg_extractor/integrations/accounting/quickbooks/"
        "services/invoice_service.py"
    )
    router = source(
        "masyg_extractor/integrations/accounting/quickbooks/"
        "router.py"
    )

    assert '"/send-invoice"' in router
    assert (
        "send_method_getter=lambda service: service.send_invoice"
        in router
    )

    assert "run_single_via_bulk" in service
    assert (
        "send_bulk=super().send_document_in_bulk"
        in service
    )
    assert 'record_type="invoices"' in service
    assert (
        "super().send_document(invoice"
        not in service
    )


def test_xero_single_route_converges_to_atomic_bulk_owner():
    service = source(
        "masyg_extractor/integrations/accounting/xero/"
        "services/invoice_service.py"
    )
    router = source(
        "masyg_extractor/integrations/accounting/xero/"
        "router.py"
    )

    assert '"/send-invoice"' in router
    assert (
        "send_method_getter=lambda service: service.send_invoice"
        in router
    )

    assert "run_single_via_bulk" in service
    assert (
        "send_bulk=super().send_document_in_bulk"
        in service
    )
    assert 'record_type="invoices"' in service
    assert (
        "super().send_document(invoice"
        not in service
    )


def test_xero_single_route_uses_xero_repository_identity():
    helper = source(
        "masyg_extractor/integrations/accounting/xero/"
        "route_helper.py"
    )

    compact = "".join(helper.split())

    assert (
        'QuickBooksFirestoreService('
        'user_id=user_id,integration="xero")'
        in compact
    )


def test_quickbooks_public_single_send_contract_is_still_async():
    service = source(
        "masyg_extractor/integrations/accounting/quickbooks/"
        "services/invoice_service.py"
    )

    tree = ast.parse(service)

    cls = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and node.name == "InvoiceService"
    )

    method = next(
        node
        for node in cls.body
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "send_invoice"
    )

    assert [
        arg.arg
        for arg in method.args.args
    ] == [
        "self",
        "invoice",
        "share_progress",
    ]
