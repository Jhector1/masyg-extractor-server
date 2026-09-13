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


def test_live_quickbooks_owner_is_canonical_registry_entry():
    registry = _source(
        "masyg_extractor/integrations/accounting/registry.py"
    )

    assert 'provider="quickbooks"' in registry
    assert (
        'router_module="masyg_extractor.integrations.accounting.'
        'quickbooks.router"'
    ) in registry


def test_live_quickbooks_router_uses_canonical_workflow_services():
    router = _source(
        "masyg_extractor/integrations/accounting/quickbooks/router.py"
    )

    for owner in (
        "integrations.accounting.quickbooks.services.account_service",
        "integrations.accounting.quickbooks.services.invoice_service",
        "integrations.accounting.quickbooks.services.document_service",
        "integrations.accounting.quickbooks.services.quickbook_service",
        "integrations.accounting.quickbooks.authentication.quickbook_auth",
        "integrations.accounting.quickbooks.adapter",
        "integrations.accounting.quickbooks.route_helper",
    ):
        assert owner in router


def test_live_quickbooks_route_contract_matches_frontend_actions():
    decorators = _route_decorators(
        "masyg_extractor/integrations/accounting/quickbooks/router.py"
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


def test_canonical_invoice_service_keeps_async_contract():
    invoice_methods = _class_methods(
        "masyg_extractor/integrations/accounting/quickbooks/"
        "services/invoice_service.py",
        "InvoiceService",
    )

    assert isinstance(
        invoice_methods["send_invoice"],
        ast.AsyncFunctionDef,
    )
    assert isinstance(
        invoice_methods["send_invoice_in_bulk"],
        ast.AsyncFunctionDef,
    )

    assert [arg.arg for arg in invoice_methods["send_invoice"].args.args] == [
        "self",
        "invoice",
        "share_progress",
    ]


def test_canonical_quickbooks_customer_and_item_services_use_domain_models():
    customer_methods = _class_methods(
        "masyg_extractor/integrations/accounting/quickbooks/"
        "services/customer_service.py",
        "CustomerService",
    )
    item_methods = _class_methods(
        "masyg_extractor/integrations/accounting/quickbooks/"
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
