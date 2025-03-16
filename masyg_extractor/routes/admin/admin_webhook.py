import os
import hmac
import hashlib
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse
from firebase_admin import db
from masyg_extractor.services.file_extractor_service import *  # if needed

router = APIRouter()

async def verify_webhook(request: Request) -> bool:
    """
    Verify Firebase Webhook Signature.
    """
    webhook_secret = os.getenv("WEBHOOK_SECRET", "your_webhook_secret")
    received_signature = request.headers.get("X-Firebase-Signature")
    if not received_signature:
        return False

    body = await request.body()
    expected_signature = hmac.new(webhook_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected_signature, received_signature)

@router.post("/admin/user/update")
async def update_user_from_webhook(request: Request):
    """
    Receives webhook from Firebase and updates user session.
    """
    # Verify the request (optional security step)
    if not await verify_webhook(request):
        raise HTTPException(status_code=403, detail="Invalid webhook signature")

    data = await request.json()
    user_id = data.get("userId")
    if not user_id:
        raise HTTPException(status_code=400, detail="Missing userId")

    # Fetch updated user details from Firebase Realtime Database.
    firebase_user = db.reference("users").child(user_id).get()
    if not firebase_user:
        raise HTTPException(status_code=404, detail="User not found in database")

    # Update session data if the user is logged in.
    # Assumes your session middleware stores session data in request.session.
    if "user" in request.session and request.session.get("userId") == user_id:
        request.session["user"]["isSubscribed"] = firebase_user.get("isSubscribed", False)
        request.session["user"]["hasUsedTrial"] = firebase_user.get("hasUsedTrial", False)

    return JSONResponse({"message": "User session updated"}, status_code=200)
