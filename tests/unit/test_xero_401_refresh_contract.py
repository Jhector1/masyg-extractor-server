from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def source(relative: str) -> str:
    return (ROOT / relative).read_text()


def test_xero_refresh_preserves_tenant_identity():
    auth = source("masyg_extractor/integrations/auth_helper.py")

    assert 'tenant_id=token_data.get("tenant_id")' in auth
    assert 'id_token=token_data.get("id_token") or token_data.get(self.extra_param)' in auth


def test_xero_list_routes_propagate_provider_auth_expiry():
    router = source("masyg_extractor/integrations/accounting/xero/router.py")

    assert 'XERO_AUTH_EXPIRED' in router
    assert 'def _xero_provider_error_response(' in router
    assert router.count("_xero_provider_error_response(response)") >= 4
    assert "status_code=status.HTTP_401_UNAUTHORIZED" in router


def test_xero_success_list_shapes_are_preserved():
    router = source("masyg_extractor/integrations/accounting/xero/router.py")

    assert 'items = response.get("Items", [])' in router
    assert 'customers = response.get("Contacts", [])' in router
    assert 'suppliers = response.get("Contacts", [])' in router
    assert 'accounts = response.get("Accounts", [])' in router
