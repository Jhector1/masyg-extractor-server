

from werkzeug.security import generate_password_hash


import uuid
from flask import jsonify, session
from firebase_admin import auth as firebase_auth
from werkzeug.security import check_password_hash

from firebase_admin import  db

# Firebase Realtime Database reference
ref = db.reference('users')

def handle_google_login(google_id_token):
    """Handle Google login."""
    try:
        decoded_token = firebase_auth.verify_id_token(google_id_token)
        google_email = decoded_token.get('email')
        username = decoded_token.get('name', 'Google User')
        firebase_uid = decoded_token.get('sub')  # Firebase UID

        # Query user by email
        users = ref.order_by_child('email').equal_to(google_email).get()
        user_found = None

        if users:
            # Extract user data and include Firebase key as userId
            for user_id_key, user_data in users.items():
                user_found = {**user_data, 'userId': user_id_key}
                break

        if not user_found:
            # Create new user if not found
            new_user = {
                'email': google_email,
                'username': username,
                'password': generate_password_hash(uuid.uuid4().hex, method='pbkdf2:sha256'),
                'isSubscribed': False,
                'hasUsedTrial': False,
            }
            new_user_ref = ref.push(new_user)
            user_id = new_user_ref.key
            user_found = {**new_user, 'userId': user_id}

        # Store user session
        session['user'] = {
            'userId': user_found['userId'],
            'username': user_found['username'],
            'email': user_found['email'],
            'isSubscribed': user_found['isSubscribed'],
        }
        return jsonify({'message': 'Google login successful', 'user': session['user']}), 200

    except Exception as e:
        print(f"Error verifying Google ID token: {e}")
        return jsonify({'message': 'Invalid Google ID token'}), 400
def handle_password_login(email, password):
    """Handle password-based login."""
    try:
        users = ref.order_by_child('email').equal_to(email).get()
        user_found = None

        if users:
            # Extract user data and Firebase key as userId
            for user_id_key, user_data in users.items():
                if user_data['email'] == email:
                    user_found = {**user_data, 'userId': user_id_key}
                    break

        if not user_found or not check_password_hash(user_found['password'], password):
            return jsonify({'message': 'Invalid email or password'}), 400

        # Store user session
        session['user'] = {
            'userId': user_found['userId'],
            'username': user_found['username'],
            'email': user_found['email'],
            'isSubscribed': user_found['isSubscribed']
        }
        return jsonify({'message': 'Login successful', 'user': session['user']}), 200

    except Exception as e:
        print(f"Error during login: {e}")
        return jsonify({'message': 'An error occurred during login'}), 500