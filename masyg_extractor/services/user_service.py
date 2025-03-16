from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse
from werkzeug.security import generate_password_hash, check_password_hash
import uuid
import time
import asyncio
from firebase_admin import auth as firebase_auth
from firebase_admin import firestore  # Using Firestore for user data
import concurrent.futures

# Initialize Firestore client and set the collection reference for users.
firestore_db = firestore.client()
ref = firestore_db.collection("users")  # Firestore collection for user documents

# Create a global ThreadPoolExecutor to offload blocking Firestore calls.
executor = concurrent.futures.ThreadPoolExecutor(max_workers=10)


def query_user_by_email(email):
    """Query Firestore for a user document matching the given email."""
    docs = list(ref.where('email', '==', email).limit(1).stream())
    if docs:
        doc = docs[0]
        user_data = doc.to_dict()
        user_data['userId'] = doc.id
        return user_data
    return None


async def safe_query_user_by_email(email, timeout_secs=10):
    """Offload the Firestore query to a dedicated thread with a timeout."""
    start = time.time()
    loop = asyncio.get_running_loop()
    user_found = await asyncio.wait_for(
        loop.run_in_executor(executor, query_user_by_email, email),
        timeout_secs
    )
    elapsed = time.time() - start
    print(f"Firestore query took {elapsed:.2f} seconds")
    return user_found


async def safe_verify_id_token(token, timeout_secs=10):
    """
    Verify a Firebase ID token by offloading to a thread and waiting for a result.
    If the operation takes longer than timeout_secs, a TimeoutError is raised.
    """
    loop = asyncio.get_running_loop()
    return await asyncio.wait_for(
        loop.run_in_executor(None, firebase_auth.verify_id_token, token),
        timeout_secs
    )


async def handle_google_login(request: Request, google_id_token: str):
    """Handle Google login using Firestore for efficient user management."""
    try:
        # Verify the Google ID token with a timeout.
        decoded_token = await safe_verify_id_token(google_id_token, timeout_secs=10)
        google_email = decoded_token.get('email')
        username = decoded_token.get('name', 'Google User')

        # Offload the Firestore query to a thread.
        user_found = await safe_query_user_by_email(google_email, timeout_secs=10)

        if not user_found:
            # If user does not exist, create a new user with a random hashed password.
            new_user = {
                'email': google_email,
                'username': username,
                'password': generate_password_hash(uuid.uuid4().hex, method='pbkdf2:sha256'),
                'isSubscribed': False,
                'hasUsedTrial': False,
            }

            def add_new_user():
                new_doc_ref, _ = ref.add(new_user)
                new_user_copy = new_user.copy()
                new_user_copy['userId'] = new_doc_ref.id
                return new_user_copy

            start = time.time()
            loop = asyncio.get_running_loop()
            user_found = await asyncio.wait_for(
                loop.run_in_executor(executor, add_new_user),
                timeout_secs=10
            )
            elapsed = time.time() - start
            print(f"Firestore add operation took {elapsed:.2f} seconds")

        # Store user details in the session (requires session middleware).
        request.session['user'] = {
            'userId': user_found['userId'],
            'username': user_found.get('username'),
            'email': user_found.get('email'),
            'isSubscribed': user_found.get('isSubscribed'),
            'hasUsedTrial': user_found.get('hasUsedTrial')
        }
        return JSONResponse(
            content={'message': 'Google login successful', 'user': request.session['user']},
            status_code=200
        )

    except Exception as e:
        print(f"Error verifying Google ID token: {e}")
        return JSONResponse(content={'message': 'Invalid Google ID token'}, status_code=400)


async def handle_password_login(request: Request, email: str, password: str):
    """Handle password-based login using Firestore for efficient user lookup."""
    try:
        user_found = await safe_query_user_by_email(email, timeout_secs=10)
        if not user_found or not check_password_hash(user_found.get('password'), password):
            return JSONResponse(content={'message': 'Invalid email or password'}, status_code=400)

        request.session['user'] = {
            'userId': user_found['userId'],
            'username': user_found.get('username'),
            'email': user_found.get('email'),
            'isSubscribed': user_found.get('isSubscribed'),
            'hasUsedTrial': user_found.get('hasUsedTrial')
        }
        return JSONResponse(
            content={'message': 'Login successful', 'user': request.session['user']},
            status_code=200
        )

    except Exception as e:
        print(f"Error during login: {e}")
        return JSONResponse(content={'message': 'An error occurred during login'}, status_code=500)
