from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _source(relative_path: str) -> str:
    return (ROOT / relative_path).read_text()


def test_route_tests_override_the_current_cookie_auth_dependency():
    rename_group = _source(
        "tests/firestore_change_update_test/test_rename_group.py"
    )
    image_extractor = _source("tests/image_extractor_test.py")

    for source in (rename_group, image_extractor):
        assert (
            "from masyg_extractor.config.jwt_config "
            "import get_current_user_from_cookie"
        ) in source
        assert "get_firebase_user" not in source


def test_user_route_test_targets_the_current_fastapi_app_and_httpx_transport():
    user_test = _source("tests/test_user_routes/test_user.py")
    sync_client = _source("test_support/sync_asgi_client.py")

    assert "from server import app" in user_test
    assert "asgi_app" not in user_test
    assert "SyncASGIClient" in user_test

    # The user-route tests should use the project-owned synchronous adapter,
    # while modern httpx ASGITransport remains owned by that adapter.
    assert "ASGITransport" not in user_test
    assert "AsyncClient" not in user_test
    assert "TestClient" not in user_test

    assert "httpx.ASGITransport" in sync_client
    assert "httpx.AsyncClient" in sync_client


def test_live_quickbooks_test_contract_targets_canonical_owner():
    contract = _source("tests/unit/test_active_quickbooks_contracts.py")

    assert "integrations/accounting/quickbooks" in contract
    assert "test_canonical_invoice_service_keeps_async_contract" in contract


def test_accounting_registry_uses_canonical_quickbooks_router():
    registry = _source(
        "masyg_extractor/integrations/accounting/registry.py"
    )

    assert 'provider="quickbooks"' in registry
    assert (
        'router_module="masyg_extractor.integrations.accounting.'
        'quickbooks.router"'
    ) in registry
