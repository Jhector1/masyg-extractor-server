from datetime import datetime, timedelta
from fastapi import APIRouter, Request, HTTPException, status, Depends, Header
from fastapi.responses import JSONResponse
from jose import jwt, JWTError
import asyncio
import concurrent.futures
import time
import uuid
from werkzeug.security import generate_password_hash, check_password_hash
from firebase_admin import auth as firebase_auth
from firebase_admin import firestore
import os
import secrets

# Initialize Firestore client and collection reference
firestore_db = firestore.client()
ref = firestore_db.collection("users")
executor = concurrent.futures.ThreadPoolExecutor(max_workers=10)

# JWT settings
SECRET_KEY = os.getenv("SECRET_KEY")  # Replace with a secure secret in production!
ALGORITHM = os.getenv("ALGORITHM")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", 30))

def create_access_token(data: dict, expires_delta: timedelta = None):
    now = datetime.utcnow()
    expire = now + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode = data.copy()
    to_encode.update({
        "iat": now,
        # "nbf": now,
        "exp": expire,
        "jti": str(uuid.uuid4()),          # optional: a unique ID
    })
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
from typing import Optional
from fastapi import Header

def get_current_user_from_cookie(
    request: Request,
    csrf_token_header: Optional[str] = Header(None, alias="X-CSRF-Token")
):
    token = request.cookies.get("access_token")
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing access token cookie"
        )

    try:
        payload = jwt.decode(token,
                             SECRET_KEY,
                             algorithms=[ALGORITHM],

                             options={
                                 "verify_signature": True,
                                 "verify_exp": True,
                                 "verify_nbf": False,  # ← disable nbf check
                             }
                             )
        user_id = payload.get("sub")
        if user_id is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token"
            )

        # ✅ Skip CSRF checks for GET requests
        if request.method == "GET":
            return {
                "userId": user_id,
                "username": payload.get("username"),
                "email": payload.get("email")
            }

        # For POST/PUT/DELETE → enforce CSRF
        csrf_cookie = request.cookies.get("csrf_token")
        if not csrf_cookie:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Missing CSRF token cookie"
            )
        if csrf_token_header != csrf_cookie:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="CSRF token mismatch"
            )

        return {
            "userId": user_id,
            "username": payload.get("username"),
            "email": payload.get("email")
        }

    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Could not validate credentials"
        )



def verify_jwt_token(token: str):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM],             options={"verify_nbf": False}
)
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")

def generate_csrf_token():
    return secrets.token_hex(16)
