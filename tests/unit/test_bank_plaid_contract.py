from pathlib import Path

import pytest

from masyg_extractor.integrations.bank.plaid_client import PlaidClientConfig, PlaidConfigurationError
from masyg_extractor.integrations.bank.service import normalize_account, normalize_transaction


ROOT = Path(__file__).resolve().parents[2]


def _source(relative_path: str) -> str:
    return (ROOT / relative_path).read_text()


def test_bank_router_is_registered_once():
    routes = _source("masyg_extractor/routes/__init__.py")
    assert "from masyg_extractor.integrations.bank.router import router as bank_router" in routes
    assert 'app.include_router(bank_router, prefix="")' in routes


def test_bank_routes_are_authenticated_and_transactions_only():
    router = _source("masyg_extractor/integrations/bank/router.py")
    client = _source("masyg_extractor/integrations/bank/plaid_client.py")
    for endpoint in (
        '@router.post("/link-token")',
        '@router.post("/exchange")',
        '@router.get("/accounts")',
        '@router.post("/transactions/sync")',
    ):
        assert endpoint in router
    assert "Depends(get_current_user_from_cookie)" in router
    assert '"products": ["transactions"]' in client
    assert '"/transactions/sync"' in client
    assert "payment_initiation" not in client
    assert "transfer" not in client


def test_plaid_defaults_to_sandbox_only_outside_production(monkeypatch):
    monkeypatch.setenv("FAST_API_ENV", "development")
    monkeypatch.delenv("PLAID_ENV", raising=False)
    monkeypatch.setenv("PLAID_CLIENT_ID", "client-id")
    monkeypatch.setenv("PLAID_SECRET", "secret")
    config = PlaidClientConfig.from_env()
    assert config.base_url == "https://sandbox.plaid.com"
    assert config.transactions_days == 90
    assert config.country_codes == ("US",)


def test_production_requires_explicit_plaid_environment(monkeypatch):
    monkeypatch.setenv("FAST_API_ENV", "production")
    monkeypatch.delenv("PLAID_ENV", raising=False)
    monkeypatch.setenv("PLAID_CLIENT_ID", "client-id")
    monkeypatch.setenv("PLAID_SECRET", "secret")
    with pytest.raises(PlaidConfigurationError):
        PlaidClientConfig.from_env()


def test_bank_storage_encrypts_access_token():
    repository = _source("masyg_extractor/integrations/bank/repository.py")
    assert '"encryptedAccessToken": encrypted' in repository
    assert '"transactionsCursor": None' in repository
    assert "self.fernet.decrypt" in repository
    assert '"accessToken": access_token' not in repository


def test_normalized_records_are_reconciliation_scoped():
    account = normalize_account(
        {
            "account_id": "acct-1",
            "name": "Checking",
            "balances": {"current": 120.5, "iso_currency_code": "USD"},
        },
        item_id="item-1",
    )
    transaction = normalize_transaction(
        {
            "transaction_id": "tx-1",
            "account_id": "acct-1",
            "name": "Coffee",
            "amount": 4.5,
            "date": "2026-09-14",
            "personal_finance_category": {"primary": "FOOD_AND_DRINK"},
        },
        item_id="item-1",
    )
    assert account["balances"]["current"] == 120.5
    assert transaction["category"] == "FOOD_AND_DRINK"
    assert transaction["amount"] == 4.5
