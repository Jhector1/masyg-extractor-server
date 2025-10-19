import time

import stripe
from firebase_admin import firestore
from fastapi import Request, HTTPException
from starlette.concurrency import run_in_threadpool
import os
from masyg_extractor.services.my_log import logger
# Initialize Firestore client and set the collection reference for users.
firestore_db = firestore.client()
ref = firestore_db.collection("users")  # Firestore collection for user documents


from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple

ACTIVEISH = {"active", "trialing", "past_due", "unpaid"}  # treat these as subscribed

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)

def _ensure_aware(dt: Optional[datetime]) -> Optional[datetime]:
    if not isinstance(dt, datetime):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

def _fetch_trial(user_id: str) -> Tuple[bool, Optional[datetime]]:
    """
    Reads /users/{uid}/plan/trial and returns (has_used, trial_end).
    trial_end is either saved 'trialEnd' or computed from 'date' + TRIAL_DAYS if present.
    """
    TRIAL_DAYS = int(os.getenv("TRIAL_DAYS", "7"))
    t_ref = ref.document(user_id).collection("plan").document("trial")
    snap = t_ref.get()
    if not snap.exists:
        return False, None
    t = snap.to_dict() or {}
    has_used = bool(t.get("hasUsed"))
    start = _ensure_aware(t.get("date"))
    end = _ensure_aware(t.get("trialEnd"))
    if not end and start:
        end = start + timedelta(days=TRIAL_DAYS)
    return has_used, end

def _trial_active(user_id: str) -> bool:
    has_used, trial_end = _fetch_trial(user_id)
    return bool(has_used and trial_end and trial_end > _utcnow())

def _stripe_active(user_doc: dict) -> bool:
    status = (user_doc.get("subscriptionStatus") or "").lower()
    if status not in ACTIVEISH:
        return False
    cancel_at = _ensure_aware(user_doc.get("cancelAt"))
    # if a cancel date exists, it must be in the future
    return bool(not cancel_at or _utcnow() < cancel_at)

# masyg_extractor/services/subscription_services.py
# from datetime import datetime, timezone
# from firebase_admin import firestore
# import stripe
# from masyg_extractor.services.my_log import logger
#
db = firestore.client()
users = db.collection("users")

def _recompute_is_subscribed(uid: str) -> dict:
    """
    Derived truth:
      True if (Stripe subscription active|trialing) OR (trialEnd in the future)
    Mirrors a small 'trial' object into top-level for cheap reads.
    """
    user_ref = users.document(uid)
    user = user_ref.get()
    if not user.exists:
        return {"isSubscribed": False}

    data = user.to_dict() or {}
    stripe_customer_id = data.get("stripeCustomerId")

    # 1) Stripe side
    stripe_active = False
    stripe_status = None
    cancel_at = None

    if stripe_customer_id:
        try:
            subs = stripe.Subscription.list(customer=stripe_customer_id, status="all", limit=3)
            for s in subs.auto_paging_iter():
                # consider "active" or "trialing" as subscribed
                if s.status in ("active", "trialing"):
                    stripe_active = True
                    stripe_status = s.status
                    cancel_at = s.cancel_at and datetime.fromtimestamp(s.cancel_at, tz=timezone.utc)
                    break
        except Exception as e:
            logger.warning(f"Stripe lookup failed for {uid}: {e}")

    # 2) Trial side
    trial_snap = user_ref.collection("plan").document("trial").get()
    trial = trial_snap.to_dict() if trial_snap.exists else {}
    trial_end = trial.get("trialEnd")
    if isinstance(trial_end, datetime) and trial_end.tzinfo is None:
        trial_end = trial_end.replace(tzinfo=timezone.utc)

    now = datetime.now(timezone.utc)
    trial_active = bool(trial_end and trial_end > now)

    # 3) Derived
    is_sub = bool(stripe_active or trial_active)

    patch = {
        "isSubscribed": is_sub,
        "subscriptionStatus": stripe_status or ("trialing" if trial_active else "none"),
        "cancelAt": cancel_at.isoformat() if cancel_at else None,
        # mirror (keeps client logic simple)
        "trial": {
            "hasUsed": bool(trial.get("hasUsed")),
            "date": trial.get("date").isoformat() if isinstance(trial.get("date"), datetime) else None,
            "trialEnd": trial_end.isoformat() if trial_end else None,
            "trialExpired": False if trial_active else bool(trial.get("hasUsed")),
        },
    }
    user_ref.set(patch, merge=True)
    return patch





def update_firestore_user(user_id: str, is_subscribed: bool = None, has_used_trial: bool = None, request: Request = None):
    """
    Update Firestore user fields and then recompute derived isSubscribed.
    Only write direct isSubscribed if you truly want to force it (generally avoid).
    """
    updates = {}
    if has_used_trial is not None:
        updates['hasUsedTrial'] = has_used_trial
        if request and hasattr(request, "session") and "user" in request.session:
            request.session["user"]["hasUsedTrial"] = has_used_trial

    # Avoid forcing isSubscribed here unless absolutely necessary.
    if updates:
        ref.document(user_id).update(updates)

    # Always recompute derived value from Stripe + trial
    _recompute_is_subscribed(user_id)






def handle_subscription_created(data: dict, firebase_user: dict, firebase_user_id: str, request: Request = None):
    price_id = data["items"]["data"][0]["price"]["id"]
    status_ = (data.get("status") or "").lower()
    trial_start = data.get("trial_start")
    trial_end = data.get("trial_end")

    # Persist Stripe subscription fields on the user
    ref.document(firebase_user_id).update({
        "subscriptionId": data.get("id"),
        "subscriptionStatus": status_,
        "currentPeriodEnd": datetime.fromtimestamp(data.get("current_period_end", 0), tz=timezone.utc) if data.get("current_period_end") else None,
        "cancelAt": datetime.fromtimestamp(data.get("cancel_at", 0), tz=timezone.utc) if data.get("cancel_at") else None,
        "cancelAtPeriodEnd": bool(data.get("cancel_at_period_end")),
        "priceId": price_id,
        "subscriptionUpdatedAt": _utcnow(),
    })

    # If Stripe is providing a trial now, you may (optionally) write/mirror a trialEnd
    if status_ == "trialing" and trial_end:
        # Respect “already used trial” policy if you enforce it; otherwise just record state.
        # If you mirror to the trial subdoc, do it here; otherwise skip.
        pass

    # Finally recompute
    _recompute_is_subscribed(firebase_user_id)
    return {"message": "Subscription processed"}

def handle_subscription_deleted(data: dict, firebase_user_id: str):
    # Mark Stripe subscription fields as cancelled, then recompute
    ref.document(firebase_user_id).update({
        "subscriptionStatus": "canceled",
        "subscriptionId": None,
        "cancelAt": None,
        "cancelAtPeriodEnd": False,
        "subscriptionUpdatedAt": _utcnow(),
    })
    _recompute_is_subscribed(firebase_user_id)
    return {"message": "Subscription deleted"}

def handle_payment_failed(data: dict, firebase_user_id: str):
    # Do NOT immediately set isSubscribed=False. Let status changes (updated/deleted) drive it.
    ref.document(firebase_user_id).update({"lastPaymentFailedAt": _utcnow()})
    _recompute_is_subscribed(firebase_user_id)
    notify_user_of_payment_failure(firebase_user_id)
    return {"message": "Payment failure handled"}

def handle_subscription_updated(data: dict, firebase_user_id: str):
    status_ = (data.get("status") or "").lower()
    ref.document(firebase_user_id).update({
        "subscriptionStatus": status_,
        "currentPeriodEnd": datetime.fromtimestamp(data.get("current_period_end", 0), tz=timezone.utc) if data.get("current_period_end") else None,
        "cancelAt": datetime.fromtimestamp(data.get("cancel_at", 0), tz=timezone.utc) if data.get("cancel_at") else None,
        "cancelAtPeriodEnd": bool(data.get("cancel_at_period_end")),
        "subscriptionUpdatedAt": _utcnow(),
    })
    _recompute_is_subscribed(firebase_user_id)






def notify_user_of_payment_failure(user_id: str):
    """
    Notify the user of a payment failure.
    """
    logger.info(f"Notifying user {user_id} of payment failure.")

def find_firestore_user(stripe_customer_id: str):
    """
    Find a Firestore user by Stripe customer ID.
    Returns a tuple (user_data, user_id) if found, otherwise (None, None).
    """
    query = ref.where('stripeCustomerId', '==', stripe_customer_id).limit(1).stream()
    for doc in query:
        user_data = doc.to_dict()
        return user_data, doc.id
    return None, None

# ------------------------------------------------------------------------------
# Stripe Helper (Async Wrapper)
# ------------------------------------------------------------------------------
async def delete_stripe_customer_data(customer_id: str):
    """
    Delete a Stripe customer asynchronously using run_in_threadpool.
    If the customer does not exist, log a warning and skip deletion.
    """
    try:
        # Step 1: Verify if the Stripe customer exists.
        def blocking_check_customer():
            try:
                return stripe.Customer.retrieve(customer_id)
            except stripe.error.InvalidRequestError as e:
                # If the error message indicates the customer doesn't exist, return None.
                if "No such customer" in str(e):
                    return None
                raise

        customer = await run_in_threadpool(blocking_check_customer)
        if customer is None:
            # Log a warning and skip deletion if the customer doesn't exist.
            logger.warning(f"Stripe customer {customer_id} does not exist. Skipping deletion.")
            return {"status": "skipped", "message": f"Stripe customer {customer_id} not found, deletion skipped."}

        # Step 2: Delete the customer if it exists.
        def blocking_delete():
            return stripe.Customer.delete(customer_id)

        deleted_customer = await run_in_threadpool(blocking_delete)
        if not deleted_customer.get("deleted", False):
            raise HTTPException(status_code=500, detail="Failed to delete Stripe customer.")

        return {
            "status": "success",
            "message": f"Customer {customer_id} deleted and active subscriptions cancelled.",
            "deleted": True
        }

    except stripe.error.StripeError as e:
        logger.error(f"Stripe error when deleting customer {customer_id}: {e}")
        raise HTTPException(status_code=400, detail=f"Stripe error: {str(e)}")
    except Exception as e:
        logger.error(f"Internal error when deleting customer {customer_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")
