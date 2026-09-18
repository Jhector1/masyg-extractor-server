from __future__ import annotations

import ast
from pathlib import Path

from masyg_extractor.services.subscription_access import has_active_subscription


ROOT = Path(__file__).resolve().parents[2]


def read(relative: str) -> str:
    return (ROOT / relative).read_text()


def function_source(relative: str, name: str) -> str:
    source = read(relative)
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == name
        ):
            lines = source.splitlines()
            return "\n".join(lines[node.lineno - 1 : node.end_lineno])
    raise AssertionError(f"missing function {name}")


def test_subscription_truth_uses_server_owned_is_subscribed():
    assert has_active_subscription({"isSubscribed": True}) is True
    assert has_active_subscription({"isSubscribed": False}) is False
    assert has_active_subscription({"isSubscribed": "true"}) is False
    assert has_active_subscription({"isSubscribed": 1}) is False
    assert has_active_subscription({}) is False
    assert has_active_subscription(None) is False


def test_local_extract_requires_canonical_subscription_dependency():
    source = function_source(
        "masyg_extractor/routes/data_extractor_routes.py",
        "extract_data",
    )
    route = read("masyg_extractor/routes/data_extractor_routes.py")

    assert (
        "from masyg_extractor.services.subscription_access import "
        "require_active_subscription"
        in route
    )
    assert "Depends(require_active_subscription)" in source
    assert "Depends(get_current_user_from_cookie)" not in source


def test_drive_import_requires_subscription_but_passive_and_lifecycle_routes_do_not():
    router = read(
        "masyg_extractor/integrations/document_sources/google_drive/router.py"
    )

    import_source = function_source(
        "masyg_extractor/integrations/document_sources/google_drive/router.py",
        "google_drive_import",
    )
    status_source = function_source(
        "masyg_extractor/integrations/document_sources/google_drive/router.py",
        "google_drive_status",
    )
    connect_source = function_source(
        "masyg_extractor/integrations/document_sources/google_drive/router.py",
        "google_drive_connect",
    )
    disconnect_source = function_source(
        "masyg_extractor/integrations/document_sources/google_drive/router.py",
        "google_drive_disconnect",
    )

    assert (
        "from masyg_extractor.services.subscription_access import "
        "require_active_subscription"
        in router
    )
    assert "Depends(require_active_subscription)" in import_source
    assert "Depends(require_active_subscription)" not in status_source
    assert "Depends(require_active_subscription)" not in connect_source
    assert "Depends(require_active_subscription)" not in disconnect_source


def test_accounting_guard_is_a_thin_adapter_to_canonical_owner():
    source = read(
        "masyg_extractor/integrations/accounting/shared/subscription_guard.py"
    )

    assert "masyg_extractor.services.subscription_access" in source
    assert (
        "require_active_accounting_subscription = "
        "require_active_subscription"
        in source
    )
    assert "get_firestore_client" not in source
    assert "document_get" not in source

def test_subscription_required_semantics_use_payment_required():
    source = read("masyg_extractor/services/subscription_access.py")

    assert "status.HTTP_402_PAYMENT_REQUIRED" in source
    assert "status.HTTP_403_FORBIDDEN" not in source
    assert 'get("isSubscribed") is True' in source
