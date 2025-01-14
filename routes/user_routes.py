from flask import request, jsonify, Blueprint, session

from werkzeug.security import generate_password_hash, check_password_hash
from services.user_service import *
import stripe

import random
import string
from flask_mail import Message

from services.helper import  mail
import uuid
from flask import request, jsonify, session
from firebase_admin import auth as firebase_auth
from werkzeug.security import check_password_hash
import firebase_admin
from firebase_admin import credentials, db

# Firebase Realtime Database reference
ref = db.reference('users')
user = Blueprint('user', __name__)

# Secret key for session management
import os

@user.route('/user/signup', methods=['POST'])
def signup():
    data = request.json
    username = data.get('username')
    email = data.get('email').lower().strip()
    password = data.get('password')
    hashed_password = generate_password_hash(password, method='pbkdf2:sha256')
    is_subscribed = data.get('isSubscribed')

    # Check if email already exists
    users = ref.get()
    if users and any(user['email'] == email for user in users.values()):
        return jsonify({'message': 'Email already exists'}), 400

    # Save new user to Firebase
    user_id = ref.push({
        'username': username,
        'email': email,
        'password': hashed_password,
        'isSubscribed': is_subscribed,
        'hasUsedTrial': False
    }).key
    return jsonify({'message': 'User created', 'userId': user_id}), 201


# @user.route('/user/login', methods=['POST'])
# def login():
#     data = request.json
#     email = data.get('email').lower().strip()
#     password = data.get('password')
#
#     # Retrieve user data from Firebase
#     users = ref.get()
#     if not users:
#         return jsonify({'message': 'Invalid email or password'}), 400
#
#     for user_id, user_data in users.items():
#         if user_data['email'] == email:
#             if check_password_hash(user_data['password'], password):
#                 # Store user info in the session
#                 session['user'] = {
#                     'userId': user_id,
#                     'username': user_data['username'],
#                     'email': user_data['email'],
#                     'isSubscribed': user_data['isSubscribed']
#                 }
#                 print("Session content:", session)
#                 return jsonify({'message': 'Login successful', 'user': session['user']}), 200
#             else:
#                 return jsonify({'message': 'Invalid email or password'}), 400
#
#     return jsonify({'message': 'Invalid email or password'}), 400

# Initialize Firebase Admin
# cred = credentials.Certificate('path/to/serviceAccountKey.json')
# firebase_admin.initialize_app(cred, {
#     'databaseURL': 'https://masyg-extractor-db.firebaseio.com'
# })

# Reference to Firebase database
# ref = db.reference('users')
@user.route('/user/login', methods=['POST'])
def login():
    """Handle user login with email/password or Google ID token."""
    data = request.json
    email = data.get('email', '').lower().strip()
    password = data.get('password')
    google_id_token = data.get('googleIdToken')

    if not email and not google_id_token:
        return jsonify({'message': 'Missing credentials'}), 400

    try:
        if google_id_token:
            return handle_google_login(google_id_token)
        elif email and password:
            return handle_password_login(email, password)
    except Exception as e:
        print(f"Error during login: {e}")
        return jsonify({'message': 'An error occurred during login'}), 500

    return jsonify({'message': 'Invalid request'}), 400




@user.route('/user/logout', methods=['POST'])
def logout():
    # Clear the session
    session.pop('user', None)
    return jsonify({'message': 'Logout successful'}), 200


@user.route('/user/current', methods=['GET'])
def get_current_user():
    current_user = session.get('user')
    print(current_user)
    if not current_user:
        return jsonify({'error': 'No user is currently logged in'}), 401

    # Re-fetch the user from Firebase using their userId:
    firebase_user = db.reference('users').child(current_user['userId']).get()
    if not firebase_user:
        return jsonify({'error': 'User not found in database'}), 404

    # Update the subscription status from the database, not the session
    firebase_user_data = {
        'userId': firebase_user.get('userId'),
        'email': firebase_user.get('email'),
        'username': firebase_user.get('username'),
        'isSubscribed': firebase_user.get('isSubscribed', False),
        'hasUsedTrial': firebase_user.get('hasUsedTrial', False)
    }
    return jsonify({'user': firebase_user_data}), 200


@user.route('/user/update', methods=['POST'])
def update_user_info():
    """
    Update user information for the logged-in user.
    """
    firebase_user = session.get('user')

    if not firebase_user:
        return jsonify({'error': 'User not logged in'}), 401

    firebase_user_id = firebase_user.get('userId')
    user_data = ref.child(firebase_user_id).get()


    if not user_data:
        return jsonify({'error': 'User not found in Firebase'}), 404

    try:
        # Get updated data from the request
        updated_data = request.json
        email = updated_data.get('email').lower().strip()
        username = updated_data.get('username')
        old_password = updated_data.get('old_password')
        old_email = firebase_user.get('email').lower().strip()
        new_password = updated_data.get('password')  # Optional, handle securely
        #
        # users = ref.get()
        # if users and any(user['email'].lower().strip() == email for user in users.values()):
        #     return jsonify({'error': 'Email already exists'}), 400

        # Validate the old email and old password

        if not (user_data['email'].lower().strip()== old_email and check_password_hash(user_data['password'], old_password)):
            return jsonify({
                'error': 'Invalid email or password. Please provide correct credentials to update your information.'
            }), 400

        # Update user data in Firebase
        updates = {}
        if email and email != user_data['email']:
            updates['email'] = email
        if username and username != user_data['username']:
            updates['username'] = username
        if new_password:
            # Hash the new password before storing it
            hashed_password = generate_password_hash(new_password, method='pbkdf2:sha256')
            updates['password'] = hashed_password

        if updates:
            ref.child(firebase_user_id).update(updates)

        # Optionally update Stripe customer email if available
        stripe_customer_id = user_data.get('stripeCustomerId')
        if stripe_customer_id and 'email' in updates:
            stripe.Customer.modify(
                stripe_customer_id,
                email=updates['email'],
            )

        return jsonify({'message': 'User information updated successfully'}), 200

    except Exception as e:
        print(f"Error updating user info: {str(e)}")
        return jsonify({'error': 'Failed to update user information'}), 500

# Send password reset email
@user.route('/user/request-reset', methods=['POST'])
def request_reset():
    data = request.json
    email = data.get('email')

    if not email:
        return jsonify({'error': 'Email is required'}), 400

    # Find user in Firebase
    users = ref.get()

    for user_id, user_data in users.items():
        print(user_data.get('email'), email)
        if user_data.get('email') == email:
            # Generate a random token
            token = ''.join(random.choices(string.ascii_letters + string.digits, k=20))
            ref.child(user_id).update({'resetToken': token})

            # Send email with reset link
            reset_url = f"{os.getenv('CLIENT_URL')}/reset-password/{token}"
            msg = Message("Password Reset Request", sender="support@masyg.com", recipients=[email])
            msg.body = f"Click the link to reset your password: {reset_url}"
            mail.send(msg)

            return jsonify({'message': 'Password reset link sent successfully.'}), 200

    return jsonify({'error': 'No account found with this email.'}), 404


@user.route('/user/reset-password', methods=['POST'])
def reset_password():
    data = request.json
    token = data.get('token')
    new_password = data.get('password')

    if not token or not new_password:
        return jsonify({'error': 'Token and new password are required'}), 400

    # Find user by token
    users = ref.get()
    for user_id, user_data in users.items():
        if user_data.get('resetToken') == token:
            # Hash and update the password
            hashed_password = generate_password_hash(new_password, method='pbkdf2:sha256')
            ref.child(user_id).update({'password': hashed_password, 'resetToken': None})
            return jsonify({'message': 'Password updated successfully.'}), 200

    return jsonify({'error': 'Invalid or expired token.'}), 400
@user.route('/user/create-customer-portal', methods=['POST'])
def create_customer_portal():
    try:
        # Check if the user is logged in
        if 'user' not in session:
            return jsonify({"error": "User not logged in"}), 401

        # Get the customer ID from the session

        firebase_user = session['user']
        firebase_user_id = firebase_user.get('userId')
        user_data = ref.child(firebase_user_id).get()
        customer_id = user_data.get('stripeCustomerId')


        if not customer_id:
            return jsonify({"error": "Stripe customer ID not found for the user"}), 400

        # Create the customer portal session
        session_data = stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=os.getenv('CLIENT_URL')  # Replace with your desired return URL
        )

        return jsonify({"url": session_data.url}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500