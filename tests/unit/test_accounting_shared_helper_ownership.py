import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

SKU_IMPORTERS = [
    "masyg_extractor/integrations/accounting/quickbooks/services/customer_service.py",
    "masyg_extractor/integrations/accounting/quickbooks/services/document_service.py",
    "masyg_extractor/integrations/accounting/quickbooks/services/item_service.py",
    "masyg_extractor/integrations/accounting/quickbooks/route_helper.py",
    "masyg_extractor/integrations/accounting/xero/services/item_service.py",
    "masyg_extractor/integrations/accounting/xero/route_helper.py",
]

UUID_IMPORTERS = [
    "masyg_extractor/integrations/accounting/quickbooks/services/customer_service.py",
    "masyg_extractor/integrations/accounting/quickbooks/services/document_service.py",
    "masyg_extractor/integrations/accounting/quickbooks/services/item_service.py",
    "masyg_extractor/integrations/accounting/xero/services/customer_service.py",
    "masyg_extractor/integrations/accounting/xero/services/document_service.py",
    "masyg_extractor/integrations/accounting/xero/services/item_service.py",
]

UNUSED_CROSS_PROVIDER_IMPORTS = {
    "masyg_extractor/integrations/accounting/quickbooks/router.py":
        "masyg_extractor.integrations.accounting.xero.client",
    "masyg_extractor/integrations/accounting/xero/entity_helper.py":
        "masyg_extractor.integrations.accounting.quickbooks.client",
    "masyg_extractor/integrations/accounting/xero/adapter.py":
        "masyg_extractor.integrations.accounting.quickbooks.client",
    "masyg_extractor/integrations/accounting/xero/services/account_service.py":
        "masyg_extractor.integrations.accounting.quickbooks.client",
    "masyg_extractor/integrations/accounting/xero/services/item_service.py":
        "masyg_extractor.integrations.accounting.quickbooks.client",
}


def _imports(path: Path):
    tree = ast.parse(path.read_text(), filename=str(path))
    result = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            result.append((node.module or "", tuple(a.name for a in node.names)))
        elif isinstance(node, ast.Import):
            result.extend((a.name, ("*",)) for a in node.names)
    return result


def test_generate_sku_has_shared_owner():
    shared = ROOT / "masyg_extractor/integrations/accounting/shared/sku.py"
    assert "def generate_sku(" in shared.read_text()

    for relative in SKU_IMPORTERS:
        imports = _imports(ROOT / relative)
        assert (
            "masyg_extractor.integrations.accounting.shared.sku",
            ("generate_sku",),
        ) in imports, relative


def test_safe_uuid_key_has_shared_owner():
    shared = ROOT / "masyg_extractor/integrations/accounting/shared/identifiers.py"
    source = shared.read_text()
    assert "def extract_uuid(" in source
    assert "def safe_uuid_key(" in source

    for relative in UUID_IMPORTERS:
        imports = _imports(ROOT / relative)
        assert (
            "masyg_extractor.integrations.accounting.shared.identifiers",
            ("safe_uuid_key",),
        ) in imports, relative


def test_canonical_provider_modules_use_shared_helpers():
    qb_utils = _imports(ROOT / "masyg_extractor/integrations/accounting/quickbooks/utils.py")
    assert (
        "masyg_extractor.integrations.accounting.shared.identifiers",
        ("extract_uuid", "safe_uuid_key"),
    ) in qb_utils

    xero_items = _imports(
        ROOT / "masyg_extractor/integrations/accounting/xero/services/item_service.py"
    )
    assert (
        "masyg_extractor.integrations.accounting.shared.sku",
        ("generate_sku",),
    ) in xero_items


def test_known_unused_cross_provider_imports_are_removed():
    for relative, forbidden_module in UNUSED_CROSS_PROVIDER_IMPORTS.items():
        imports = _imports(ROOT / relative)
        assert all(module != forbidden_module for module, _ in imports), relative
