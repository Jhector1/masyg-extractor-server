from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from masyg_extractor.integrations.accounting.registry import (
    ACCOUNTING_PROVIDER_REGISTRY,
    get_accounting_provider,
    iter_accounting_routers,
    load_accounting_router,
)


def _source(relative_path: str) -> str:
    root = Path(__file__).resolve().parents[2]
    return (root / relative_path).read_text()


def test_accounting_provider_registry_has_one_ordered_owner_per_provider():
    assert [
        registration.provider
        for registration in ACCOUNTING_PROVIDER_REGISTRY
    ] == ["quickbooks", "xero"]

    assert [
        registration.route_prefix
        for registration in ACCOUNTING_PROVIDER_REGISTRY
    ] == [
        "/integrations/quickbooks",
        "/integrations/xero",
    ]


def test_registry_points_to_current_mounted_provider_modules_without_importing_them():
    quickbooks = get_accounting_provider("quickbooks")
    xero = get_accounting_provider("xero")

    assert (
        quickbooks.router_module
        == "masyg_extractor.integrations.accounting.quickbooks.router"
    )
    assert (
        xero.router_module
        == "masyg_extractor.integrations.accounting.xero.router"
    )


def test_provider_router_loading_is_lazy_and_returns_router_attribute():
    registration = get_accounting_provider("quickbooks")
    sentinel_router = object()

    with patch(
        "masyg_extractor.integrations.accounting.registry.import_module",
        return_value=SimpleNamespace(router=sentinel_router),
    ) as import_module:
        assert load_accounting_router(registration) is sentinel_router

    import_module.assert_called_once_with(registration.router_module)


def test_iter_accounting_routers_preserves_registry_order():
    quickbooks_router = object()
    xero_router = object()

    def fake_import(module_name: str):
        if module_name.endswith("integrations.accounting.quickbooks.router"):
            return SimpleNamespace(router=quickbooks_router)
        if module_name.endswith("integrations.accounting.xero.router"):
            return SimpleNamespace(router=xero_router)
        raise AssertionError(module_name)

    with patch(
        "masyg_extractor.integrations.accounting.registry.import_module",
        side_effect=fake_import,
    ):
        assert iter_accounting_routers() == (
            quickbooks_router,
            xero_router,
        )


def test_application_route_composition_uses_canonical_accounting_registry():
    routes = _source("masyg_extractor/routes/__init__.py")

    assert (
        "from masyg_extractor.integrations.accounting.registry "
        "import iter_accounting_routers"
    ) in routes
    assert "for accounting_router in iter_accounting_routers():" in routes
    assert 'app.include_router(accounting_router, prefix="")' in routes

    assert "quickbook_router" not in routes
    assert "xero_router" not in routes


def test_registry_has_no_eager_active_router_imports():
    registry = _source(
        "masyg_extractor/integrations/accounting/registry.py"
    )

    assert (
        "from masyg_extractor.integrations.accounting.quickbooks.router"
        not in registry
    )
    assert (
        "from masyg_extractor.integrations.accounting.xero.router"
        not in registry
    )
    assert "import_module(registration.router_module)" in registry


def test_frontend_required_provider_prefixes_remain_stable():
    quickbooks = get_accounting_provider("quickbooks")
    xero = get_accounting_provider("xero")

    assert quickbooks.route_prefix == "/integrations/quickbooks"
    assert xero.route_prefix == "/integrations/xero"
