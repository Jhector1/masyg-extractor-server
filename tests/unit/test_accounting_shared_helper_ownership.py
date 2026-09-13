import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

SKU_IMPORTERS = [
    "masyg_extractor/integration_qb_v5/intergrate/quickbooks/services/customer_service.py",
    "masyg_extractor/integration_qb_v5/intergrate/quickbooks/services/document_service.py",
    "masyg_extractor/integration_qb_v5/intergrate/quickbooks/services/item_service.py",
    "masyg_extractor/integration_qb_v5/routers/route_helper.py",
    "masyg_extractor/integration_v4/intergrate/xero/services/item_service.py",
    "masyg_extractor/integration_v4/routers/route_helper.py",
]

UUID_IMPORTERS = [
    "masyg_extractor/integration_qb_v5/intergrate/quickbooks/services/customer_service.py",
    "masyg_extractor/integration_qb_v5/intergrate/quickbooks/services/document_service.py",
    "masyg_extractor/integration_qb_v5/intergrate/quickbooks/services/item_service.py",
    "masyg_extractor/integration_v4/intergrate/xero/services/customer_service.py",
    "masyg_extractor/integration_v4/intergrate/xero/services/document_service.py",
    "masyg_extractor/integration_v4/intergrate/xero/services/item_service.py",
]

UNUSED_CROSS_PROVIDER_IMPORTS = {
    "masyg_extractor/integration_qb_v5/routers/qb_router.py":
        "masyg_extractor.integrations.xero.xero_client",
    "masyg_extractor/integration_v4/entity_helper.py":
        "masyg_extractor.integrations.quickbooks.quickbooks_client",
    "masyg_extractor/integration_v4/intergrate/xero/adapter.py":
        "masyg_extractor.integrations.quickbooks.quickbooks_client",
    "masyg_extractor/integration_v4/intergrate/xero/services/account_service.py":
        "masyg_extractor.integrations.quickbooks.quickbooks_client",
    "masyg_extractor/integration_v4/intergrate/xero/services/item_service.py":
        "masyg_extractor.integrations.quickbooks.quickbooks_client",
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


def test_legacy_provider_modules_reexport_shared_helpers():
    qb_utils = _imports(ROOT / "masyg_extractor/integration_qb_v5/utils.py")
    assert (
        "masyg_extractor.integrations.accounting.shared.identifiers",
        ("extract_uuid", "safe_uuid_key"),
    ) in qb_utils

    xero_items = _imports(
        ROOT / "masyg_extractor/integrations/xero/services/item_services.py"
    )
    assert (
        "masyg_extractor.integrations.accounting.shared.sku",
        ("generate_sku",),
    ) in xero_items


def test_known_unused_cross_provider_imports_are_removed():
    for relative, forbidden_module in UNUSED_CROSS_PROVIDER_IMPORTS.items():
        imports = _imports(ROOT / relative)
        assert all(module != forbidden_module for module, _ in imports), relative
