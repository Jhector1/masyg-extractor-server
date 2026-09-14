from pathlib import Path

from masyg_extractor.integrations.bank.service import normalize_item_status

ROOT = Path(__file__).resolve().parents[2]


def source(relative_path: str) -> str:
    return (ROOT / relative_path).read_text()


def test_update_mode_does_not_request_products_again():
    client = source("masyg_extractor/integrations/bank/plaid_client.py")
    start = client.index("async def create_update_link_token")
    end = client.index("async def exchange_public_token", start)
    block = client[start:end]
    assert '"access_token": access_token' in block
    assert '"products"' not in block
    assert '"transactions"' not in block


def test_disconnect_revokes_plaid_then_deletes_local_transactions():
    client = source("masyg_extractor/integrations/bank/plaid_client.py")
    service = source("masyg_extractor/integrations/bank/service.py")
    repository = source("masyg_extractor/integrations/bank/repository.py")
    assert '"/item/remove"' in client
    block = service[
        service.index("async def disconnect"):
        service.index("async def _collect_pages")
    ]
    assert "await self.client.remove_item(access_token)" in block
    assert "self.repository.delete_item" in block
    assert '.collection("transactions").stream()' in repository


def test_item_health_detects_login_required():
    result = normalize_item_status(
        {
            "item": {"error": {"error_code": "ITEM_LOGIN_REQUIRED"}},
            "status": {"transactions": {}},
        }
    )
    assert result["health"] == "needs_attention"
    assert result["needs_attention"] is True


def test_lifecycle_routes_remain_authenticated():
    router = source("masyg_extractor/integrations/bank/router.py")
    assert '@router.post("/items/{item_id}/link-token")' in router
    assert '@router.delete("/items/{item_id}")' in router
    assert '@router.get("/transactions")' in router
    assert router.count("Depends(get_current_user_from_cookie)") >= 7


def test_accounts_resolves_item_health_before_provider_account_fetch():
    from pathlib import Path

    service = Path(
        "masyg_extractor/integrations/bank/service.py"
    ).read_text()

    start = service.index("    async def accounts(")
    end = service.index("    async def transactions(", start)
    block = service[start:end]

    status_pos = block.index(
        "item_status = await self.refresh_item_status(item_id)"
    )
    accounts_pos = block.index(
        "response = await self.client.get_accounts(access_token)"
    )

    assert status_pos < accounts_pos
    assert 'if item_status["needs_attention"]:' in block
    assert 'item.get("accounts") or []' in block
    assert "public_items.append(self.repository.public_item(refreshed))" in block
