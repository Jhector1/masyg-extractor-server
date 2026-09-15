from __future__ import annotations

import ast
from pathlib import Path

import pytest

from masyg_extractor.integrations.accounting.core.models import (
    Customer,
    Document,
)
from masyg_extractor.integrations.accounting.shared.document_materializer import (
    materialize_accounting_document,
)


ROOT = Path(__file__).resolve().parents[2]


def make_document(
    transaction_id: str,
    group_id: str,
) -> Document:
    return Document(
        customer=Customer(
            id=None,
            name="Customer",
            transaction_id=transaction_id,
        ),
        items=[],
        date="2026-09-15",
        due_date="2026-09-30",
        transaction_id=transaction_id,
        group_id=group_id,
    )


def test_materializer_passes_exact_canonical_identity_to_factory():
    calls = []

    def factory(
        transaction_id,
        group_id,
        details,
    ):
        calls.append(
            (
                transaction_id,
                group_id,
                details,
            )
        )

        return make_document(
            transaction_id,
            group_id,
        )

    source = {
        "documentType": "vendor_bill",
        "customer_name": "Example",
        "line_items": [],
    }

    result = materialize_accounting_document(
        source,
        group_id="group-123",
        file_id="file-456",
        factory=factory,
    )

    assert result.transaction_id == "file-456"
    assert result.group_id == "group-123"

    assert calls == [
        (
            "file-456",
            "group-123",
            source,
        )
    ]


def test_materializer_preserves_real_canonical_file_id_without_legacy_suffix():
    file_id = (
        "e9ef5d6a-ac9f-4433-82b7-"
        "08236b931d83_yyyo_pdf"
    )

    result = materialize_accounting_document(
        {
            "documentType": "vendor_bill",
            "line_items": [],
        },
        group_id="20260912072644",
        file_id=file_id,
        factory=lambda transaction_id, group_id, details: (
            make_document(
                transaction_id,
                group_id,
            )
        ),
    )

    assert result.transaction_id == file_id
    assert not result.transaction_id.endswith(
        "-0"
    )


def test_materializer_does_not_mutate_persisted_source_mapping():
    source = {
        "documentType": "sales_invoice",
        "customer_name": "Example",
        "line_items": [
            {
                "item_name": "Widget",
                "quantity": 1,
                "unit_price": "10.00",
            }
        ],
    }

    before = {
        **source,
        "line_items": [
            dict(source["line_items"][0])
        ],
    }

    materialize_accounting_document(
        source,
        group_id="group-1",
        file_id="file-1",
        factory=lambda transaction_id, group_id, details: (
            make_document(
                transaction_id,
                group_id,
            )
        ),
    )

    assert source == before


@pytest.mark.parametrize(
    (
        "group_id",
        "file_id",
    ),
    [
        ("", "file-1"),
        ("group-1", ""),
        ("   ", "file-1"),
        ("group-1", "   "),
    ],
)
def test_materializer_requires_canonical_identity(
    group_id,
    file_id,
):
    with pytest.raises(
        ValueError,
        match=(
            "Canonical group_id and file_id "
            "are required"
        ),
    ):
        materialize_accounting_document(
            {},
            group_id=group_id,
            file_id=file_id,
            factory=lambda *_: make_document(
                "unused",
                "unused",
            ),
        )


def test_materializer_rejects_factory_that_changes_file_id():
    with pytest.raises(
        ValueError,
        match="changed canonical file_id",
    ):
        materialize_accounting_document(
            {},
            group_id="group-1",
            file_id="file-1",
            factory=lambda *_: make_document(
                "file-1-0",
                "group-1",
            ),
        )


def test_materializer_rejects_factory_that_changes_group_id():
    with pytest.raises(
        ValueError,
        match="changed canonical group_id",
    ):
        materialize_accounting_document(
            {},
            group_id="group-1",
            file_id="file-1",
            factory=lambda *_: make_document(
                "file-1",
                "different-group",
            ),
        )


def test_materializer_rejects_non_document_factory_result():
    with pytest.raises(
        TypeError,
        match="must return Document",
    ):
        materialize_accounting_document(
            {},
            group_id="group-1",
            file_id="file-1",
            factory=lambda *_: {},
        )


def test_shared_materializer_never_uses_legacy_payload_normalization():
    source = (
        ROOT
        / (
            "masyg_extractor/integrations/accounting/"
            "shared/document_materializer.py"
        )
    ).read_text()

    tree = ast.parse(source)

    referenced_symbols = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            referenced_symbols.add(node.id)
        elif isinstance(node, ast.Attribute):
            referenced_symbols.add(node.attr)
        elif isinstance(node, ast.Import):
            referenced_symbols.update(
                alias.asname or alias.name
                for alias in node.names
            )
        elif isinstance(node, ast.ImportFrom):
            referenced_symbols.update(
                alias.asname or alias.name
                for alias in node.names
            )

    forbidden_symbols = {
        "normalize_payload",
        "txn_id_full",
        "split",
        "QuickBooksClientAdapter",
        "XeroClientAdapter",
        "claim_record",
        "finalize_record",
        "request",
    }

    assert referenced_symbols.isdisjoint(
        forbidden_symbols
    )

    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "endswith"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == "-0"
        for node in ast.walk(tree)
    )


@pytest.mark.parametrize(
    "relative",
    [
        (
            "masyg_extractor/integrations/accounting/"
            "quickbooks/route_helper.py"
        ),
        (
            "masyg_extractor/integrations/accounting/"
            "xero/route_helper.py"
        ),
    ],
)
def test_existing_provider_factories_accept_prebuilt_transaction_identity(
    relative,
):
    source = (
        ROOT / relative
    ).read_text()

    assert (
        "def create_document("
        "txn_id_full: str, group_id: str"
        in source
    )

    assert (
        "transaction_id=txn_id_full"
        in source
    )

    assert (
        "group_id=group_id"
        in source
    )


def test_legacy_normalizers_remain_outside_canonical_materializer_boundary():
    for relative in (
        (
            "masyg_extractor/integrations/accounting/"
            "quickbooks/route_helper.py"
        ),
        (
            "masyg_extractor/integrations/accounting/"
            "xero/route_helper.py"
        ),
    ):
        source = (
            ROOT / relative
        ).read_text()

        assert (
            'txn_id_full = '
            'f"{key.strip()}-{txn_id.strip()}"'
            in source
        )

    materializer = (
        ROOT
        / (
            "masyg_extractor/integrations/accounting/"
            "shared/document_materializer.py"
        )
    ).read_text()

    materializer_tree = ast.parse(
        materializer
    )

    called_functions = {
        (
            node.func.id
            if isinstance(node.func, ast.Name)
            else node.func.attr
            if isinstance(node.func, ast.Attribute)
            else None
        )
        for node in ast.walk(materializer_tree)
        if isinstance(node, ast.Call)
    }

    assert "normalize_payload" not in called_functions
    assert "key.strip()" not in materializer
    assert "txn_id.strip()" not in materializer
