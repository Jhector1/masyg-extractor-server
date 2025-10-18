import os
import time
from datetime import datetime

import stripe
from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.responses import JSONResponse, RedirectResponse
from firebase_admin import firestore

from masyg_extractor.config.jwt_config import get_current_user_from_cookie
from masyg_extractor.services.subscription_services import *  # Ensure these functions are updated to use Firestore as well
from masyg_extractor.services.subscription_services import _recompute_is_subscribed

# Initialize Firestore client and reference to the "users" collection.
firestore_db = firestore.client()
ref = firestore_db.collection("users")

ENDPOINT_SECRET = os.getenv("MASYG_EXTRACTOR_WEBHOOK_SECRET")
CLIENT_URL = os.getenv("CLIENT_URL")
DOMAIN = os.getenv("DOMAIN")
STRIPE_PRICE_ID = os.getenv("STRIPE_PRICE_ID")
EXTRACTOR_STRIPE_PUBLISHABLE_KEY = os.getenv("EXTRACTOR_STRIPE_PUBLISHABLE_KEY")
BASIC_PRICE_ID = os.getenv("BASIC_PRICE_ID")
PRO_PRICE_ID = os.getenv("PRO_PRICE_ID")

router = APIRouter(prefix="/payment")
# ------------------------------------------------------------------------------
# Payment Endpoints
# ------------------------------------------------------------------------------

@router.get("/config")
async def get_publishable_key():
    """
    Return the publishable key and price IDs for the frontend.
    """
    return {
        "publishableKey": EXTRACTOR_STRIPE_PUBLISHABLE_KEY,
        "basicPrice": BASIC_PRICE_ID,
        "proPrice": PRO_PRICE_ID,
    }

@router.get("/checkout-session")
async def get_checkout_session(sessionId: str):
    """
    Retrieve an existing Checkout Session by its ID.
    """
    try:
        def blocking_retrieve():
            return stripe.checkout.Session.retrieve(sessionId)
        checkout_session_obj = await run_in_threadpool(blocking_retrieve)
        return checkout_session_obj
    except Exception as e:
        logger.error(f"Error retrieving checkout session: {e}")
        raise HTTPException(status_code=400, detail="Error retrieving session")

@router.post("/create-checkout-session")
async def create_checkout_session(request : Request, current_user: dict = Depends(get_current_user_from_cookie)):
    """
    Create a new Checkout Session for the logged-in user's subscription.
    Apply a free trial only if the user has not already used one.
    """
    firebase_user_id = current_user.get("userId")
    if not firebase_user_id:
        raise HTTPException(status_code=401, detail="User not logged in")


    doc_ref = ref.document(firebase_user_id)
    doc = doc_ref.get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail="User not found in Firestore")
    user_data = doc.to_dict()

    stripe_customer_id = user_data.get("stripeCustomerId")
    if not stripe_customer_id:
        try:
            def blocking_create_customer():
                return stripe.Customer.create(
                    email=user_data["email"],
                    name=user_data["username"],
                )
            customer = await run_in_threadpool(blocking_create_customer)
            doc_ref.update({"stripeCustomerId": customer.id})
            stripe_customer_id = customer.id
        except Exception as e:
            logger.error(f"Error creating Stripe customer: {e}")
            raise HTTPException(status_code=400, detail="Failed to create Stripe customer")

    # Check if the user has already used the free trial.
    has_used_trial = user_data.get("hasUsedTrial", False)
    request_data = await request.json()
    free_trial = request_data.get("free_trial", False)
    if free_trial and has_used_trial:
        raise HTTPException(status_code=401, detail="You have already used your free trial. Please choose a paid plan.")

    price_id = STRIPE_PRICE_ID
    domain_url = CLIENT_URL
    plan = "Free Trial" if free_trial else "Monthly"
    try:
        def blocking_retrieve_price():
            return stripe.Price.retrieve(price_id)
        price_object = await run_in_threadpool(blocking_retrieve_price)
        plan_price = 0.00 if free_trial else price_object.unit_amount / 100
    except Exception as e:
        logger.error(f"Error retrieving price details: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve price details")

    try:
        def blocking_create_checkout():
            return stripe.checkout.Session.create(
                payment_method_types=["card"],
                success_url=f"{domain_url}/payment/success?session_id={{CHECKOUT_SESSION_ID}}&plan={plan}&price={plan_price}",
                cancel_url=f"{domain_url}/",
                mode="subscription",
                automatic_tax={"enabled": True},
                customer=stripe_customer_id,
                line_items=[{"price": price_id, "quantity": 1}],
                customer_update={"address": "auto"},
                subscription_data={
                    "trial_period_days": 7 if free_trial and not has_used_trial else None
                },
                client_reference_id=firebase_user_id,  # ✅ helps map user
                metadata={"firebaseUserId": firebase_user_id},  # ✅ another fallback

                consent_collection={"terms_of_service": "required"},
            )
        checkout_session_obj = await run_in_threadpool(blocking_create_checkout)
        return JSONResponse({"url": checkout_session_obj.url, "id": checkout_session_obj.id})
    except Exception as e:
        logger.error(f"Error creating checkout session: {e}")
        raise HTTPException(status_code=500, detail="Failed to create checkout session")

@router.post("/customer-portal")
async def customer_portal(request: Request, current_user: dict = Depends(get_current_user_from_cookie)):
    """
    Redirect the customer to the Stripe customer portal for subscription management.
    """
    form = await request.form()
    checkout_session_id = form.get("sessionId")
    try:
        def blocking_retrieve_session():
            return stripe.checkout.Session.retrieve(checkout_session_id)
        checkout_session_obj = await run_in_threadpool(blocking_retrieve_session)
        def blocking_create_portal():
            return stripe.billing_portal.Session.create(
                customer=checkout_session_obj.customer,
                return_url=DOMAIN,
            )
        portal_session = await run_in_threadpool(blocking_create_portal)
        return RedirectResponse(url=portal_session.url, status_code=303)
    except Exception as e:
        logger.error(f"Error creating customer portal session: {e}")
        raise HTTPException(status_code=400, detail="Failed to create customer portal session")


# from fastapi import  status


# from datetime import datetime
from fastapi import status
# from fastapi.responses import JSONResponse
# from firebase_admin import firestore
#
# from masyg_extractor.config.jwt_config import get_current_user_from_cookie
#
# router = APIRouter(prefix="/payment")
# firestore_db = firestore.client()
users = firestore_db.collection("users")

# from datetime import datetime, timedelta, timezone

TRIAL_DAYS = int(os.getenv("TRIAL_DAYS", "30"))

@router.post("/plan/trial")
async def activate_free_trial(current_user: dict = Depends(get_current_user_from_cookie)):
    uid = current_user.get("userId")
    if not uid:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    trial_doc = users.document(uid).collection("plan").document("trial")
    snapshot = trial_doc.get()
    if snapshot.exists and snapshot.to_dict().get("hasUsed", False):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Free trial already used")

    started = datetime.now(timezone.utc)
    trial_end = started + timedelta(days=TRIAL_DAYS)

    trial_doc.set({
        "hasUsed": True,
        "date": started,       # aware datetime
        "trialEnd": trial_end, # authoritative trial end
    }, merge=True)

    # (optional) mirror for cheap reads
    users.document(uid).update({"trial": {"hasUsed": True, "date": started, "trialEnd": trial_end}})

    # ✅ Recompute derived flag instead of forcing isSubscribed
    _recompute_is_subscribed(uid)

    return JSONResponse({"hasUsed": True, "date": started.isoformat(), "trialEnd": trial_end.isoformat()})


@router.post("/webhook")
async def webhook_received(request: Request):
    """
    Handle Stripe webhook events related to subscriptions and payments.
    """
    payload = await request.body()
    sig_header = request.headers.get("Stripe-Signature")
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, ENDPOINT_SECRET)
    except ValueError as e:
        logger.error(f"Invalid payload: {e}")
        return JSONResponse({"error": "Invalid payload"}, status_code=400)
    except stripe.error.SignatureVerificationError as e:
        logger.error(f"Invalid signature: {e}")
        return JSONResponse({"error": "Invalid signature"}, status_code=400)

    event_type = event["type"]
    obj = event["data"]["object"]

    # ── 1) Identify customer / user robustly ───────────────────────────────────
    customer_id = obj.get("customer")

    # Fallback via subscription lookup (invoice.*, checkout.session.* often have subscription)
    if not customer_id:
        sub_id = obj.get("subscription")
        if sub_id:
            def blocking_get_sub():
                return stripe.Subscription.retrieve(sub_id)
            try:
                sub = await run_in_threadpool(blocking_get_sub)
                customer_id = sub.get("customer")
            except Exception as e:
                logger.warning(f"Could not retrieve subscription {sub_id}: {e}")

    # For checkout.session.* use client_reference_id and/or metadata.firebaseUserId
    client_ref = obj.get("client_reference_id") if event_type.startswith("checkout.session") else None
    meta_uid = (obj.get("metadata") or {}).get("firebaseUserId")

    firebase_user, firebase_user_id = None, None

    if customer_id:
        firebase_user, firebase_user_id = find_firestore_user(customer_id)

    if not firebase_user and client_ref:
        snap = ref.document(client_ref).get()
        if snap.exists:
            firebase_user, firebase_user_id = snap.to_dict(), snap.id

    if not firebase_user and meta_uid:
        snap = ref.document(meta_uid).get()
        if snap.exists:
            firebase_user, firebase_user_id = snap.to_dict(), snap.id

    if not firebase_user:
        # Do NOT return 404 — Stripe will retry forever.
        logger.error(
            f"No Firestore user found (event={event_type}) "
            f"customer={customer_id} client_ref={client_ref} meta_uid={meta_uid}"
        )
        return JSONResponse({"received": True})  # 200 OK to stop retries

    # ── 2) Route events ───────────────────────────────────────────────────────
    if event_type == "customer.subscription.created":
        handle_subscription_created(obj, firebase_user, firebase_user_id, request=request)
    elif event_type == "customer.subscription.deleted":
        handle_subscription_deleted(obj, firebase_user_id)
    elif event_type == "invoice.payment_failed":
        handle_payment_failed(obj, firebase_user_id)
    elif event_type == "customer.subscription.updated":
        handle_subscription_updated(obj, firebase_user_id)
    elif event_type == "checkout.session.completed":
        # Optional: you can mark them as subscribed or just rely on recompute
        pass

    return JSONResponse({"status": "success"})


@router.post("/unsubscribe")
async def unsubscribe(request: Request, current_user: dict = Depends(get_current_user_from_cookie)):
    """
    Unsubscribe the user by canceling their active subscription in Stripe
    and updating their subscription status in Firestore.
    """
    firebase_user_id = current_user.get("userId")
    if not firebase_user_id:
        raise HTTPException(status_code=401, detail="User not logged in")
    doc_ref = ref.document(firebase_user_id)
    doc = doc_ref.get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail="User not found in Firestore")
    user_data = doc.to_dict()
    stripe_customer_id = user_data.get("stripeCustomerId")
    if not stripe_customer_id:
        raise HTTPException(status_code=400, detail="No Stripe customer ID associated with user")

    try:
        def blocking_list_subscriptions():
            return stripe.Subscription.list(customer=stripe_customer_id, status="active")
        subscriptions = await run_in_threadpool(blocking_list_subscriptions)
        for subscription in subscriptions.auto_paging_iter():
            def blocking_delete_subscription(subscription_id):
                return stripe.Subscription.delete(subscription_id)
            await run_in_threadpool(blocking_delete_subscription, subscription.id)
        _recompute_is_subscribed(firebase_user_id)  # ✅ derived from Stripe + trial
        if "user" in request.session:
            request.session["user"]["isSubscribed"] = False
        return JSONResponse({"message": "Successfully unsubscribed"})
    except Exception as e:
        logger.error(f"Error unsubscribing user: {e}")
        raise HTTPException(status_code=400, detail="Failed to unsubscribe")

# @router.get("/payment-method")
# async def get_payment_methods(request: Request, current_user: dict = Depends(get_current_user_from_cookie)):
#     """
#     Retrieve all payment methods for the logged-in user.
#     """
#     firebase_user_id = current_user.get("userId")
#     if not firebase_user_id:
#         raise HTTPException(status_code=401, detail="User not logged in")
#     doc_ref = ref.document(firebase_user_id)
#     doc = doc_ref.get()
#     if not doc.exists:
#         raise HTTPException(status_code=404, detail="User not found in Firestore")
#     user_data = doc.to_dict()
#     stripe_customer_id = user_data.get("stripeCustomerId")
#     if not stripe_customer_id:
#         raise HTTPException(status_code=400, detail="No Stripe customer ID associated with user")
#
#     try:
#         def blocking_list_payment_methods():
#             return stripe.PaymentMethod.list(customer=stripe_customer_id, type="card")
#         payment_methods = await run_in_threadpool(blocking_list_payment_methods)
#         cards = []
#         for method in payment_methods.data:
#             cards.append({
#                 "id": method.id,
#                 "brand": method.card.brand,
#                 "last4": method.card.last4,
#                 "exp_month": method.card.exp_month,
#                 "exp_year": method.card.exp_year,
#             })
#         if cards:
#             return JSONResponse({"paymentMethods": cards})
#         else:
#             return JSONResponse({"message": "No payment methods found"}, status_code=404)
#     except stripe.error.StripeError as e:
#         logger.error(f"Stripe API error: {e}")
#         raise HTTPException(status_code=500, detail="Failed to retrieve payment methods")
#     except Exception as e:
#         logger.error(f"Unexpected error: {e}")
#         raise HTTPException(status_code=500, detail="An unexpected error occurred")

@router.post("/payment-method/delete")
async def delete_payment_method(request: Request):
    """
    Delete a specific payment method for the logged-in user.
    Prevent deletion if the payment method is linked to an active subscription or free trial.
    """
    firebase_user = request.session.get("user")
    if not firebase_user:
        raise HTTPException(status_code=401, detail="User not logged in")
    firebase_user_id = firebase_user.get("userId")
    doc_ref = ref.document(firebase_user_id)
    doc = doc_ref.get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail="User not found in Firestore")
    user_data = doc.to_dict()
    stripe_customer_id = user_data.get("stripeCustomerId")
    if not stripe_customer_id:
        raise HTTPException(status_code=400, detail="No Stripe customer ID associated with user")

    try:
        body = await request.json()
        payment_method_id = body.get("paymentMethodId")
        if not payment_method_id:
            raise HTTPException(status_code=400, detail="Payment method ID is required")

        def blocking_list_subscriptions():
            return stripe.Subscription.list(customer=stripe_customer_id)
        subscriptions = await run_in_threadpool(blocking_list_subscriptions)
        for subscription in subscriptions.data:
            if subscription.default_payment_method == payment_method_id:
                if subscription.status != "canceled":
                    if subscription.trial_end and subscription.trial_end > int(time.time()):
                        raise HTTPException(
                            status_code=400,
                            detail="This payment method is linked to an active free trial. Please update your subscription with a new payment method before deleting this one."
                        )
                    raise HTTPException(
                        status_code=400,
                        detail="This payment method is linked to an active subscription. Please update your subscription with a new payment method before deleting this one."
                    )
        def blocking_detach():
            return stripe.PaymentMethod.detach(payment_method_id)
        await run_in_threadpool(blocking_detach)
        return JSONResponse({"message": "Payment method deleted successfully"})
    except stripe.error.StripeError as e:
        logger.error(f"Stripe API error: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete payment method")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        raise HTTPException(status_code=500, detail="An unexpected error occurred")

@router.post("/subscription/reactivate")
async def reactivate_subscription(request: Request):
    """
    Reactivate a subscription by creating a new one if the old one was canceled.
    """
    firebase_user = request.session.get("user")
    if not firebase_user:
        raise HTTPException(status_code=401, detail="User not logged in")
    firebase_user_id = firebase_user.get("userId")
    doc_ref = ref.document(firebase_user_id)
    doc = doc_ref.get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail="User not found in Firestore")
    user_data = doc.to_dict()
    stripe_customer_id = user_data.get("stripeCustomerId")
    if not stripe_customer_id:
        raise HTTPException(status_code=400, detail="No Stripe customer ID associated with user")

    try:
        def blocking_list_payment_methods():
            return stripe.PaymentMethod.list(customer=stripe_customer_id, type="card")
        payment_methods = await run_in_threadpool(blocking_list_payment_methods)
        if not payment_methods.data:
            raise HTTPException(status_code=400, detail="No valid payment method found. Please add a payment method.")
        def blocking_create_subscription():
            return stripe.Subscription.create(
                customer=stripe_customer_id,
                items=[{"price": STRIPE_PRICE_ID}],
                default_payment_method=payment_methods.data[0].id,
            )
        new_subscription = await run_in_threadpool(blocking_create_subscription)
        return JSONResponse({"message": "Subscription reactivated successfully", "subscriptionId": new_subscription.id})
    except Exception as e:
        logger.error(f"Error reactivating subscription: {e}")
        raise HTTPException(status_code=500, detail="Failed to reactivate subscription")
