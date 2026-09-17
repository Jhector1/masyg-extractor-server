from __future__ import annotations

from fastapi import Depends, HTTPException

from masyg_extractor.config.jwt_config import (
    get_current_user_from_cookie,
)
from masyg_extractor.services.firestore_helpers import (
    document_get,
    get_firestore_client,
)

SUBSCRIPTION_REQUIRED_DETAIL = {
    "code": "SUBSCRIPTION_REQUIRED",
    "message": (
        "An active Masyg subscription is required "
        "for accounting exports."
    ),
}

async def require_active_accounting_subscription(
    current_user: dict = Depends(
        get_current_user_from_cookie
    ),
) -> dict:
    user_id = str(
        current_user.get("userId") or ""
    ).strip()

    if not user_id:
        raise HTTPException(
            status_code=401,
            detail="User not authenticated.",
        )

    try:
        client = await get_firestore_client()
        snapshot = await document_get(
            client.collection("users").document(user_id)
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "SUBSCRIPTION_STATUS_UNAVAILABLE",
                "message": "Subscription status could not be verified.",
            },
        ) from exc

    if not snapshot.exists:
        raise HTTPException(
            status_code=402,
            detail=SUBSCRIPTION_REQUIRED_DETAIL,
        )

    data = snapshot.to_dict() or {}
    if data.get("isSubscribed") is not True:
        raise HTTPException(
            status_code=402,
            detail=SUBSCRIPTION_REQUIRED_DETAIL,
        )

    return current_user
