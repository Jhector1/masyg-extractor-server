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
