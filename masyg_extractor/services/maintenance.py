# services/maintenance.py
import asyncio
import os
from datetime import datetime, timezone, timedelta
from google.api_core.exceptions import FailedPrecondition
from google.cloud.firestore_v1 import FieldFilter
from firebase_admin import firestore as admin_fs, firestore

from masyg_extractor.services.firestore_helpers import get_firestore_client, document_delete
from masyg_extractor.services.my_log import logger
from masyg_extractor.services.subscription_services import _recompute_is_subscribed

TRASH_TTL_DAYS = 30
db = firestore.client()
def _utc_now():
    return datetime.now(timezone.utc)

def _in_days(d: int):
    return _utc_now() + timedelta(days=d)

async def roll_failed_to_trash():
    client = await get_firestore_client()
    now = _utc_now()

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
            "trashAt": _utc_now(),
            "trashExpiresAt": _in_days(TRASH_TTL_DAYS),
        })
        moved += 1

    if moved:
        await asyncio.to_thread(batch.commit)

    logger.info(f"roll_failed_to_trash: moved={moved}")
    return {"moved": moved}

async def purge_expired_trash():
    client = await get_firestore_client()
    now = _utc_now()

    try:
        q = (
            client.collection_group("files")
            .where(filter=FieldFilter("trashed", "==", True))
            .where(filter=FieldFilter("trashExpiresAt", "<=", now))
            .order_by("trashExpiresAt")
            .limit(500)
        )
        docs = await asyncio.to_thread(lambda: list(q.stream()))
    except FailedPrecondition as e:
        logger.warning("Missing index (create link in error): %s", e)
        return {"purged": 0}

    to_delete = [d.reference for d in docs]

    purged = 0
    for i in range(0, len(to_delete), 450):
        batch = client.batch()
        for ref in to_delete[i:i+450]:
            batch.delete(ref)
            purged += 1
        await asyncio.to_thread(batch.commit)

    logger.info(f"purge_expired_trash: purged={purged}")
    return {"purged": purged}

TRIAL_DAYS = int(os.getenv("TRIAL_DAYS", "30"))

def expire_free_trials():
  now = datetime.now(timezone.utc)
  print(f"[expire_free_trials] sweep @ {now.isoformat()}", flush=True)

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

  print("✅ [expire_free_trials] recompute sweep done.", flush=True)