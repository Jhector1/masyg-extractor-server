from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Query

from masyg_extractor.config.jwt_config import get_current_user_from_cookie
from masyg_extractor.services.user_notifications import (
    list_user_notifications,
    mark_all_notifications_read,
    mark_notification_read,
)


router = APIRouter(prefix="/user/notifications", tags=["notifications"])


def _user_id(current_user: dict) -> str:
    return str(current_user.get("userId") or "").strip()


@router.get("")
async def get_notifications(
    limit: int = Query(default=25, ge=1, le=100),
    current_user: dict = Depends(get_current_user_from_cookie),
):
    notifications, unread_count = await asyncio.to_thread(
        list_user_notifications,
        _user_id(current_user),
        limit=limit,
    )
    return {
        "notifications": notifications,
        "unreadCount": unread_count,
    }


@router.patch("/{notification_id}/read")
async def mark_read(
    notification_id: str,
    current_user: dict = Depends(get_current_user_from_cookie),
):
    await asyncio.to_thread(
        mark_notification_read,
        _user_id(current_user),
        notification_id,
    )
    return {"ok": True}


@router.post("/read-all")
async def mark_all_read(
    current_user: dict = Depends(get_current_user_from_cookie),
):
    count = await asyncio.to_thread(
        mark_all_notifications_read,
        _user_id(current_user),
    )
    return {"ok": True, "markedRead": count}
