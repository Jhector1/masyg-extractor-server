from __future__ import annotations

from masyg_extractor.services.subscription_access import (
    SUBSCRIPTION_REQUIRED_DETAIL,
    SUBSCRIPTION_STATUS_UNAVAILABLE_DETAIL,
    require_active_subscription,
)


# Compatibility name retained for existing QuickBooks/Xero route imports.
# Authorization ownership is canonical in services.subscription_access.
require_active_accounting_subscription = require_active_subscription


__all__ = [
    "SUBSCRIPTION_REQUIRED_DETAIL",
    "SUBSCRIPTION_STATUS_UNAVAILABLE_DETAIL",
    "require_active_accounting_subscription",
]
