from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from typing import Any, Literal

AccountingProviderId = Literal["quickbooks", "xero"]


@dataclass(frozen=True)
class AccountingProviderRegistration:
    provider: AccountingProviderId
    route_prefix: str
    router_module: str


ACCOUNTING_PROVIDER_REGISTRY: tuple[AccountingProviderRegistration, ...] = (
    AccountingProviderRegistration(
        provider="quickbooks",
        route_prefix="/integrations/quickbooks",
        router_module="masyg_extractor.integrations.accounting.quickbooks.router",
    ),
    AccountingProviderRegistration(
        provider="xero",
        route_prefix="/integrations/xero",
        router_module="masyg_extractor.integrations.accounting.xero.router",
    ),
)



@dataclass(frozen=True)
class AccountingExecutionActionRegistration:
    """
    Canonical backend capability for an accounting provider write.

    This registry is deliberately descriptive only:
      - no provider client
      - no Firestore mutation
      - no document creation
      - no HTTP dispatch

    Execution itself remains owned by the existing provider services.
    """

    provider: AccountingProviderId
    accounting_intent: str
    action: str


ACCOUNTING_EXECUTION_ACTION_REGISTRY: tuple[
    AccountingExecutionActionRegistration,
    ...,
] = (
    AccountingExecutionActionRegistration(
        provider="quickbooks",
        accounting_intent="create_ar_invoice",
        action="send-invoice-in-bulk",
    ),
    AccountingExecutionActionRegistration(
        provider="quickbooks",
        accounting_intent="create_sales_receipt",
        action="send-salereceipt-in-bulk",
    ),
    AccountingExecutionActionRegistration(
        provider="xero",
        accounting_intent="create_ar_invoice",
        action="send-invoice-in-bulk",
    ),
    AccountingExecutionActionRegistration(
        provider="xero",
        accounting_intent="create_ap_bill",
        action="send-receipt-in-bulk",
    ),
)


def get_accounting_execution_action(
    provider: str,
    accounting_intent: str,
) -> AccountingExecutionActionRegistration:
    provider = str(
        provider or ""
    ).strip().lower()

    accounting_intent = str(
        accounting_intent or ""
    ).strip()

    # Validate the provider through the existing canonical provider
    # registry rather than allowing the execution registry to become
    # a second provider owner.
    get_accounting_provider(provider)

    for registration in (
        ACCOUNTING_EXECUTION_ACTION_REGISTRY
    ):
        if (
            registration.provider == provider
            and registration.accounting_intent
            == accounting_intent
        ):
            return registration

    raise KeyError(
        (
            provider,
            accounting_intent,
        )
    )


def accounting_execution_route_path(
    provider: str,
    accounting_intent: str,
) -> str:
    registration = (
        get_accounting_execution_action(
            provider,
            accounting_intent,
        )
    )

    provider_registration = (
        get_accounting_provider(
            registration.provider
        )
    )

    return (
        f"{provider_registration.route_prefix}/"
        f"{registration.action}"
    )


def get_accounting_provider(
    provider: AccountingProviderId,
) -> AccountingProviderRegistration:
    for registration in ACCOUNTING_PROVIDER_REGISTRY:
        if registration.provider == provider:
            return registration
    raise KeyError(provider)


def load_accounting_router(
    registration: AccountingProviderRegistration,
) -> Any:
    module = import_module(registration.router_module)
    return module.router


def iter_accounting_routers() -> tuple[Any, ...]:
    return tuple(
        load_accounting_router(registration)
        for registration in ACCOUNTING_PROVIDER_REGISTRY
    )
