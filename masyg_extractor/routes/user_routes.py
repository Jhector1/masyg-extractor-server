import os
import uuid
import random
import string
import time
import asyncio
import concurrent.futures

from fastapi import APIRouter, Request, HTTPException, status, Depends, BackgroundTasks
from fastapi.responses import JSONResponse
from fastapi_mail import MessageSchema
from pydantic import BaseModel, EmailStr

from werkzeug.security import generate_password_hash, check_password_hash

import stripe

from firebase_admin import auth as firebase_auth
from firebase_admin import firestore
from masyg_extractor.services.my_log import send_log, logger

from masyg_extractor.services.data_extractor_services import get_firebase_user
from masyg_extractor.services.firestore_helpers import document_delete, document_get, get_firestore_client
from masyg_extractor.services.subscription_services import delete_stripe_customer_data

from masyg_extractor.utils.extensions import sio  # Assuming socketio integrations is available.

# Initialize Firestore client and collection reference
firestore_db = firestore.client()
ref = firestore_db.collection("users")

# Create a global ThreadPoolExecutor to offload blocking Firestore calls.
executor = concurrent.futures.ThreadPoolExecutor(max_workers=10)

router = APIRouter(prefix="/user")

# --- Helper Async Functions ---

def _query_user_by_email(email):
    """Blocking Firestore query to get a user by email."""
    docs = list(ref.where('email', '==', email).limit(1).stream())
    if docs:
        doc = docs[0]
        user_data = doc.to_dict()
        user_data['userId'] = doc.id
        return user_data
    return None

async def query_user_by_email_async(email, timeout_secs=10):
    loop = asyncio.get_running_loop()
    start = time.time()
    user_found = await asyncio.wait_for(
        loop.run_in_executor(executor, _query_user_by_email, email),
        timeout_secs
    )
    elapsed = time.time() - start
    print(f"Firestore query took {elapsed:.2f} seconds")
    return user_found

async def add_new_user_async(new_user, timeout_secs=10):
    """Offload adding a new user to Firestore."""
    def _add_new_user():
        new_doc_ref = ref.document()  # Create a new document reference
        new_user_copy = new_user.copy()
        new_user_copy['userId'] = new_doc_ref.id  # Assign the correct ID
        new_doc_ref.set(new_user_copy)
        return new_user_copy

    loop = asyncio.get_running_loop()
    start = time.time()
    user_added = await asyncio.wait_for(
        loop.run_in_executor(executor, _add_new_user),
        timeout_secs
    )
    elapsed = time.time() - start
    print(f"Firestore add operation took {elapsed:.2f} seconds")
    return user_added

async def verify_id_token_async(token, timeout_secs=10):
    """Offload Firebase token verification to a thread."""
    loop = asyncio.get_running_loop()
    return await asyncio.wait_for(
        loop.run_in_executor(None, firebase_auth.verify_id_token, token),
        timeout_secs
    )

# --- Async Route Handlers ---

@router.post("/signup", status_code=status.HTTP_201_CREATED)
async def signup(request: Request):
    data = await request.json()
    if not data:
        raise HTTPException(status_code=400, detail="No data provided")

    username = data.get('username')
    email = data.get('email', '').lower().strip()
    password = data.get('password')
    is_subscribed = data.get('isSubscribed', False)

    if not username or not email or not password:
        raise HTTPException(status_code=400, detail="Username, email, and password are required")

    loop = asyncio.get_running_loop()
    existing_users = await loop.run_in_executor(
        executor,
        lambda: list(ref.where('email', '==', email).limit(1).stream())
    )
    if existing_users:
        raise HTTPException(status_code=400, detail="Email already exists")

    new_user = {
        'username': username,
        'email': email,
        'password': generate_password_hash(password, method='pbkdf2:sha256'),
        'isSubscribed': is_subscribed,
        'hasUsedTrial': False
    }
    user_added = await add_new_user_async(new_user, timeout_secs=10)
    return {"message": "User created", "userId": user_added['userId']}


@router.post("/login")
async def login(request: Request):
    data = await request.json()
    clientId = request.session.get("client_id")
    if clientId is None:
        clientId = 'Guest'
    # print('client------', clientId)
    if not data:
        raise HTTPException(status_code=400, detail="No data provided")

    email = data.get('email', '').lower().strip()
    password = data.get('password')
    google_id_token = data.get('googleIdToken')

    if not email and not google_id_token:
        raise HTTPException(status_code=400, detail="Missing credentials")

    try:
        if google_id_token:
            # Google login logic.
            decoded_token = await verify_id_token_async(google_id_token, timeout_secs=10)
            google_email = decoded_token.get('email')
            username = decoded_token.get('name', 'Google User')

            user_found = await query_user_by_email_async(google_email, timeout_secs=10)

            if not user_found:
                new_user = {
                    'email': google_email,
                    'username': username,
                    'password': generate_password_hash(uuid.uuid4().hex, method='pbkdf2:sha256'),
                    'isSubscribed': False,
                    'hasUsedTrial': False,
                }
                user_found = await add_new_user_async(new_user, timeout_secs=10)

        elif email and password:
            user_found = await query_user_by_email_async(email, timeout_secs=10)
            if not user_found or not check_password_hash(user_found.get('password'), password):
                # Emit socket message for invalid login (assuming sio is integrated)
                await sio.emit('log_message', {'data': '❌Invalid email or password!'}, room=clientId)
                raise HTTPException(status_code=400, detail="Invalid email or password")
        else:
            raise HTTPException(status_code=400, detail="Invalid request")

        # Assuming session is available via request.session from a session middleware.
        request.session['user'] = {
            'userId': user_found['userId'],
            'username': user_found.get('username'),
            'email': user_found.get('email'),
            'isSubscribed': user_found.get('isSubscribed'),
            'hasUsedTrial': user_found.get('hasUsedTrial')
        }

        # await sio.emit('log_message', {'data': '✅Login successful!'}, room=clientId)
        print("Client ID", clientId)
        # await sio.emit("welcome", {"message": f"Welcome, {clientId}!"}, room=clientId)
        asyncio.create_task(send_log( '✅Login successful!', user_room=clientId))
        # logger.info(f"✅Login successful!", extra={"target_room": clientId})
        # logger.info("Processing POST request", extra={"target_room": sid})

        return {"message": "Login successful", "user": request.session['user']}

    except Exception as e:
        print(f"Error during login: {e}")
        await sio.emit('log_message', {'data': '❌Error during login!'}, room=clientId)
        raise HTTPException(status_code=500, detail="An error occurred during login")


@router.post("/logout")
async def logout(request: Request):
    request.session.pop('user', None)
    return {"message": "Logout successful"}


@router.get("/current")
async def get_current_user(request: Request):
    current_user = request.session.get('user')
    if not current_user:
        raise HTTPException(status_code=401, detail="No user is currently logged in")

    loop = asyncio.get_running_loop()
    user_doc = await loop.run_in_executor(
        executor,
        lambda: ref.document(current_user['userId']).get()
    )
    if not user_doc.exists:
        raise HTTPException(status_code=404, detail="User not found in database")

    firebase_user_data = user_doc.to_dict()
    request.session['user'] = {
        'userId': current_user['userId'],
        'username': firebase_user_data.get('username'),
        'email': firebase_user_data.get('email'),
        'isSubscribed': firebase_user_data.get('isSubscribed', False),
        'hasUsedTrial': firebase_user_data.get('hasUsedTrial', False)
    }
    return {"user": request.session['user']}


@router.post("/update")
async def update_user_info(request: Request):
    firebase_user = request.session.get('user')
    if not firebase_user:
        raise HTTPException(status_code=401, detail="User not logged in")

    firebase_user_id = firebase_user.get('userId')
    loop = asyncio.get_running_loop()
    doc = await loop.run_in_executor(
        executor,
        lambda: ref.document(firebase_user_id).get()
    )
    if not doc.exists:
        raise HTTPException(status_code=404, detail="User not found in Firestore")

    user_data = doc.to_dict()
    updated_data = await request.json()
    if not updated_data:
        raise HTTPException(status_code=400, detail="No update data provided")

    old_email = firebase_user.get('email', '').lower().strip()
    new_email = updated_data.get('email', '').lower().strip()
    username = updated_data.get('username')
    old_password = updated_data.get('old_password')
    new_password = updated_data.get('password')

    if not (user_data.get('email', '').lower().strip() == old_email and check_password_hash(user_data.get('password'), old_password)):
        raise HTTPException(
            status_code=400,
            detail="Invalid email or password. Please provide correct credentials to update your information."
        )

    updates = {}
    if new_email and new_email != user_data.get('email'):
        updates['email'] = new_email
    if username and username != user_data.get('username'):
        updates['username'] = username
    if new_password:
        updates['password'] = generate_password_hash(new_password, method='pbkdf2:sha256')

    if updates:
        await loop.run_in_executor(
            executor,
            lambda: ref.document(firebase_user_id).update(updates)
        )

    stripe_customer_id = user_data.get('stripeCustomerId')
    if stripe_customer_id and 'email' in updates:
        stripe.Customer.modify(stripe_customer_id, email=updates['email'])

    return {"message": "User information updated successfully"}


# Pydantic model for request validation
class ResetRequest(BaseModel):
    email: EmailStr


import datetime


@router.post("/request-reset")
async def request_reset(request: Request, reset_req: ResetRequest, background_tasks: BackgroundTasks):
    email = reset_req.email

    loop = asyncio.get_running_loop()
    users_query = await loop.run_in_executor(
        executor,
        lambda: list(ref.where('email', '==', email).stream()) if ref else []
    )
    if not users_query:
        raise HTTPException(status_code=404, detail="No account found with this email.")

    user_doc = users_query[0]
    token = ''.join(random.choices(string.ascii_letters + string.digits, k=20))
    await loop.run_in_executor(
        executor,
        lambda: user_doc.reference.update({'resetToken': token})
    )

    reset_url = f"{os.getenv('CLIENT_URL')}/reset-password/{token}"

    # Build a modern HTML email template with inline CSS
    html_body = f"""
    <html>
      <head>
        <style>
          body {{
            font-family: Arial, sans-serif;
            background-color: #f4f4f4;
            padding: 20px;
          }}
          .container {{
            background-color: #ffffff;
            max-width: 600px;
            margin: 0 auto;
            padding: 30px;
            border-radius: 8px;
            box-shadow: 0 0 10px rgba(0, 0, 0, 0.1);
          }}
          h2 {{
            color: #333333;
          }}
          p {{
            color: #555555;
            font-size: 16px;
          }}
          .btn {{
            display: inline-block;
            padding: 10px 20px;
            margin-top: 20px;
            background-color: #007BFF;
            color: #ffffff;
            text-decoration: none;
            border-radius: 5px;
          }}
          .footer {{
            margin-top: 30px;
            font-size: 12px;
            color: #999999;
          }}
        </style>
      </head>
      <body>
        <div class="container">
          <h2>Password Reset Request</h2>
          <p>We received a request to reset your password. Click the button below to reset it:</p>
          <a href="{reset_url}" class="btn">Reset Password</a>
          <p>If you did not request a password reset, please ignore this email.</p>
          <div class="footer">
            <p>&copy; {datetime.datetime.now().year} Masyg Link. All rights reserved.</p>
          </div>
        </div>
      </body>
    </html>
    """

    message = MessageSchema(
        subject="Password Reset Request",
        recipients=[email],
        body=html_body,
        subtype="html"
    )

    # Schedule email sending in the background
    background_tasks.add_task(request.app.state.mail.send_message, message)

    return {"message": "Password reset link sent successfully."}



@router.post("/reset-password")
async def reset_password(request: Request):
    data = await request.json()
    token = data.get('token')
    new_password = data.get('password')
    if not token or not new_password:
        raise HTTPException(status_code=400, detail="Token and new password are required")

    loop = asyncio.get_running_loop()
    user_query = await loop.run_in_executor(
        executor,
        lambda: list(ref.where('resetToken', '==', token).stream())
    )
    if not user_query:
        raise HTTPException(status_code=400, detail="Invalid or expired token.")

    hashed_password = generate_password_hash(new_password, method='pbkdf2:sha256')
    def update_doc():
        for doc in user_query:
            doc.reference.update({'password': hashed_password, 'resetToken': None})
            return True
    result = await loop.run_in_executor(executor, update_doc)
    if result:
        return {"message": "Password updated successfully."}
    raise HTTPException(status_code=400, detail="Invalid or expired token.")


@router.post("/create-customer-portal")
async def create_customer_portal(request: Request):
    try:
        if 'user' not in request.session:
            raise HTTPException(status_code=401, detail="User not logged in")

        firebase_user = request.session['user']
        firebase_user_id = firebase_user.get('userId')
        loop = asyncio.get_running_loop()
        doc = await loop.run_in_executor(
            executor,
            lambda: ref.document(firebase_user_id).get()
        )
        if not doc.exists:
            raise HTTPException(status_code=404, detail="User not found in Firestore")
        user_data = doc.to_dict()
        customer_id = user_data.get('stripeCustomerId')
        if not customer_id:
            raise HTTPException(status_code=400, detail="Stripe customer ID not found for the user")

        session_data = stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=os.getenv('CLIENT_URL')
        )
        return {"url": session_data.url}

    except Exception as e:
        print(f"Error creating customer portal: {e}")
        raise HTTPException(status_code=500, detail=str(e))


from fastapi import APIRouter, Request, HTTPException, status, Depends
from fastapi.responses import JSONResponse

# Assuming these are your helper functions:
# - get_firebase_user: retrieves the Firebase-authenticated user details.
# - get_firestore_client: returns an asynchronous Firestore client.
# - document_get: retrieves a Firestore document.
# - document_delete: deletes a Firestore document.
# - delete_stripe_customer_data: a function that handles deletion of a Stripe customer.
# - ref: a Firestore collection reference for your users collection.

@router.delete("/delete-my-account/{email}")
async def delete_my_account(
        email: str,
        request: Request,
        firebase_user: dict = Depends(get_firebase_user)
):
    # Validate that the authenticated user has a valid user ID.
    user_id = firebase_user.get('userId')
    if not user_id:
        logger.error("Authenticated user does not have a userId")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User ID not found")

    # Validate that the authenticated user's email is available.
    session_email = firebase_user.get("email")
    if not session_email:
        logger.error("Authenticated user does not have an email")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Email not found")

    # Check that the session email matches the provided email.
    if session_email.lower() != email.lower():
        logger.error(
            f"Email mismatch: session email '{session_email}' does not match provided email '{email}'"
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Unauthorized: Email mismatch")

    # Retrieve the user document from Firestore using the user_id.
    doc_ref = ref.document(user_id)
    doc = doc_ref.get()
    if not doc.exists:
        return JSONResponse({"error": "User not found in Firestore"}, status_code=404)
    user_data = doc.to_dict()

    # Get the Stripe customer ID from the user data.
    stripe_customer_id = user_data.get("stripeCustomerId")

    # Get the Firestore client and reference to the user document.
    firestore_client = await get_firestore_client()
    user_ref = firestore_client.collection("users").document(user_id)

    # Attempt to fetch the user document snapshot.
    try:
        user_snapshot = await document_get(user_ref)
    except Exception as e:
        logger.exception("Error fetching user account '%s': %s", user_id, e)
        raise HTTPException(status_code=500, detail="Error retrieving user account") from e

    # If the user account exists, proceed with deletion.
    if user_snapshot.exists:
        try:
            # If there is a Stripe customer ID, delete the associated Stripe customer data.
            if stripe_customer_id:
                await delete_stripe_customer_data(stripe_customer_id)
            # Delete the user document from Firestore.
            await document_delete(user_ref)
            logger.info("Deleted account for user '%s'", user_id)
        except Exception as e:
            logger.exception("Failed to delete account for user '%s': %s", user_id, e)
            raise HTTPException(status_code=500, detail="Failed to delete account") from e
    else:
        logger.warning("User account '%s' not found", user_id)
        raise HTTPException(status_code=404, detail="User account not found")

    return JSONResponse(
        content={'message': 'Your account has been deleted successfully'},
        status_code=200
    )

