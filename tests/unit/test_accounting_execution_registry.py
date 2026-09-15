import ast
from pathlib import Path

import pytest

from masyg_extractor.integrations.accounting.registry import (
    ACCOUNTING_EXECUTION_ACTION_REGISTRY,
    accounting_execution_route_path,
    get_accounting_execution_action,
)
from masyg_extractor.integrations.accounting.shared.durable_status import (
    resolve_accounting_record_lookup,
)


ROOT = Path(__file__).resolve().parents[2]


EXPECTED_ACTIONS = {
    (
        "quickbooks",
        "create_ar_invoice",
    ): "send-invoice-in-bulk",
    (
        "quickbooks",
        "create_sales_receipt",
    ): "send-salereceipt-in-bulk",
    (
        "xero",
        "create_ar_invoice",
    ): "send-invoice-in-bulk",
    (
        "xero",
        "create_ap_bill",
    ): "send-receipt-in-bulk",
}


def source(relative: str) -> str:
    return (
        ROOT / relative
    ).read_text()


def test_backend_execution_registry_is_exactly_the_supported_write_surface():
    actual = {
        (
            registration.provider,
            registration.accounting_intent,
        ): registration.action
        for registration
        in ACCOUNTING_EXECUTION_ACTION_REGISTRY
    }

    assert actual == EXPECTED_ACTIONS


@pytest.mark.parametrize(
    (
        "provider",
        "intent",
        "action",
    ),
    [
        (
            provider,
            intent,
            action,
        )
        for (
            provider,
            intent,
        ), action
        in EXPECTED_ACTIONS.items()
    ],
)
def test_execution_registry_resolves_supported_actions(
    provider,
    intent,
    action,
):
    registration = (
        get_accounting_execution_action(
            provider,
            intent,
        )
    )

    assert registration.provider == provider
    assert (
        registration.accounting_intent
        == intent
    )
    assert registration.action == action


def test_execution_registry_rejects_unsupported_provider_intent_pairs():
    with pytest.raises(KeyError):
        get_accounting_execution_action(
            "quickbooks",
            "create_ap_bill",
        )

    with pytest.raises(KeyError):
        get_accounting_execution_action(
            "xero",
            "create_sales_receipt",
        )

    with pytest.raises(KeyError):
        get_accounting_execution_action(
            "xero",
            "review_required",
        )


def test_execution_registry_routes_match_existing_provider_routes():
    quickbooks_router = source(
        "masyg_extractor/integrations/accounting/"
        "quickbooks/router.py"
    )

    xero_router = source(
        "masyg_extractor/integrations/accounting/"
        "xero/router.py"
    )

    assert (
        '@router.post("/send-invoice-in-bulk")'
        in quickbooks_router
    )
    assert (
        '@router.post("/send-salereceipt-in-bulk")'
        in quickbooks_router
    )

    assert (
        '@router.post("/send-invoice-in-bulk")'
        in xero_router
    )
    assert (
        '@router.post("/send-receipt-in-bulk")'
        in xero_router
    )

    assert (
        accounting_execution_route_path(
            "quickbooks",
            "create_ar_invoice",
        )
        == (
            "/integrations/quickbooks/"
            "send-invoice-in-bulk"
        )
    )

    assert (
        accounting_execution_route_path(
            "quickbooks",
            "create_sales_receipt",
        )
        == (
            "/integrations/quickbooks/"
            "send-salereceipt-in-bulk"
        )
    )

    assert (
        accounting_execution_route_path(
            "xero",
            "create_ar_invoice",
        )
        == (
            "/integrations/xero/"
            "send-invoice-in-bulk"
        )
    )

    assert (
        accounting_execution_route_path(
            "xero",
            "create_ap_bill",
        )
        == (
            "/integrations/xero/"
            "send-receipt-in-bulk"
        )
    )


def test_every_executable_action_has_a_durable_duplicate_barrier_owner():
    for (
        provider,
        intent,
    ) in EXPECTED_ACTIONS:
        lookup = (
            resolve_accounting_record_lookup(
                provider,
                intent,
            )
        )

        assert lookup.record_type


def test_registry_remains_descriptive_and_provider_write_free():
    registry = source(
        "masyg_extractor/integrations/accounting/"
        "registry.py"
    )

    forbidden = (
        "QuickBooksClientAdapter",
        "XeroClientAdapter",
        "DocumentService",
        "claim_record",
        "finalize_record",
        "mark_record_uncertain",
        "release_record_claim",
        ".request(",
    )

    for token in forbidden:
        assert token not in registry


def test_preflight_uses_backend_execution_registry_as_capability_owner():
    preflight = source(
        "masyg_extractor/integrations/accounting/"
        "shared/batch_preflight.py"
    )

    assert (
        "get_accounting_execution_action("
        in preflight
    )

    tree = ast.parse(preflight)

    assert any(
        isinstance(node, ast.Constant)
        and node.value
        == (
            "Accounting execution capability and durable "
            "status configuration are inconsistent."
        )
        for node in ast.walk(tree)
    )
