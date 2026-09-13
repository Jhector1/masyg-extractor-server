from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

ACTIVE_RUNTIME_FILES = [
    "masyg_extractor/integration_qb_v5/routers/qb_router.py",
    "masyg_extractor/integration_qb_v5/routers/route_helper.py",
    "masyg_extractor/integration_qb_v5/entity_helper.py",
    "masyg_extractor/integration_qb_v5/intergrate/baseAdapter.py",
    "masyg_extractor/integration_qb_v5/intergrate/quickbooks/adapter.py",
    "masyg_extractor/integration_qb_v5/intergrate/quickbooks/services/account_service.py",
    "masyg_extractor/integration_qb_v5/intergrate/quickbooks/services/customer_service.py",
    "masyg_extractor/integration_qb_v5/intergrate/quickbooks/services/document_service.py",
    "masyg_extractor/integration_qb_v5/intergrate/quickbooks/services/invoice_service.py",
    "masyg_extractor/integration_qb_v5/intergrate/quickbooks/services/item_service.py",
    "masyg_extractor/integration_qb_v5/repository/firestore_repository.py",
    "masyg_extractor/integration_qb_v5/core/integration_context.py",
    "masyg_extractor/integration_qb_v5/domain/models.py",
    "masyg_extractor/integration_qb_v5/utils.py",
    "masyg_extractor/integration_v4/routers/xero_router.py",
    "masyg_extractor/integration_v4/routers/route_helper.py",
    "masyg_extractor/integration_v4/entity_helper.py",
    "masyg_extractor/integration_v4/intergrate/baseAdapter.py",
    "masyg_extractor/integration_v4/intergrate/xero/adapter.py",
    "masyg_extractor/integration_v4/intergrate/xero/services/account_service.py",
    "masyg_extractor/integration_v4/intergrate/xero/services/customer_service.py",
    "masyg_extractor/integration_v4/intergrate/xero/services/document_service.py",
    "masyg_extractor/integration_v4/intergrate/xero/services/invoice_service.py",
    "masyg_extractor/integration_v4/intergrate/xero/services/item_service.py",
    "masyg_extractor/integration_v4/repository/firestore_repository.py",
    "masyg_extractor/integration_v4/core/integration_context.py",
    "masyg_extractor/integration_v4/domain/models.py",
    "masyg_extractor/integration_v4/utils.py",
]


def test_canonical_accounting_context_exists():
    source = (
        ROOT
        / "masyg_extractor/integrations/accounting/core/integration_context.py"
    ).read_text()

    assert "class IntegrationContext" in source


def test_active_accounting_runtime_no_longer_imports_v2_or_v3():
    for relative in ACTIVE_RUNTIME_FILES:
        source = (ROOT / relative).read_text()

        assert "masyg_extractor.integration_v2" not in source, relative
        assert "masyg_extractor.integration_v3" not in source, relative


def test_legacy_integration_v3_tree_is_removed():
    assert not (ROOT / "masyg_extractor/integration_v3").exists()


def test_xero_uses_its_current_adapter_and_repository_owners():
    entity_helper = (
        ROOT / "masyg_extractor/integration_v4/entity_helper.py"
    ).read_text()
    adapter = (
        ROOT / "masyg_extractor/integration_v4/intergrate/xero/adapter.py"
    ).read_text()
    account_service = (
        ROOT
        / "masyg_extractor/integration_v4/intergrate/xero/services/account_service.py"
    ).read_text()

    assert (
        "from masyg_extractor.integration_v4.intergrate.baseAdapter "
        "import IntegrationClientAdapter"
    ) in entity_helper
    assert (
        "from masyg_extractor.integrations.accounting.shared.firestore_repository "
        "import QuickBooksFirestoreService"
    ) in entity_helper
    assert (
        "from masyg_extractor.integration_v4.intergrate.baseAdapter "
        "import IntegrationClientAdapter"
    ) in adapter
    assert (
        "from masyg_extractor.integrations.accounting.shared.firestore_repository "
        "import QuickBooksFirestoreService"
    ) in account_service
