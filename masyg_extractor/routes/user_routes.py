import os
import uuid
import time
import asyncio
import concurrent.futures
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Request, status, BackgroundTasks, Depends, Response, HTTPException
from fastapi_mail import MessageSchema, MessageType
from jose import jwt, JWTError
from pydantic import BaseModel, EmailStr, Field

from werkzeug.security import generate_password_hash, check_password_hash

import stripe

from firebase_admin import auth as firebase_auth
from firebase_admin import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from masyg_extractor.config.jwt_config import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    REFRESH_TOKEN_EXPIRE_DAYS,
    create_access_token,
    create_refresh_token,
    decode_jwt_token,
    generate_csrf_token,
    get_current_user_from_cookie,
)
from masyg_extractor.services.my_log import send_log, logger
from masyg_extractor.services.mail_delivery import send_message_safely
from masyg_extractor.services.auth_sessions import (
    create_refresh_session,
    revoke_all_refresh_sessions,
    revoke_refresh_session,
    rotate_refresh_session,
)
from masyg_extractor.services.socket_connections import socket_connections
from masyg_extractor.security import (
    cookie_security_options,
    generate_password_reset_token,
    hash_password_reset_token,
    normalize_email,
    reset_token_is_expired,
)

from masyg_extractor.services.firestore_helpers import document_delete, document_get, get_firestore_client
from masyg_extractor.services.subscription_services import delete_stripe_customer_data

from masyg_extractor.utils.extensions import sio  # Assuming socketio integrations is available.

# Initialize Firestore client and collection reference
firestore_db = firestore.client()
ref = firestore_db.collection("users")

# Create a global ThreadPoolExecutor to offload blocking Firestore calls.
executor = concurrent.futures.ThreadPoolExecutor(max_workers=10)

router = APIRouter(prefix="/user")

SUPPORT_TOPICS = {
    "general": "General question",
    "document": "Document issue",
    "accounting": "Accounting integration",
    "bank": "Bank connection",
    "billing": "Billing",
    "bug": "Bug report",
    "other": "Other",
}

SUPPORT_EMAIL = (
    os.getenv("SUPPORT_EMAIL")
    or "support@masyglink.com"
).strip()


class SupportRequest(BaseModel):
    topic: str = Field(
        min_length=1,
        max_length=32,
    )
    message: str = Field(
        min_length=5,
        max_length=4000,
    )
    page: str = Field(
        default="/",
        min_length=1,
        max_length=2048,
    )
    sent_at: str | None = Field(
        default=None,
        max_length=64,
    )


@router.post("/support")
async def submit_support_request(
    payload: SupportRequest,
    request: Request,
    current_user: dict = Depends(
        get_current_user_from_cookie
    ),
):
    topic_key = payload.topic.strip().lower()

    if topic_key not in SUPPORT_TOPICS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Unsupported support topic",
        )

    message_text = payload.message.strip()

    if len(message_text) < 5:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Support message is too short",
        )

    user_id = str(
        current_user.get("userId") or ""
    ).strip()

    user_email = str(
        current_user.get("email") or ""
    ).strip()

    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authenticated user identity is required",
        )

    mail = getattr(
        request.app.state,
        "mail",
        None,
    )

    if mail is None:
        logger.error(
            "Support request mail transport unavailable "
            "user_id=%s",
            user_id,
        )

        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Support messaging is temporarily unavailable",
        )

    topic_label = SUPPORT_TOPICS[
        topic_key
    ]

    identity = (
        user_email
        if user_email
        else user_id
    )

    body = "\n".join(
        [
            "New MASYG support request",
            "",
            f"Topic: {topic_label}",
            f"User: {identity}",
            f"User ID: {user_id}",
            f"Page: {payload.page}",
            (
                f"Client timestamp: {payload.sent_at}"
                if payload.sent_at
                else "Client timestamp: not supplied"
            ),
            "",
            "Message:",
            message_text,
        ]
    )

    support_message = MessageSchema(
        subject=(
            f"[MASYG Support] "
            f"{topic_label} — {identity}"
        ),
        recipients=[SUPPORT_EMAIL],
        body=body,
        subtype=MessageType.plain,
    )

    try:
        result = await send_message_safely(
            mail,
            support_message,
        )
    except Exception as exc:
        logger.error(
            "Support request delivery failed "
            "user_id=%s error_type=%s",
            user_id,
            type(exc).__name__,
        )

        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Support messaging is temporarily unavailable",
        ) from exc

    # The canonical delivery wrapper may either return
    # no value on success or an explicit boolean.
    if result is False:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Support messaging is temporarily unavailable",
        )

    logger.info(
        "Support request accepted "
        "user_id=%s topic=%s",
        user_id,
        topic_key,
    )

    return {
        "message": "Support request sent",
    }


# --- Helper Async Functions ---

users_coll = firestore_db.collection("users")

def _query_user_by_email(email):
    """Blocking Firestore query to get a user by email."""
    docs = list(ref.where(filter=FieldFilter('email', '==', email)).limit(1).stream())
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

    return user_added

async def verify_id_token_async(token, timeout_secs=10):
    """Offload Firebase token verification to a thread."""
    loop = asyncio.get_running_loop()
    return await asyncio.wait_for(
        loop.run_in_executor(None, firebase_auth.verify_id_token, token),
        timeout_secs
    )

# --- Async Route Handlers ---
async def update_last_login_async(user_id: str, timeout_secs: int = 10):
    """
    Update the lastLoginAt field on the user document.
    Stores an ISO 8601 string in UTC.
    """
    now_iso = datetime.now(timezone.utc).isoformat()

    def _update_last_login():
        # you can use ref or users_coll; both point to "users"
        users_coll.document(user_id).update({"lastLoginAt": now_iso})

    loop = asyncio.get_running_loop()
    await asyncio.wait_for(
        loop.run_in_executor(executor, _update_last_login),
        timeout_secs
    )

@router.post("/signup", status_code=status.HTTP_201_CREATED)
async def signup(request: Request,  background_tasks: BackgroundTasks):
    data = await request.json()
    if not data:
        raise HTTPException(status_code=400, detail="No data provided")

    username = data.get('username')
    email = normalize_email(data.get('email'))
    password = data.get('password')
    is_subscribed = data.get('isSubscribed', False)

    if not username or not email or not password:
        raise HTTPException(status_code=400, detail="Username, email, and password are required")

    loop = asyncio.get_running_loop()
    existing_users = await loop.run_in_executor(
        executor,
        lambda: list(ref.where(filter=FieldFilter('email', '==', email)).limit(1).stream())
    )
    if existing_users:
        raise HTTPException(status_code=400, detail="Email already exists")
    now_iso = datetime.now(timezone.utc).isoformat()

    new_user = {
        'username': username,
        'email': email,
        'password': generate_password_hash(password, method='pbkdf2:sha256'),
        'isSubscribed': is_subscribed,
        'hasUsedTrial': False,
        'authProviders': ['password'],
        'createdAt': now_iso,  # or datetime.now().isoformat() for local time
        'lastLoginAt': now_iso,  # 👈 first login time = signup time

    }
    user_added = await add_new_user_async(new_user, timeout_secs=10)



    # Build a modern HTML email template with inline CSS
    html_body = f"""
   <!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <title>Welcome to Masyg</title>
</head>
<body style="margin:0; padding:0; font-family:Arial, sans-serif; background-color:#f7f7f7;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background-color:#f7f7f7;">
    <tr>
      <td align="center">
        <table width="600" cellpadding="0" cellspacing="0" style="background-color:#ffffff; padding:30px; 
        border-radius:8px; margin-top:40px; box-shadow:0 2px 6px rgba(0,0,0,0.1);">
          <tr>
            <td style="text-align:center; padding-bottom:20px;">
              <h1 style="margin:0; color:#333;">🎉 Welcome to Masyg Link</h1>
              <p style="margin:10px 0 0; color:#666;">Let’s Simplify Your Invoice & Receipt Workflow</p>
            </td>
          </tr>
          <tr>
            <td style="padding:20px 0; color:#444; font-size:16px; line-height:1.6;">
              <p>Hi <strong>{username}</strong>,</p>
              <p>Thanks for signing up for <strong>Masyg</strong>, your new go-to tool for effortless 
              receipt and invoice data extraction. We're excited to have you on board!</p>
              <ul style="padding-left: 20px; color:#444;">
                <li>📄 Extract key info from receipts and invoices in seconds</li>
                <li>📄 Export to excel, csv format</li>
                <li>🔄 Sync data with tools like QuickBooks and Xero</li>
                <li>📊 Save time and reduce manual entry errors</li>
              </ul>
              <p>You're all set to start extracting like a pro. Simply upload your first file and let Masyg 
              Link do the heavy lifting.</p>
              <p style="text-align:center; margin:30px 0;">
                <a href="{os.getenv('CLIENT_URL')}" style="display:inline-block; padding:12px 24px; 
                background-color:#2c7be5; color:#ffffff; text-decoration:none; border-radius:5px;">👉 Get Started Now</a>
              </p>
              <p>If you have any questions or need help, just reply to this email — we're here for you.</p>
              <p style="margin-top:30px;">Welcome again,</p>
              <p><strong>— The Masyg Link Team</strong><br><a href="{os.getenv('CLIENT_URL')}" style="color:#2c7be5;">masyglink.com</a></p>
            </td>
          </tr>
        </table>
        <p style="font-size:12px; color:#999; margin-top:20px;">You’re receiving this email because you signed up for Masyg Link. If this wasn’t you, please ignore this message.</p>
      </td>
    </tr>
  </table>
</body>
</html>

    """

    message = MessageSchema(
        subject="🎉 Welcome to Masyg Link – Let’s Simplify Your Receipt Workflow",
        recipients=[email],
        body=html_body,
        subtype="html"
    )

    # Schedule email sending in the background
    background_tasks.add_task(send_message_safely, request.app.state.mail, message)




    return {"message": "User created", "userId": user_added['userId']}



@router.post("/logout")
async def logout(request: Request, response: Response):
    # Revoke the current browser refresh session when possible. Invalid/legacy
    # refresh cookies are still cleared so logout remains idempotent.
    raw_refresh = request.cookies.get("refresh_token")
    if raw_refresh:
        try:
            payload = decode_jwt_token(raw_refresh, expected_type="refresh")
            user_id = payload.get("sub")
            auth_session_id = payload.get("session_id")
            if user_id and auth_session_id:
                await revoke_refresh_session(user_id, auth_session_id)
        except JWTError:
            pass

    cookie_opts = cookie_security_options(os.getenv("FAST_API_ENV"))
    response.delete_cookie(
        "access_token", path=cookie_opts["path"],
        secure=cookie_opts["secure"], samesite=cookie_opts["samesite"],
    )
    response.delete_cookie(
        "refresh_token", path=cookie_opts["path"],
        secure=cookie_opts["secure"], samesite=cookie_opts["samesite"],
    )
    response.delete_cookie(
        "csrf_token", path=cookie_opts["path"],
        secure=cookie_opts["secure"], samesite=cookie_opts["samesite"],
    )

    # The Starlette session owns only browser-local coordination state such as
    # client_id; it is not an authentication source of truth. Disconnect the
    # active Socket.IO owner before clearing/rotating that browser identity.
    if hasattr(request, "session"):
        client_id = request.session.get("client_id")
        if client_id:
            sid = await socket_connections.current_sid(client_id)
            if sid:
                try:
                    await sio.disconnect(sid)
                except Exception as exc:
                    logger.warning("Socket disconnect during logout failed error_type=%s", type(exc).__name__)
        request.session.clear()

    return {"message": "Logout successful"}




@router.post("/update")
async def update_user_info(request: Request, current_user: dict = Depends(get_current_user_from_cookie)):

    firebase_user_id = current_user.get('userId')
    if not firebase_user_id:
        raise HTTPException(status_code=401, detail="User not logged in")
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

    old_email = current_user.get('email', '').lower().strip()
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
        try:
            await asyncio.to_thread(
                stripe.Customer.modify,
                stripe_customer_id,
                email=updates['email'],
            )
        except Exception as exc:
            logger.error("Stripe customer email update failed error_type=%s", type(exc).__name__)
            # Firestore is already authoritative for the user profile. Surface the
            # sync problem explicitly instead of blocking the event loop or leaking
            # provider details.
            raise HTTPException(status_code=502, detail="Failed to synchronize billing email") from exc

    return {"message": "User information updated successfully"}


# Pydantic model for request validation
class ResetRequest(BaseModel):
    email: EmailStr





@router.post("/request-reset")
async def request_reset(request: Request, reset_req: ResetRequest, background_tasks: BackgroundTasks):
    email = normalize_email(str(reset_req.email))
    loop = asyncio.get_running_loop()
    users_query = await loop.run_in_executor(
        executor,
        lambda: list(ref.where(filter=FieldFilter('email', '==', email)).limit(1).stream()) if ref else []
    )

    # Always return the same response so this endpoint cannot be used to enumerate accounts.
    if not users_query:
        logger.info("Password reset requested for unknown account")
        return {"message": "If an account exists for that email, a password reset link has been sent."}

    user_doc = users_query[0]
    token, token_hash, expires_at = generate_password_reset_token(
        ttl_minutes=int(os.getenv("PASSWORD_RESET_TTL_MINUTES", "30"))
    )
    await loop.run_in_executor(
        executor,
        lambda: user_doc.reference.update({
            'resetTokenHash': token_hash,
            'resetTokenExpiresAt': expires_at,
            'resetToken': firestore.DELETE_FIELD,
        })
    )

    reset_url = f"{os.getenv('CLIENT_URL')}/reset-password/{token}"
    html_body = f"""
    <html>
      <body style="font-family:Arial,sans-serif;background:#f4f4f4;padding:20px;">
        <div style="background:#fff;max-width:600px;margin:0 auto;padding:30px;border-radius:8px;">
          <h2>Password Reset Request</h2>
          <p>We received a request to reset your password. This link expires shortly.</p>
          <p><a href="{reset_url}">Reset Password</a></p>
          <p>If you did not request a password reset, you can ignore this email.</p>
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
    background_tasks.add_task(send_message_safely, request.app.state.mail, message)
    return {"message": "If an account exists for that email, a password reset link has been sent."}


@router.post("/reset-password")
async def reset_password(request: Request):
    data = await request.json()
    token = (data.get('token') or '').strip()
    new_password = data.get('password')
    if not token or not new_password:
        raise HTTPException(status_code=400, detail="Token and new password are required")
    if len(new_password) < int(os.getenv("MIN_PASSWORD_LENGTH", "8")):
        raise HTTPException(status_code=400, detail="Password does not meet minimum length")

    token_hash = hash_password_reset_token(token)
    loop = asyncio.get_running_loop()
    user_query = await loop.run_in_executor(
        executor,
        lambda: list(ref.where(filter=FieldFilter('resetTokenHash', '==', token_hash)).limit(1).stream())
    )
    if not user_query:
        raise HTTPException(status_code=400, detail="Invalid or expired token.")

    user_doc = user_query[0]
    user_data = user_doc.to_dict() or {}
    if reset_token_is_expired(user_data.get('resetTokenExpiresAt')):
        await loop.run_in_executor(
            executor,
            lambda: user_doc.reference.update({
                'resetTokenHash': firestore.DELETE_FIELD,
                'resetTokenExpiresAt': firestore.DELETE_FIELD,
            })
        )
        raise HTTPException(status_code=400, detail="Invalid or expired token.")

    hashed_password = generate_password_hash(new_password, method='pbkdf2:sha256')
    await loop.run_in_executor(
        executor,
        lambda: user_doc.reference.update({
            'password': hashed_password,
            'authProviders': firestore.ArrayUnion(['password']),
            'resetTokenHash': firestore.DELETE_FIELD,
            'resetTokenExpiresAt': firestore.DELETE_FIELD,
        })
    )
    # A password reset is a credential-recovery boundary. Any previously issued
    # refresh session must stop minting access tokens immediately.
    await revoke_all_refresh_sessions(user_doc.id)
    return {"message": "Password updated successfully."}


@router.post("/create-customer-portal")
async def create_customer_portal(request: Request,  current_user: dict = Depends(get_current_user_from_cookie)):
    try:
        firebase_user_id = current_user.get('userId')
        # print(firebase_user_id, "user_id")

        loop = asyncio.get_running_loop()
        doc = await loop.run_in_executor(
            executor,
            lambda: ref.document(firebase_user_id).get()
        )
        if not doc.exists:
            # print("urddur")
            raise HTTPException(status_code=404, detail="User not found in Firestore")
        user_data = doc.to_dict()
        customer_id = user_data.get('stripeCustomerId')
        if not customer_id:
            # print("urur")
            raise HTTPException(status_code=400, detail="Stripe customer ID not found for the user")

        session_data = await asyncio.to_thread(
            lambda: stripe.billing_portal.Session.create(
                customer=customer_id,
                return_url=os.getenv('CLIENT_URL')
            )
        )
        return {"url": session_data.url}

    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Customer portal creation failed error_type=%s", type(exc).__name__)
        raise HTTPException(status_code=502, detail="Failed to create customer portal") from exc


from fastapi import Request, HTTPException, status, Depends
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
    current_user: dict = Depends(get_current_user_from_cookie)
):

    # Validate that the authenticated user has a valid user ID.
    user_id = current_user.get('userId')

    if not user_id:
        logger.error("Authenticated user does not have a userId")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User ID not found")

    # Validate that the authenticated user's email is available.
    session_email = current_user.get("email")
    if not session_email:
        logger.error("Authenticated user does not have an email")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Email not found")

    # Check that the authenticated email matches the provided email.
    if session_email.lower() != email.lower():
        logger.error(
            f"Email mismatch: authenticated email '{session_email}' does not match provided email '{email}'"
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Unauthorized: Email mismatch")

    # Retrieve the user document from Firestore using the user_id.
    doc_ref = ref.document(user_id)
    doc = await asyncio.to_thread(doc_ref.get)
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
            # If there is a Stripe customer ID, try to delete the associated Stripe customer data.
            if stripe_customer_id:
                await delete_stripe_customer_data(stripe_customer_id)
            # Firestore does not cascade-delete subcollections. Remove refresh
            # sessions before deleting the parent user document.
            await revoke_all_refresh_sessions(user_id)
            # Delete the user document from Firestore.
            await document_delete(user_ref)
            logger.info("Deleted account for user '%s'", user_id)
        except Exception as e:
            logger.exception("Failed to delete account for user '%s': %s", user_id, e)
            raise HTTPException(status_code=500, detail="Failed to delete account") from e
    else:
        logger.warning("User account '%s' not found", user_id)
        raise HTTPException(status_code=404, detail="User account not found")

    if hasattr(request, "session"):
        client_id = request.session.get("client_id")
        if client_id:
            sid = await socket_connections.current_sid(client_id)
            if sid:
                try:
                    await sio.disconnect(sid)
                except Exception as exc:
                    logger.warning("Socket disconnect during account deletion failed error_type=%s", type(exc).__name__)
        request.session.clear()

    response = JSONResponse(
        content={'message': 'Your account has been deleted successfully'},
        status_code=200
    )
    cookie_opts = cookie_security_options(os.getenv("FAST_API_ENV"))
    for cookie_name in ("access_token", "refresh_token", "csrf_token"):
        response.delete_cookie(
            cookie_name,
            path=cookie_opts["path"],
            secure=cookie_opts["secure"],
            samesite=cookie_opts["samesite"],
        )
    return response










@router.post("/login")
async def login(request: Request, response: Response):
    # 2. Parse incoming JSON
    data           = await request.json()
    email          = normalize_email(data.get("email"))
    password       = data.get("password")
    google_id_token= data.get("googleIdToken")
    remember_me    = data.get("rememberMe", False)

    # 3. Authenticate or create user
    if google_id_token:
        login_provider = "google"
        decoded = await verify_id_token_async(google_id_token)
        email = normalize_email(decoded["email"])
        username = decoded.get("name", "Google User")
        user = await query_user_by_email_async(email) or await add_new_user_async({
            "email": email,
            "username": username,
            "isSubscribed": False,
            "authProviders": ["google"],
        })
    else:
        login_provider = "password"
        if not email or not password:
            raise HTTPException(status_code=400, detail="Email and password are required")

        user = await query_user_by_email_async(email)
        stored_hash = user.get("password") if user else None
        password_ok = bool(stored_hash) and check_password_hash(stored_hash, password)
        if not password_ok:
            # Keep the public response generic while preserving an internal reason.
            reason = "user_not_found" if not user else "password_mismatch"
            logger.warning("Login rejected reason=%s", reason)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password",
            )

    user_id = user["userId"]

    # Opportunistically migrate legacy accounts to explicit provider ownership.
    # ArrayUnion is idempotent, so repeated logins do not duplicate entries.
    await asyncio.to_thread(
        users_coll.document(user_id).update,
        {"authProviders": firestore.ArrayUnion([login_provider])},
    )

    # ✅ 3.5. Update last login timestamp in Firestore
    await update_last_login_async(user_id)

    # 4. Gather trial info (unchanged)
    trial_ref = users_coll.document(user_id).collection("plan").document("trial")
    trial_snap= await asyncio.to_thread(trial_ref.get)
    has_used_trial = trial_snap.exists and trial_snap.to_dict().get("hasUsed", False)
    trial_date     = trial_snap.to_dict().get("date") if trial_snap.exists else None

    # 5. Build consistent payload
    user_payload = {
        "userId":       user_id,
        "username":     user.get("username"),
        "email":        user.get("email"),
        "isSubscribed": user.get("isSubscribed", False),
        "hasUsedTrial": has_used_trial,
        "trialDate":    trial_date.isoformat() if trial_date else None,
        # Optional: return previous lastLoginAt value to the client
        "lastLoginAt":  user.get("lastLoginAt"),
    }

    # 6. Create typed access/refresh credentials. Each browser login owns one
    # server-side refresh session; only the hash of its current JTI is stored.
    token_payload = {"sub": user_id, **user_payload}
    access_token = create_access_token(
        data=token_payload,
        expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
    )
    auth_session_id = str(uuid.uuid4())
    refresh_jti = str(uuid.uuid4())
    refresh_token = create_refresh_token(
        data=token_payload,
        session_id=auth_session_id,
        remember_me=remember_me,
        jti=refresh_jti,
    )
    await create_refresh_session(
        user_id,
        auth_session_id,
        refresh_jti,
        expires_at=datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS),
    )

    # 7. Set cookies. Local HTTP development cannot store Secure cookies;
    # production cross-site frontend/API deployments require Secure + SameSite=None.
    cookie_opts = cookie_security_options(os.getenv("FAST_API_ENV"))
    response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
        secure=cookie_opts["secure"],
        samesite=cookie_opts["samesite"],
        path=cookie_opts["path"],
    )

    refresh_opts = dict(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        secure=cookie_opts["secure"],
        samesite=cookie_opts["samesite"],
        path=cookie_opts["path"],
    )

    if remember_me:
        refresh_opts["max_age"] = REFRESH_TOKEN_EXPIRE_DAYS * 24 * 3600
    response.set_cookie(**refresh_opts)

    csrf = generate_csrf_token()
    response.set_cookie(
        key="csrf_token",
        value=csrf,
        httponly=False,
        secure=cookie_opts["secure"],
        samesite=cookie_opts["samesite"],
        path=cookie_opts["path"],
    )

    # 8. Return user data
    return {"message": "Login successful", "user": user_payload}


@router.get("/current")
async def get_current_user(current_user: dict = Depends(get_current_user_from_cookie)):
    uid = current_user.get("userId")
    if not uid:
        raise HTTPException(status_code=401, detail="Not authenticated")

    doc = await asyncio.to_thread(users_coll.document(uid).get)
    if not doc.exists:
        raise HTTPException(status_code=404, detail="User not found")

    data = doc.to_dict() or {}
    # Ensure trial mirror is present; if not, rebuild minimal structure from subdoc
    trial = data.get("trial") or {}
    if not trial.get("trialEnd"):
        tdoc = await asyncio.to_thread(
            users_coll.document(uid).collection("plan").document("trial").get
        )
        if tdoc.exists:
            t = tdoc.to_dict()
            trial = {
                "hasUsed": bool(t.get("hasUsed")),
                "date": t.get("date").isoformat() if isinstance(t.get("date"), datetime) else None,
                "trialEnd": t.get("trialEnd").isoformat() if isinstance(t.get("trialEnd"), datetime) else None,
                "trialExpired": False,  # client can compute, but this keeps shape stable
            }

    return {"user": {
        "userId": uid,
        "username": data.get("username"),
        "email": data.get("email"),
        "isSubscribed": data.get("isSubscribed", False),
        "subscriptionStatus": data.get("subscriptionStatus", "none"),
        "cancelAt": data.get("cancelAt"),
        "trial": trial,
    }}


@router.post("/refresh-token")
async def refresh_token(request: Request):
    raw_refresh = request.cookies.get("refresh_token")
    if not raw_refresh:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token missing",
        )

    try:
        payload = decode_jwt_token(raw_refresh, expected_type="refresh")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    user_id = payload.get("sub")
    auth_session_id = payload.get("session_id")
    presented_jti = payload.get("jti")
    if not user_id or not auth_session_id or not presented_jti:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    # Rebuild claims from current server-side user state instead of trusting stale
    # subscription/email claims carried by the refresh token.
    user_doc, trial_snap = await asyncio.gather(
        asyncio.to_thread(users_coll.document(user_id).get),
        asyncio.to_thread(
            users_coll.document(user_id).collection("plan").document("trial").get
        ),
    )
    if not user_doc.exists:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    user_data = user_doc.to_dict() or {}
    has_used_trial = False
    if trial_snap.exists:
        has_used_trial = bool((trial_snap.to_dict() or {}).get("hasUsed", False))

    token_payload = {
        "sub": user_id,
        "username": user_data.get("username"),
        "email": user_data.get("email"),
        "isSubscribed": user_data.get("isSubscribed", False),
        "hasUsedTrial": has_used_trial,
    }

    remember_me = bool(payload.get("remember_me", False))
    new_refresh_jti = str(uuid.uuid4())
    refresh_expires_at = datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    rotated = await rotate_refresh_session(
        user_id,
        auth_session_id,
        presented_jti,
        new_refresh_jti,
        expires_at=refresh_expires_at,
    )
    if not rotated:
        # A rotated/replayed/revoked refresh token must not mint new credentials.
        raise HTTPException(status_code=401, detail="Refresh token expired or revoked")

    new_access_token = create_access_token(
        data=token_payload,
        expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
    )
    new_refresh_token = create_refresh_token(
        data=token_payload,
        session_id=auth_session_id,
        remember_me=remember_me,
        jti=new_refresh_jti,
    )

    new_csrf_token = generate_csrf_token()
    response = JSONResponse(content={"message": "Token refreshed"})
    cookie_opts = cookie_security_options(os.getenv("FAST_API_ENV"))
    response.set_cookie(
        key="access_token", value=new_access_token, httponly=True,
        secure=cookie_opts["secure"], samesite=cookie_opts["samesite"], path=cookie_opts["path"],
    )
    refresh_opts = dict(
        key="refresh_token", value=new_refresh_token, httponly=True,
        secure=cookie_opts["secure"], samesite=cookie_opts["samesite"], path=cookie_opts["path"],
    )
    if remember_me:
        refresh_opts["max_age"] = REFRESH_TOKEN_EXPIRE_DAYS * 24 * 3600
    response.set_cookie(**refresh_opts)
    response.set_cookie(
        key="csrf_token", value=new_csrf_token, httponly=False,
        secure=cookie_opts["secure"], samesite=cookie_opts["samesite"], path=cookie_opts["path"],
    )
    return response
