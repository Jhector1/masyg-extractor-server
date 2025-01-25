
from flask import  request, redirect, Blueprint, session
import os
import stripe

from datetime import datetime
import logging
from services.subscription_services import *


ENDPOINT_SECRET = os.getenv('MASYG_EXTRACTOR_WEBHOOK_SECRET')
# Setup Stripe
# stripe.set_app_info(
#     'stripe-samples/checkout-single-subscription',
#     version='0.0.1',
#     url='https://github.com/stripe-samples/checkout-single-subscription'
# )
# stripe.api_key = os.getenv('MASYG_EXTRACTOR_STRIPE_SECRET_KEY')

# Firebase reference for users
ref = db.reference('users')

payment = Blueprint('payment', __name__)
# Configure logging
# logging.basicConfig(level=logging.INFO)

@payment.route('/config', methods=['GET'])
def get_publishable_key():
    """Return the publishable key and price IDs for the frontend."""
    return jsonify({
        'publishableKey': os.getenv('EXTRACTOR_STRIPE_PUBLISHABLE_KEY'),
        'basicPrice': os.getenv('BASIC_PRICE_ID'),
        'proPrice': os.getenv('PRO_PRICE_ID')
    })


@payment.route('/payment/checkout-session', methods=['GET'])
def get_checkout_session():
    """Retrieve an existing Checkout Session by its ID."""
    session_id = request.args.get('sessionId')
    checkout_session_obj = stripe.checkout.Session.retrieve(session_id)
    return jsonify(checkout_session_obj)


@payment.route('/payment/create-checkout-session', methods=['POST'])
def create_checkout_session():
    """
    Create a new Checkout Session for the ljogged-in user's subscription.
    Apply a free trial only if the user has not already used one.
    """
    firebase_user = session.get('user')


    if not firebase_user:
        return jsonify({'error': 'User not logged in'}), 401

    firebase_user_id = firebase_user.get('userId')
    user_data = ref.child(firebase_user_id).get()

    if not user_data:
        return jsonify({'error': 'User not found in Firebase'}), 404

    stripe_customer_id = user_data.get('stripeCustomerId')

    if not stripe_customer_id:
        # Create a Stripe customer if none exists
        try:
            customer = stripe.Customer.create(
                email=user_data['email'],
                name=user_data['username']
            )
            ref.child(firebase_user_id).update({'stripeCustomerId': customer.id})
            stripe_customer_id = customer.id
        except Exception as e:
            print(f"Error creating Stripe customer: {str(e)}")
            return jsonify({'error': {'message': 'Failed to create Stripe customer'}}), 400

    # Check if the user has already used the free trial
    has_used_trial = user_data.get('hasUsedTrial', False)

    # Extract free_trial flag from the request
    request_data = request.get_json()
    free_trial = request_data.get('free_trial', False)

    # Disallow free trial if the user has already used it
    if free_trial and has_used_trial:
        return jsonify({
            'message': 'You have already used your free trial. Please choose a paid plan.'
        }), 401

    price_id = os.getenv('STRIPE_PRICE_ID')
    domain_url = os.getenv('CLIENT_URL')
    plan= 'Free Trial' if free_trial else 'Monthly'

    try:
        price_object = stripe.Price.retrieve(price_id)
        plan_price = 0.00 if free_trial else price_object.unit_amount / 100  # Convert to dollars (from cents)
    except Exception as e:
        print(f"Error retrieving price details: {str(e)}")
        return jsonify({'error': {'message': 'Failed to retrieve price details'}}), 500

    # Get the current date
    current_date = datetime.now()


    # Format the date as MM/DD/YYYY
    start_date = current_date.strftime("%m/%d/%Y")



    try:
        # Create a Checkout Session
        checkout_session_obj = stripe.checkout.Session.create(
            payment_method_types=['card'],
            success_url=f"{domain_url}/payment/success?session_id={{CHECKOUT_SESSION_ID}}&plan={plan}&price={plan_price}",
            cancel_url=f"{domain_url}/",
            mode='subscription',
            automatic_tax={"enabled": True},
            customer=stripe_customer_id,
            line_items=[{'price': price_id, 'quantity': 1}],
            customer_update={
                "address": "auto",
            },
            subscription_data={
                # Apply the free trial if eligible
                'trial_period_days': 7 if free_trial and not has_used_trial else None
            },
            consent_collection={
                'terms_of_service': 'required'
            },
        )


        return jsonify({'url': checkout_session_obj.url, 'id': checkout_session_obj.id})
    except Exception as e:
        print(f"Error creating checkout session: {str(e)}")
        return jsonify({'error': {'message': 'Failed to create checkout session'}}), 500




@payment.route('/payment/customer-portal', methods=['POST'])
def customer_portal():
    """
    Redirect the customer to the Stripe customer portal for subscription management.
    The sessionId must be provided in the request form.
    """
    checkout_session_id = request.form.get('sessionId')
    checkout_session_obj = stripe.checkout.Session.retrieve(checkout_session_id)
    return_url = os.getenv("DOMAIN")

    portal_session = stripe.billing_portal.Session.create(
        customer=checkout_session_obj.customer,
        return_url=return_url,
    )
    return redirect(portal_session.url, code=303)


@payment.route('/payment/webhook', methods=['POST'])
def webhook_received():
    """
    Handle Stripe webhook events related to subscriptions and payments.
    """
    payload = request.data
    sig_header = request.headers.get('Stripe-Signature')

    # Verify webhook signature
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, ENDPOINT_SECRET)
    except ValueError as e:
        print("Invalid payload:", e)
        return jsonify({'error': 'Invalid payload'}), 400
    except stripe.error.SignatureVerificationError as e:
        print("Invalid signature:", e)
        return jsonify({'error': 'Invalid signature'}), 400

    event_type = event['type']
    event_data = event['data']['object']

    stripe_customer_id = event_data.get('customer')

    # Find Firebase user
    firebase_user, firebase_user_id = find_firebase_user(stripe_customer_id)

    if not firebase_user:
        print(f"No Firebase user found for Stripe customer ID: {stripe_customer_id}")
        return jsonify({'error': 'User not found'}), 404

    # Handle subscription events
    if event_type == 'customer.subscription.created':
        handle_subscription_created(event_data, firebase_user, firebase_user_id)

    elif event_type == 'customer.subscription.deleted':
        handle_subscription_deleted(event_data, firebase_user_id)

    elif event_type == 'invoice.payment_failed':
        handle_payment_failed(event_data, firebase_user_id)

    return jsonify({'status': 'success'}), 200





@payment.route('/payment/unsubscribe', methods=['POST'])
def unsubscribe():
    """
    Unsubscribe the user by canceling their active subscription in Stripe
    and updating their subscription status in Firebase.
    """
    firebase_user = session.get('user')

    if not firebase_user:
        return jsonify({'error': 'User not logged in'}), 401

    firebase_user_id = firebase_user.get('userId')
    user_data = ref.child(firebase_user_id).get()

    if not user_data:
        return jsonify({'error': 'User not found in Firebase'}), 404

    stripe_customer_id = user_data.get('stripeCustomerId')

    if not stripe_customer_id:
        return jsonify({'error': 'No Stripe customer ID associated with user'}), 400

    try:
        # Retrieve active subscriptions for the customer
        subscriptions = stripe.Subscription.list(customer=stripe_customer_id, status='active')

        for subscription in subscriptions.auto_paging_iter():
            # Cancel each active subscription
            stripe.Subscription.delete(subscription.id)

        # Update Firebase subscription status
        ref.child(firebase_user_id).update({'isSubscribed': False})
        session['user']['isSubscribed'] = False

        return jsonify({'message': 'Successfully unsubscribed'}), 200

    except Exception as e:
        print(f"Error unsubscribing user: {str(e)}")
        return jsonify({'error': {'message': 'Failed to unsubscribe'}}), 400


@payment.route('/payment/payment-method', methods=['GET'])
def get_payment_methods():
    """
    Retrieve all payment methods for the logged-in user.
    """
    firebase_user = session.get('user')

    if not firebase_user:
        return jsonify({'error': 'User not logged in'}), 401

    firebase_user_id = firebase_user.get('userId')
    user_data = ref.child(firebase_user_id).get()

    if not user_data:
        return jsonify({'error': 'User not found in Firebase'}), 404

    stripe_customer_id = user_data.get('stripeCustomerId')

    if not stripe_customer_id:
        return jsonify({'error': 'No Stripe customer ID associated with user'}), 400

    try:
        # Retrieve all payment methods of type "card"
        payment_methods = stripe.PaymentMethod.list(
            customer=stripe_customer_id,
            type="card",
        )

        if payment_methods.data:
            cards = []
            for method in payment_methods.data:
                cards.append({
                    'id': method.id,
                    'brand': method.card.brand,
                    'last4': method.card.last4,
                    'exp_month': method.card.exp_month,
                    'exp_year': method.card.exp_year,
                })

            return jsonify({'paymentMethods': cards}), 200
        else:
            return jsonify({'message': 'No payment methods found'}), 404

    except stripe.error.StripeError as e:
        logging.error(f"Stripe API error: {str(e)}")
        return jsonify({'error': 'Failed to retrieve payment methods'}), 500
    except Exception as e:
        logging.error(f"Unexpected error: {str(e)}")
        return jsonify({'error': 'An unexpected error occurred'}), 500


@payment.route('/payment/payment-method/delete', methods=['POST'])
def delete_payment_method():
    """
    Delete a specific payment method for the logged-in user.
    Prevent deletion if the payment method is linked to an active subscription or a free trial.
    Allow deletion if the free trial is canceled.
    """
    firebase_user = session.get('user')

    if not firebase_user:
        return jsonify({'error': 'User not logged in'}), 401

    firebase_user_id = firebase_user.get('userId')
    user_data = ref.child(firebase_user_id).get()

    if not user_data:
        return jsonify({'error': 'User not found in Firebase'}), 404

    stripe_customer_id = user_data.get('stripeCustomerId')

    if not stripe_customer_id:
        return jsonify({'error': 'No Stripe customer ID associated with user'}), 400

    try:
        # Retrieve payment method ID from the request
        payment_method_id = request.json.get('paymentMethodId')
        if not payment_method_id:
            return jsonify({'error': 'Payment method ID is required'}), 400

        # Retrieve all subscriptions for the customer
        subscriptions = stripe.Subscription.list(customer=stripe_customer_id)

        for subscription in subscriptions.data:
            # Check if the subscription uses this payment method
            if subscription.default_payment_method == payment_method_id:
                # Allow deletion if the subscription is canceled
                if subscription.status == 'canceled':
                    continue

                # Check if the subscription is still in a free trial
                if subscription.trial_end and subscription.trial_end > int(time.time()):
                    return jsonify({
                        'error': 'This payment method is linked to an active free trial. '
                                 'Please update your subscription with a new payment method before deleting this one.'
                    }), 400

                # Prevent deletion if the subscription is active
                return jsonify({
                    'error': 'This payment method is linked to an active subscription. '
                             'Please update your subscription with a new payment method before deleting this one.'
                }), 400

        # Detach the specified payment method
        stripe.PaymentMethod.detach(payment_method_id)
        return jsonify({'message': 'Payment method deleted successfully'}), 200

    except stripe.error.StripeError as e:
        logging.error(f"Stripe API error: {str(e)}")
        return jsonify({'error': 'Failed to delete payment method'}), 500
    except Exception as e:
        logging.error(f"Unexpected error: {str(e)}")
        return jsonify({'error': 'An unexpected error occurred'}), 500

@payment.route('/payment/subscription/reactivate', methods=['POST'])
def reactivate_subscription():
    """
    Reactivate a subscription by creating a new one if the old one was canceled,
    using the default price ID.
    """
    firebase_user = session.get('user')

    if not firebase_user:
        return jsonify({'error': 'User not logged in'}), 401

    firebase_user_id = firebase_user.get('userId')
    user_data = ref.child(firebase_user_id).get()

    if not user_data:
        return jsonify({'error': 'User not found in Firebase'}), 404

    stripe_customer_id = user_data.get('stripeCustomerId')

    if not stripe_customer_id:
        return jsonify({'error': 'No Stripe customer ID associated with user'}), 400

    try:
        # Check if the customer has a valid payment method
        payment_methods = stripe.PaymentMethod.list(
            customer=stripe_customer_id,
            type="card",
        )

        if not payment_methods.data:
            return jsonify({'error': 'No valid payment method found. Please add a payment method.'}), 400

        # Create a new subscription using the price ID
        new_subscription = stripe.Subscription.create(
            customer=stripe_customer_id,
            items=[{"price":os.getenv('STRIPE_PRICE_ID')}],  # Use your price ID here
            default_payment_method=payment_methods.data[0].id,  # Use the first valid payment method
        )

        return jsonify({'message': 'Subscription reactivated successfully', 'subscriptionId': new_subscription.id}), 200

    except Exception as e:
        print(f"Error reactivating subscription: {str(e)}")
        return jsonify({'error': 'Failed to reactivate subscription'}), 500


