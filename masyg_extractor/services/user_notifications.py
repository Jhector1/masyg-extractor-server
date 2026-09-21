from __future__ import annotations

import asyncio
import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any

from firebase_admin import firestore

from masyg_extractor.utils.extensions import sio


USER_NOTIFICATION_EVENT = "user-notification"
NOTIFICATIONS_COLLECTION = "notifications"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _notification_ref(user_id: str, notification_id: str):
    return (
        firestore.client()
        .collection("users")
        .document(user_id)
        .collection(NOTIFICATIONS_COLLECTION)
        .document(notification_id)
    )


def _notification_id(dedupe_key: str | None) -> str:
    normalized = str(dedupe_key or "").strip()
    if normalized:
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return uuid.uuid4().hex


def _iso(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value) if value is not None else None


def _serialize(notification_id: str, data: dict[str, Any]) -> dict[str, Any]:
    entity = data.get("entity")
    return {
        "id": notification_id,
        "type": str(data.get("type") or ""),
        "source": str(data.get("source") or ""),
        "severity": str(data.get("severity") or "info"),
        "title": str(data.get("title") or "Notification"),
        "message": str(data.get("message") or ""),
        "entity": entity if isinstance(entity, dict) else None,
        "createdAt": _iso(data.get("createdAt")),
        "readAt": _iso(data.get("readAt")),
    }


def create_user_notification(
    *,
    user_id: str,
    type: str,
    source: str,
    severity: str,
    title: str,
    message: str,
    entity: dict[str, str] | None = None,
    dedupe_key: str | None = None,
) -> tuple[dict[str, Any], bool]:
    normalized_user = str(user_id or "").strip()
    if not normalized_user:
        raise ValueError("user_id is required")

    notification_id = _notification_id(dedupe_key)
    ref = _notification_ref(normalized_user, notification_id)
    db = firestore.client()
    transaction = db.transaction()

    @firestore.transactional
    def _create(txn):
        snap = ref.get(transaction=txn)
        if snap.exists:
            return _serialize(notification_id, snap.to_dict() or {}), False

        payload = {
            "type": str(type or "").strip(),
            "source": str(source or "").strip(),
            "severity": str(severity or "info").strip(),
            "title": str(title or "Notification").strip(),
            "message": str(message or "").strip(),
            "entity": entity or None,
            "dedupeKey": str(dedupe_key or "").strip() or None,
            "createdAt": _utcnow(),
            "readAt": None,
        }
        txn.set(ref, payload)
        return _serialize(notification_id, payload), True

    return _create(transaction)


async def publish_user_notification(**kwargs) -> dict[str, Any]:
    payload, created = await asyncio.to_thread(create_user_notification, **kwargs)

    if created:
        await sio.emit(
            USER_NOTIFICATION_EVENT,
            payload,
            room=f"user:{kwargs['user_id']}",
        )

    return payload


def list_user_notifications(
    user_id: str,
    *,
    limit: int = 25,
) -> tuple[list[dict[str, Any]], int]:
    normalized_user = str(user_id or "").strip()
    if not normalized_user:
        return [], 0

    collection = (
        firestore.client()
        .collection("users")
        .document(normalized_user)
        .collection(NOTIFICATIONS_COLLECTION)
    )

    safe_limit = min(max(int(limit), 1), 100)
    recent = list(
        collection.order_by(
            "createdAt",
            direction=firestore.Query.DESCENDING,
        )
        .limit(safe_limit)
        .stream()
    )
    unread = list(collection.where("readAt", "==", None).stream())

    return (
        [_serialize(snap.id, snap.to_dict() or {}) for snap in recent],
        len(unread),
    )


def mark_notification_read(user_id: str, notification_id: str) -> None:
    normalized_user = str(user_id or "").strip()
    normalized_notification = str(notification_id or "").strip()
    if not normalized_user or not normalized_notification:
        return

    ref = _notification_ref(normalized_user, normalized_notification)
    snap = ref.get()
    if not snap.exists:
        return
    if (snap.to_dict() or {}).get("readAt") is None:
        ref.update({"readAt": _utcnow()})


def mark_all_notifications_read(user_id: str) -> int:
    normalized_user = str(user_id or "").strip()
    if not normalized_user:
        return 0

    collection = (
        firestore.client()
        .collection("users")
        .document(normalized_user)
        .collection(NOTIFICATIONS_COLLECTION)
    )
    unread = list(collection.where("readAt", "==", None).stream())
    if not unread:
        return 0

    db = firestore.client()
    total = 0
    for start in range(0, len(unread), 400):
        batch = db.batch()
        chunk = unread[start : start + 400]
        now = _utcnow()
        for snap in chunk:
            batch.update(snap.reference, {"readAt": now})
        batch.commit()
        total += len(chunk)

    return total
