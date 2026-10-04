from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, Optional

from google.cloud.firestore_v1 import FieldFilter

from masyg_extractor.services.firestore_helpers import get_firestore_client
from masyg_extractor.services.my_log import logger

TRASH_TTL_DAYS = 30
BATCH_DELETE_LIMIT = 450


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def trash_expiry(days: int = TRASH_TTL_DAYS) -> datetime:
    return utc_now() + timedelta(days=days)


def to_utc_datetime(value: Optional[object]) -> Optional[datetime]:
    """Normalize Firestore timestamps, datetimes, and legacy ISO strings to UTC."""
    if value is None:
        return None

    if hasattr(value, "to_datetime"):
        try:
            value = value.to_datetime()
        except Exception:
            return None

    if isinstance(value, datetime):
        current = value
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            current = datetime.fromisoformat(text)
        except ValueError:
            return None
    else:
        return None

    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def effective_expiry(data: Dict[str, Any]) -> Optional[datetime]:
    """Return explicit expiry, or derive it from trashAt for legacy rows."""
    explicit = to_utc_datetime(data.get("trashExpiresAt"))
    if explicit is not None:
        return explicit

    trashed_at = to_utc_datetime(data.get("trashAt"))
    if trashed_at is None:
        return None
    return trashed_at + timedelta(days=TRASH_TTL_DAYS)


def is_expired(data: Dict[str, Any], *, now: Optional[datetime] = None) -> bool:
    expiry = effective_expiry(data)
    if expiry is None:
        return False
    return expiry <= (now or utc_now())


async def _stream(query) -> list:
    return await asyncio.to_thread(lambda: list(query.stream()))


async def _delete_refs(client, refs: Iterable) -> int:
    refs = list(refs)
    deleted = 0
    for index in range(0, len(refs), BATCH_DELETE_LIMIT):
        batch = client.batch()
        chunk = refs[index:index + BATCH_DELETE_LIMIT]
        for ref in chunk:
            batch.delete(ref)
        if chunk:
            await asyncio.to_thread(batch.commit)
            deleted += len(chunk)
    return deleted


async def _delete_group_tree(client, group_doc) -> Dict[str, int]:
    file_docs = await _stream(group_doc.reference.collection("files"))
    file_count = await _delete_refs(client, [doc.reference for doc in file_docs])
    group_count = await _delete_refs(client, [group_doc.reference])
    return {"groups": group_count, "files": file_count}


async def _purge_documents(
    client,
    *,
    group_docs: Iterable,
    file_docs: Iterable,
    now: datetime,
) -> Dict[str, int]:
    purged_groups = 0
    purged_files = 0
    expired_group_paths: set[str] = set()

    for group_doc in group_docs:
        group_data = group_doc.to_dict() or {}
        metadata = group_data.get("metadata", {}) or {}
        if not metadata.get("trashed") or not is_expired(metadata, now=now):
            continue

        counts = await _delete_group_tree(client, group_doc)
        purged_groups += counts["groups"]
        purged_files += counts["files"]
        expired_group_paths.add(group_doc.reference.path)

    file_refs = []
    touched_parent_refs: Dict[str, Any] = {}
    for file_doc in file_docs:
        file_data = file_doc.to_dict() or {}
        if not file_data.get("trashed") or not is_expired(file_data, now=now):
            continue

        parent_group_ref = file_doc.reference.parent.parent
        if parent_group_ref is None or parent_group_ref.path in expired_group_paths:
            continue

        file_refs.append(file_doc.reference)
        touched_parent_refs[parent_group_ref.path] = parent_group_ref

    purged_files += await _delete_refs(client, file_refs)

    # Match the existing permanent-file behavior: remove an empty parent group.
    for group_ref in touched_parent_refs.values():
        remaining = await _stream(group_ref.collection("files").limit(1))
        if not remaining:
            purged_groups += await _delete_refs(client, [group_ref])

    return {
        "purged": purged_groups + purged_files,
        "groups": purged_groups,
        "files": purged_files,
    }


async def purge_expired_trash_for_user(
    user_id: str,
    *,
    client=None,
    now: Optional[datetime] = None,
) -> Dict[str, int]:
    """Catch up one user's Trash, including legacy ISO-string expiries."""
    client = client or await get_firestore_client()
    now = now or utc_now()
    groups_ref = client.collection("users").document(user_id).collection("groups")

    group_docs = await _stream(
        groups_ref.where(filter=FieldFilter("metadata.trashed", "==", True))
    )

    all_groups = await _stream(groups_ref)
    file_docs = []
    for group_doc in all_groups:
        file_docs.extend(
            await _stream(
                group_doc.reference.collection("files").where(
                    filter=FieldFilter("trashed", "==", True)
                )
            )
        )

    result = await _purge_documents(
        client,
        group_docs=group_docs,
        file_docs=file_docs,
        now=now,
    )
    logger.info(
        "purge_expired_trash_for_user user=%s groups=%s files=%s",
        user_id,
        result["groups"],
        result["files"],
    )
    return result


async def purge_expired_trash_globally(
    *,
    client=None,
    now: Optional[datetime] = None,
) -> Dict[str, int]:
    """Scheduled global purge that handles old strings and current timestamps."""
    client = client or await get_firestore_client()
    now = now or utc_now()

    group_docs = await _stream(
        client.collection_group("groups").where(
            filter=FieldFilter("metadata.trashed", "==", True)
        )
    )
    file_docs = await _stream(
        client.collection_group("files").where(
            filter=FieldFilter("trashed", "==", True)
        )
    )

    result = await _purge_documents(
        client,
        group_docs=group_docs,
        file_docs=file_docs,
        now=now,
    )
    logger.info(
        "purge_expired_trash_globally groups=%s files=%s total=%s",
        result["groups"],
        result["files"],
        result["purged"],
    )
    return result
