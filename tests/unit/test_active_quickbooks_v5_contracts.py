from pathlib import Path
import ast


ROOT = Path(__file__).resolve().parents[2]


def _source(relative_path: str) -> str:
    return (ROOT / relative_path).read_text()


def _class_methods(relative_path: str, class_name: str):
    tree = ast.parse(_source(relative_path))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return {
                child.name: child
                for child in node.body
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
    raise AssertionError(f"{class_name} not found in {relative_path}")


def _route_decorators(relative_path: str):
    tree = ast.parse(_source(relative_path))
    decorators = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            rendered = ast.unparse(decorator)
            if "router." in rendered:
                decorators.append(rendered)
    return decorators


def test_live_quickbooks_owner_is_v5_registry_entry():
    registry = _source(
        "masyg_extractor/integrations/accounting/registry.py"
    )

    assert 'provider="quickbooks"' in registry
    assert 'implementation="integration_qb_v5"' in registry
    assert (
        'router_module="masyg_extractor.integration_qb_v5.'
        'routers.qb_router"'
    ) in registry


def test_live_quickbooks_router_uses_v5_workflow_services():
    router = _source(
        "masyg_extractor/integration_qb_v5/routers/qb_router.py"
    )

    for owner in (
        "integration_qb_v5.intergrate.quickbooks.services.account_service",
        "integration_qb_v5.intergrate.quickbooks.services.invoice_service",
        "integration_qb_v5.intergrate.quickbooks.services.document_service",
        "integration_qb_v5.intergrate.quickbooks.adapter",
        "integration_qb_v5.routers.route_helper",
    ):
        assert owner in router

    assert (
        "masyg_extractor.integrations.quickbooks.services.invoice_service"
        not in router
    )
    assert (
        "masyg_extractor.integrations.quickbooks.services.receipt_service"
        not in router
    )


def test_live_quickbooks_route_contract_matches_frontend_actions():
    decorators = _route_decorators(
        "masyg_extractor/integration_qb_v5/routers/qb_router.py"
    )
    rendered = "\n".join(decorators)

    for endpoint in (
        "/send-invoice-in-bulk",
        "/send-salereceipt-in-bulk",
        "/send-invoice",
        "/get-items",
        "/get-customers",
        "/get-vendors",
        "/get-accounts",
        "/get-income-accounts",
        "/get-expense-accounts",
    ):
        assert endpoint in rendered


def test_v5_invoice_and_receipt_services_are_async_instance_services():
    invoice_methods = _class_methods(
        "masyg_extractor/integration_qb_v5/intergrate/quickbooks/"
        "services/invoice_service.py",
        "InvoiceService",
    )
    receipt_methods = _class_methods(
        "masyg_extractor/integration_qb_v5/intergrate/quickbooks/"
        "services/receipt_service.py",
        "SalesReceiptService",
    )

    assert isinstance(
        invoice_methods["send_invoice"],
        ast.AsyncFunctionDef,
    )
    assert isinstance(
        invoice_methods["send_invoice_in_bulk"],
        ast.AsyncFunctionDef,
    )
    assert isinstance(
        receipt_methods["send_receipt"],
        ast.AsyncFunctionDef,
    )

    assert [arg.arg for arg in invoice_methods["send_invoice"].args.args] == [
        "self",
        "invoice",
        "share_progress",
    ]
    assert [arg.arg for arg in receipt_methods["send_receipt"].args.args] == [
        "self",
        "sales_receipt",
        "share_progress",
    ]


def test_v5_customer_and_item_services_use_domain_models():
    customer_methods = _class_methods(
        "masyg_extractor/integration_qb_v5/intergrate/quickbooks/"
        "services/customer_service.py",
        "CustomerService",
    )
    item_methods = _class_methods(
        "masyg_extractor/integration_qb_v5/intergrate/quickbooks/"
        "services/item_service.py",
        "ItemService",
    )

    assert isinstance(
        customer_methods["get_or_create_customer"],
        ast.AsyncFunctionDef,
    )
    assert [arg.arg for arg in customer_methods[
        "get_or_create_customer"
    ].args.args] == ["self", "customer"]

    assert isinstance(
        item_methods["check_item_exists"],
        ast.AsyncFunctionDef,
    )
    assert isinstance(
        item_methods["create_item"],
        ast.AsyncFunctionDef,
    )
    assert [arg.arg for arg in item_methods["check_item_exists"].args.args] == [
        "self",
        "item",
    ]
    assert [arg.arg for arg in item_methods["create_item"].args.args] == [
        "self",
        "item",
    ]


def test_obsolete_quickbooks_transaction_service_test_is_retired():
    obsolete = (
        ROOT
        / "masyg_extractor/integrations/quickbooks/tests/"
        "test_qb_transactions_services.py"
    )
    assert not obsolete.exists()
