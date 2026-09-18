from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fastapi import Depends, HTTPException, status

from masyg_extractor.config.jwt_config import get_current_user_from_cookie
from masyg_extractor.services.firestore_helpers import (
    document_get,
    get_firestore_client,
)


SUBSCRIPTION_REQUIRED_DETAIL = {
    "code": "SUBSCRIPTION_REQUIRED",
    "message": "An active Masyg subscription is required for this action.",
}

SUBSCRIPTION_STATUS_UNAVAILABLE_DETAIL = {
    "code": "SUBSCRIPTION_STATUS_UNAVAILABLE",
    "message": "Subscription status could not be verified.",
}


def has_active_subscription(user_data: Mapping[str, Any] | None) -> bool:
    return (user_data or {}).get("isSubscribed") is True


async def require_active_subscription(
    current_user: dict = Depends(get_current_user_from_cookie),
) -> dict:
    """
    Canonical paid-action entitlement dependency.

    Authentication comes from the normal MASYG cookie dependency, but
    subscription authority is re-read from Firestore for every protected
    action so a stale JWT claim cannot keep paid mutations enabled.
    """
    user_id = str(current_user.get("userId") or "").strip()
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not authenticated.",
        )

    try:
        client = await get_firestore_client()
        snapshot = await document_get(
            client.collection("users").document(user_id)
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=SUBSCRIPTION_STATUS_UNAVAILABLE_DETAIL,
        ) from exc

    if not getattr(snapshot, "exists", False):
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=SUBSCRIPTION_REQUIRED_DETAIL,
        )

    if not has_active_subscription(snapshot.to_dict() or {}):
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=SUBSCRIPTION_REQUIRED_DETAIL,
        )

    return current_user
