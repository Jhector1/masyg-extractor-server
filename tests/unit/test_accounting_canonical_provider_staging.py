import ast
from importlib import import_module
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]

PROVIDER_ROOTS = {
    "quickbooks": ROOT / "masyg_extractor/integrations/accounting/quickbooks",
    "xero": ROOT / "masyg_extractor/integrations/accounting/xero",
}

EXPECTED_FILES = {
    "quickbooks": {
        "__init__.py",
        "entity_helper.py",
        "base_adapter.py",
        "adapter.py",
        "router.py",
        "route_helper.py",
        "utils.py",
        "services/__init__.py",
        "services/account_service.py",
        "services/audit_log_service.py",
        "services/customer_service.py",
        "services/document_service.py",
        "services/invoice_service.py",
        "services/item_service.py",
        "client.py",
        "authentication/__init__.py",
        "authentication/quickbook_auth.py",
        "services/quickbook_service.py",
    },
    "xero": {
        "__init__.py",
        "entity_helper.py",
        "base_adapter.py",
        "adapter.py",
        "router.py",
        "route_helper.py",
        "utils.py",
        "services/__init__.py",
        "services/account_service.py",
        "services/customer_service.py",
        "services/document_service.py",
        "services/invoice_service.py",
        "services/item_service.py",
        "client.py",
        "authentication/__init__.py",
        "authentication/xero_auth.py",
    },
}

FORBIDDEN_PREFIXES = (
    "masyg_extractor.integration_qb_v5",
    "masyg_extractor.integration_v4",
)


def _imports(path: Path):
    tree = ast.parse(path.read_text(), filename=str(path))
    modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
        elif isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
    return modules


def test_canonical_provider_files_are_staged():
    for provider, expected in EXPECTED_FILES.items():
        provider_root = PROVIDER_ROOTS[provider]
        actual = {
            str(path.relative_to(provider_root))
            for path in provider_root.rglob("*.py")
        }
        assert actual == expected


def test_canonical_provider_code_has_no_versioned_imports():
    offenders = []

    for provider_root in PROVIDER_ROOTS.values():
        for path in provider_root.rglob("*.py"):
            for module in _imports(path):
                if module.startswith(FORBIDDEN_PREFIXES):
                    offenders.append(
                        f"{path.relative_to(ROOT)} imports {module}"
                    )

    assert offenders == []


def _import_router_without_external_firestore(module_name: str):
    # The application initializes Firebase during normal startup. This
    # architecture contract imports provider modules in isolation, so stub only
    # the Firebase client factory to avoid requiring application bootstrap.
    with patch("firebase_admin.firestore.client", return_value=object()):
        return import_module(module_name)


def test_canonical_quickbooks_router_imports():
    module = _import_router_without_external_firestore(
        "masyg_extractor.integrations.accounting.quickbooks.router"
    )
    assert hasattr(module, "router")


def test_canonical_xero_router_imports():
    module = _import_router_without_external_firestore(
        "masyg_extractor.integrations.accounting.xero.router"
    )
    assert hasattr(module, "router")


def test_registry_cutover_uses_canonical_provider_routers():
    source = (
        ROOT / "masyg_extractor/integrations/accounting/registry.py"
    ).read_text()

    assert (
        'router_module="masyg_extractor.integrations.accounting.'
        'quickbooks.router"'
        in source
    )
    assert (
        'router_module="masyg_extractor.integrations.accounting.'
        'xero.router"'
        in source
    )
    assert (
        'router_module="masyg_extractor.integration_qb_v5.routers.qb_router"'
        not in source
    )
    assert (
        'router_module="masyg_extractor.integration_v4.routers.xero_router"'
        not in source
    )
