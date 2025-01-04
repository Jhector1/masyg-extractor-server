
from flask import jsonify

import time
from firebase_admin import db


ref = db.reference('users')




def handle_subscription_created(data, firebase_user, firebase_user_id):
    """
    Handle subscription creation events.
    """
    stripe_customer_id = data.get("customer")
    price_id = data["items"]["data"][0]["price"]["id"]
    trial_start = data.get("trial_start")
    trial_end = data.get("trial_end")
    status = data.get("status")
    has_used_trial = firebase_user.get('hasUsedTrial', False)

    if status == "trialing" and trial_start and trial_end:
        if has_used_trial:
            print(f"User {firebase_user_id} has already used the free trial.")
            return jsonify({'error': 'Free trial already used'}), 400
        else:
            print(f"Free trial activated for user {firebase_user_id}.")
            print(f"Customer {stripe_customer_id} started a trial with price {price_id}.")
            print(f"Trial starts: {time.ctime(trial_start)}, ends: {time.ctime(trial_end)}")
            update_firebase_user(firebase_user_id, is_subscribed=True, has_used_trial=True)
    else:
        print(f"Plan {price_id} activated for user {firebase_user_id}.")
        update_firebase_user(firebase_user_id, is_subscribed=True)


def handle_subscription_deleted(data, firebase_user_id):
    """
    Handle subscription deletion events.
    """
    print(f"Subscription deleted for user {firebase_user_id}.")
    update_firebase_user(firebase_user_id, is_subscribed=False)


def handle_payment_failed(data, firebase_user_id):
    """
    Handle payment failure events.
    """
    print(f"Payment failed for user {firebase_user_id}.")
    # Optional: Send a notification to the user about the failed payment
    notify_user_of_payment_failure(firebase_user_id)
    # Mark the user as unsubscribed
    update_firebase_user(firebase_user_id, is_subscribed=False)


def notify_user_of_payment_failure(user_id):
    """
    Notify the user of a payment failure.
    """
    # Implement email or in-app notification logic here
    print(f"Notifying user {user_id} of payment failure.")



def find_firebase_user(stripe_customer_id):
    """Find Firebase user by Stripe customer ID."""
    users = ref.get()
    if not users:
        return None, None
    for user_id, user_data in users.items():
        if user_data.get('stripeCustomerId') == stripe_customer_id:
            return user_data, user_id
    return None, None


def update_firebase_user(user_id, is_subscribed, has_used_trial=None):
    """Update Firebase user subscription status."""
    update_data = {'isSubscribed': is_subscribed}
    if has_used_trial is not None:
        update_data['hasUsedTrial'] = has_used_trial
    ref.child(user_id).update(update_data)
    print(f"Firebase user {user_id} updated: {update_data}")