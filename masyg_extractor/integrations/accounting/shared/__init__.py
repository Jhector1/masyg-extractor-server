"""Shared accounting helpers with side-effect-free package imports."""

from __future__ import annotations

from importlib import import_module
from typing import Any

# Preserve package-level compatibility exports without eagerly importing
# Firebase-backed repositories merely because a sibling shared module is used.
_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "IntegrationTokenRepository": (".token_repository", "IntegrationTokenRepository"),
    "extract_uuid": (".identifiers", "extract_uuid"),
    "generate_sku": (".sku", "generate_sku"),
    "get_integration_token": (".token_repository", "get_integration_token"),
    "safe_uuid_key": (".identifiers", "safe_uuid_key"),
    "store_integration_token": (".token_repository", "store_integration_token"),
}

__all__ = tuple(_LAZY_EXPORTS)


def __getattr__(name: str) -> Any:
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    module_name, attribute_name = target
    value = getattr(import_module(module_name, __name__), attribute_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
