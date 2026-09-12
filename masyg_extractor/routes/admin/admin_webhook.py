import os
import hmac
import hashlib
import asyncio
import logging
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse
from firebase_admin import db
# from masyg_extractor.services.file_extractor_service import *  # if needed

router = APIRouter()
logger = logging.getLogger("masyg.webhook")

async def verify_webhook(request: Request) -> bool:
    """
    Verify Firebase Webhook Signature.
    """
    webhook_secret = (os.getenv("WEBHOOK_SECRET") or "").strip()
    if not webhook_secret or webhook_secret == "your_webhook_secret":
        # Fail closed. A predictable fallback secret would make this mutation
        # endpoint forgeable whenever deployment configuration is incomplete.
        logger.error("Admin webhook rejected because WEBHOOK_SECRET is not configured")
        return False

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
    firebase_user = await asyncio.to_thread(
        lambda: db.reference("users").child(user_id).get()
    )
    if not firebase_user:
        raise HTTPException(status_code=404, detail="User not found in database")

    # Application authentication is JWT-cookie based. Browser SessionMiddleware is
    # reserved for coordination state (for example client_id), so there is no
    # authoritative request.session["user"] mirror to mutate here.
    return JSONResponse({"message": "User update acknowledged"}, status_code=200)
