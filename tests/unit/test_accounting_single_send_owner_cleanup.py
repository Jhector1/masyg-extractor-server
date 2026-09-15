import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def source(relative: str) -> str:
    return (ROOT / relative).read_text()


def class_method_names(
    relative: str,
    class_name: str,
) -> set[str]:
    tree = ast.parse(source(relative))

    cls = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and node.name == class_name
    )

    return {
        node.name
        for node in cls.body
        if isinstance(
            node,
            (ast.FunctionDef, ast.AsyncFunctionDef),
        )
    }


def test_quickbooks_document_service_has_one_creation_owner():
    methods = class_method_names(
        "masyg_extractor/integrations/accounting/quickbooks/"
        "services/document_service.py",
        "DocumentService",
    )

    assert "send_document_in_bulk" in methods
    assert "send_document" not in methods


def test_xero_document_service_has_one_creation_owner():
    methods = class_method_names(
        "masyg_extractor/integrations/accounting/xero/"
        "services/document_service.py",
        "DocumentService",
    )

    assert "send_document_in_bulk" in methods
    assert "send_document" not in methods


def test_quickbooks_legacy_route_still_uses_bulk_owner_through_adapter():
    router = source(
        "masyg_extractor/integrations/accounting/quickbooks/router.py"
    )
    service = source(
        "masyg_extractor/integrations/accounting/quickbooks/"
        "services/invoice_service.py"
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


def test_xero_legacy_route_still_uses_bulk_owner_through_adapter():
    router = source(
        "masyg_extractor/integrations/accounting/xero/router.py"
    )
    service = source(
        "masyg_extractor/integrations/accounting/xero/"
        "services/invoice_service.py"
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


def test_no_production_accounting_code_calls_removed_single_owner():
    accounting = (
        ROOT
        / "masyg_extractor/integrations/accounting"
    )

    offenders = []

    for path in accounting.rglob("*.py"):
        text = path.read_text()

        if (
            ".send_document(" in text
            or "super().send_document(" in text
        ):
            offenders.append(
                str(path.relative_to(ROOT))
            )

    assert offenders == []
