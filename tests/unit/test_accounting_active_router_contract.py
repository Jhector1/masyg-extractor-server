from pathlib import Path


def _source(relative_path: str) -> str:
    root = Path(__file__).resolve().parents[2]
    return (root / relative_path).read_text()


def test_active_accounting_routers_keep_auth_router_ownership():
    quickbooks = _source(
        "masyg_extractor/integrations/accounting/quickbooks/router.py"
    )
    xero = _source(
        "masyg_extractor/integrations/accounting/xero/router.py"
    )

    assert (
        "masyg_extractor.integrations.accounting.quickbooks."
        "authentication.quickbook_auth import router as auth_router"
    ) in quickbooks
    assert (
        "masyg_extractor.integrations.accounting.xero."
        "authentication.xero_auth import router as auth_router"
    ) in xero

    assert "router.include_router(auth_router" in quickbooks
    assert "router.include_router(auth_router" in xero


def test_active_provider_prefixes_do_not_move_in_registry_refactor():
    quickbooks = _source(
        "masyg_extractor/integrations/accounting/quickbooks/router.py"
    )
    xero = _source(
        "masyg_extractor/integrations/accounting/xero/router.py"
    )

    assert 'APIRouter(prefix="/integrations/quickbooks")' in quickbooks
    assert 'APIRouter(prefix="/integrations/xero")' in xero


def test_active_bulk_submission_endpoints_remain_provider_owned():
    quickbooks = _source(
        "masyg_extractor/integrations/accounting/quickbooks/router.py"
    )
    xero = _source(
        "masyg_extractor/integrations/accounting/xero/router.py"
    )

    assert '"/send-invoice-in-bulk"' in quickbooks
    assert '"/send-salereceipt-in-bulk"' in quickbooks

    assert '"/send-invoice-in-bulk"' in xero
    assert '"/send-receipt-in-bulk"' in xero


def test_active_option_endpoints_remain_available_to_shared_frontend_registry():
    quickbooks = _source(
        "masyg_extractor/integrations/accounting/quickbooks/router.py"
    )
    xero = _source(
        "masyg_extractor/integrations/accounting/xero/router.py"
    )

    for endpoint in (
        '"/get-items"',
        '"/get-customers"',
        '"/get-vendors"',
        '"/get-accounts"',
    ):
        assert endpoint in quickbooks

    for endpoint in (
        '"/get-items"',
        '"/get-customers"',
        '"/get-supplier"',
        '"/get-accounts"',
    ):
        assert endpoint in xero
