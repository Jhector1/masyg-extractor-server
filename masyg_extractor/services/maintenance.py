# services/maintenance.py
import asyncio
import os
from datetime import datetime, timezone, timedelta
from google.api_core.exceptions import FailedPrecondition
from google.cloud.firestore_v1 import FieldFilter
from firebase_admin import firestore as admin_fs, firestore

from masyg_extractor.services.firestore_helpers import get_firestore_client
from masyg_extractor.services.my_log import logger
from masyg_extractor.services.trash_retention import (
    TRASH_TTL_DAYS,
    purge_expired_trash_globally,
    trash_expiry,
    utc_now,
)
from masyg_extractor.services.subscription_services import _recompute_is_subscribed

db = firestore.client()

async def roll_failed_to_trash():
    client = await get_firestore_client()
    now = utc_now()

    try:
        q = (
            client.collection_group("files")
            .where(filter=FieldFilter("status", "==", "failed"))
            .where(filter=FieldFilter("trashed", "==", False))
            .where(filter=FieldFilter("failedUntil", "<=", now))
            .order_by("failedUntil")     # required with range filter
            .limit(500)
        )
        docs = await asyncio.to_thread(lambda: list(q.stream()))
    except FailedPrecondition as e:
        logger.warning("Missing index (create link in error): %s", e)
        return {"moved": 0}

    moved = 0
    batch = client.batch()

    for d in docs:
        batch.update(d.reference, {
            "trashed": True,
            "trash_reason": "auto_failed_rollover",
            "trashAt": utc_now(),
            "trashExpiresAt": trash_expiry(TRASH_TTL_DAYS),
        })
        moved += 1

    if moved:
        await asyncio.to_thread(batch.commit)

    logger.info(f"roll_failed_to_trash: moved={moved}")
    return {"moved": moved}

async def purge_expired_trash():
    result = await purge_expired_trash_globally()
    logger.info(
        "purge_expired_trash: purged=%s groups=%s files=%s",
        result["purged"],
        result["groups"],
        result["files"],
    )
    return result

TRIAL_DAYS = int(os.getenv("TRIAL_DAYS", "30"))

def expire_free_trials():
  now = datetime.now(timezone.utc)


  for user_snap in db.collection("users").stream():
    uid = user_snap.id
    user_ref = db.collection("users").document(uid)
    trial_ref = user_ref.collection("plan").document("trial")

    t_snap = trial_ref.get()
    if not t_snap.exists:
      # still recompute for paid users even if no trial doc
      _recompute_is_subscribed(uid)
      continue

    t = t_snap.to_dict() or {}
    start_dt = t.get("date")
    end_dt = t.get("trialEnd")

    # normalize to aware UTC
    if isinstance(start_dt, datetime) and start_dt.tzinfo is None:
      start_dt = start_dt.replace(tzinfo=timezone.utc)
    if isinstance(end_dt, datetime) and end_dt.tzinfo is None:
      end_dt = end_dt.replace(tzinfo=timezone.utc)

    # if trialEnd missing, derive from start
    if not end_dt and isinstance(start_dt, datetime):
      end_dt = start_dt + timedelta(days=TRIAL_DAYS)

    # optional: mark an explicit flag when trial is over (for UI/debug)
    if end_dt and end_dt <= now and not t.get("trialExpired"):
      trial_ref.set({"trialExpired": True}, merge=True)

    # ✅ key: never force isSubscribed here; always recompute from trial+Stripe
    _recompute_is_subscribed(uid)