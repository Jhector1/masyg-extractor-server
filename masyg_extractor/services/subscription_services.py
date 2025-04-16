import time

import stripe
from firebase_admin import firestore
from fastapi import Request, HTTPException
from starlette.concurrency import run_in_threadpool

from masyg_extractor.services.my_log import logger
# Initialize Firestore client and set the collection reference for users.
firestore_db = firestore.client()
ref = firestore_db.collection("users")  # Firestore collection for user documents

def update_firestore_user(user_id: str, is_subscribed: bool, has_used_trial: bool = None, request: Request = None):
    """
    Update Firestore user subscription status.
    """
    update_data = {'isSubscribed': is_subscribed}
    if has_used_trial is not None:
        update_data['hasUsedTrial'] = has_used_trial
        if request and hasattr(request, "session") and "user" in request.session:
            request.session["user"]["hasUsedTrial"] = has_used_trial
    doc_ref = ref.document(user_id)
    doc_ref.update(update_data)


def handle_subscription_created(data: dict, firebase_user: dict, firebase_user_id: str, request: Request = None):
    """
    Handle subscription creation events using Firestore.
    """
    stripe_customer_id = data.get("customer")
    price_id = data["items"]["data"][0]["price"]["id"]
    trial_start = data.get("trial_start")
    trial_end = data.get("trial_end")
    status_ = data.get("status")
    has_used_trial = firebase_user.get('hasUsedTrial', False)

    if status_ == "trialing" and trial_start and trial_end:
        if has_used_trial:

            return {"error": "Free trial already used"}, 400
        else:
            logger.info(f"Free trial activated for user {firebase_user_id}.")
            logger.info(f"Customer {stripe_customer_id} started a trial with price {price_id}.")
            logger.info(f"Trial starts: {time.ctime(trial_start)}, ends: {time.ctime(trial_end)}")
            update_firestore_user(firebase_user_id, is_subscribed=True, has_used_trial=True, request=request)
    else:
        logger.info(f"Plan {price_id} activated for user {firebase_user_id}.")
        update_firestore_user(firebase_user_id, is_subscribed=True, request=request)
    return {"message": "Subscription processed"}

def handle_subscription_deleted(data: dict, firebase_user_id: str):
    """
    Handle subscription deletion events using Firestore.
    """
    logger.info(f"Subscription deleted for user {firebase_user_id}.")
    update_firestore_user(firebase_user_id, is_subscribed=False)
    return {"message": "Subscription deleted"}

def handle_payment_failed(data: dict, firebase_user_id: str):
    """
    Handle payment failure events using Firestore.
    """
    logger.info(f"Payment failed for user {firebase_user_id}.")
    notify_user_of_payment_failure(firebase_user_id)
    update_firestore_user(firebase_user_id, is_subscribed=False)
    return {"message": "Payment failure handled"}

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
