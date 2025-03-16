#!/usr/bin/env python
"""
Run the FastAPI app with python‑socketio’s native AsyncServer in ASGI mode.

Run with an ASGI server such as uvicorn:
    uvicorn server:asgi_app --host 0.0.0.0 --port 5000
"""

import os
import logging
from typing import Optional

import redis
import stripe
import asyncio
import urllib.parse
from datetime import datetime
from dotenv import load_dotenv, find_dotenv
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings  # Updated import for Pydantic settings

from masyg_extractor.services import global_executor
from masyg_extractor.services.helper import init_mail

# -------------------------
# Load environment variables
# -------------------------
load_dotenv(find_dotenv())
ENV = os.getenv("FAST_API_ENV", "development").lower()
print("Environment:", ENV)

# -------------------------
# Define configuration with Pydantic Settings (allowing extra keys)
# -------------------------
class Settings(BaseSettings):
    secret_key: str =Field("", env="SECRET_KEY")
    redis_url: str =Field("", env="REDIS_URL")
    client_url: str = Field("", env="CLIENT_URL")
    server_url: str = Field("", env="SERVER_URL")
    # Optional; set if needed
    MASYG_EXTRACTOR_STRIPE_SECRET_KEY: str = Field("", env="MASYG_EXTRACTOR_STRIPE_SECRET_KEY")

    server_port: int = 5000

    class Config:
        env_file = ".env"
        extra = "ignore"  # Ignore extra environment variables

@lru_cache()
def get_settings():
    return Settings()

settings = get_settings()

# -------------------------
# Initialize Firebase early.
# -------------------------
from masyg_extractor.firebase.firebase_init import firebase_init
firebase_init()

# -------------------------
# Create FastAPI app.
# -------------------------
from fastapi import FastAPI
app = FastAPI()
# from starlette.middleware.sessions import SessionMiddleware
# app.add_middleware(SessionMiddleware, secret_key=settings.secret_key)
init_mail(app)
# -------------------------
# (Optional) Add production security middleware.
# -------------------------

from fastapi.middleware.cors import CORSMiddleware

origins = [settings.client_url, "http://localhost:3000"] if settings.client_url else ["http://localhost:3000"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import RedirectResponse


class ConditionalHTTPSRedirectMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if request.scope["type"] == "websocket":
            print("WebSocket connection detected, bypassing HTTPS redirect")
            return await call_next(request)

        if request.url.scheme != "https":
            print(f"Redirecting HTTP request to HTTPS: {request.url}")
            url = request.url.replace(scheme="https")
            return RedirectResponse(url)

        return await call_next(request)
from starlette.middleware.trustedhost import TrustedHostMiddleware


class WebsocketSafeTrustedHostMiddleware(TrustedHostMiddleware):
    async def __call__(self, scope, receive, send):
        # Bypass host validation for WebSocket handshake requests
        if scope["type"] == "http":
            headers = dict(scope["headers"])
            upgrade_header = headers.get(b"upgrade", b"").decode().lower()
            if upgrade_header == "websocket":
                return await self.app(scope, receive, send)

        # Proceed with host validation for other HTTP requests
        return await super().__call__(scope, receive, send)

from starlette.middleware.sessions import SessionMiddleware

class WebsocketSafeSessionMiddleware(SessionMiddleware):
    async def __call__(self, scope, receive, send):
        # If this is a websocket connection, bypass the session middleware.
        if scope["type"] == "websocket":
            return await self.app(scope, receive, send)
        # Otherwise, handle as usual.
        return await super().__call__(scope, receive, send)


if ENV == "production":
    # Conditional HTTPS redirect (bypasses websockets)
    app.add_middleware(ConditionalHTTPSRedirectMiddleware)

    # Use custom TrustedHostMiddleware that skips WebSocket connections.
    app.add_middleware(
        WebsocketSafeTrustedHostMiddleware,
        allowed_hosts=[
            "api-preview.masyglink.com",
            "api-preview.up.railway.app",  # Railway's internal host
            "*.masyglink.com",  # Wildcard for subdomains
            "preview.masyglink.com",
            "www.preview.masyglink.com",
            "extractor.masyglink.com",
            "www.extractor.masyglink.com"
        ]
    )

    # Use your custom session middleware if needed (make sure it also bypasses websockets)
    # For example, if you haven't already, subclass SessionMiddleware similarly.
    # Here, we assume SessionMiddleware is already handled properly.
    from starlette.middleware.sessions import SessionMiddleware

    app.add_middleware(WebsocketSafeSessionMiddleware, secret_key=settings.secret_key)

    # Set up Redis connection for sessions (or other uses)
    app.state.session_redis = redis.from_url(settings.redis_url)
else:
    app.add_middleware(SessionMiddleware, secret_key=settings.secret_key)

    app.state.session_redis = None
print('redisurl',settings.redis_url)
print('server url',settings.server_url)
print('client url',settings.client_url)
# -------------------------
# Set up CORS middleware.
# -------------------------


# -------------------------
# Initialize Stripe.
# -------------------------
stripe.set_app_info(
    'stripe-samples/checkout-single-subscription',
    version='0.0.1',
    url='https://github.com/stripe-samples/checkout-single-subscription'
)
stripe.api_key = settings.MASYG_EXTRACTOR_STRIPE_SECRET_KEY
# print( settings.MASYG_EXTRACTOR_STRIPE_SECRET_KEY)

# -------------------------
# Register your routers (FastAPI’s equivalent of FAST_API blueprints).
# -------------------------
# Assuming you have a module `routes` with a function `register_routers(app)`
from masyg_extractor.routes import register_routers
register_routers(app)

# -------------------------
# Set up logging.
# -------------------------
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG if ENV == "development" else logging.INFO)
# (Additional logging handlers can be added as needed.)

# -------------------------
# Import Socket.IO instance and define events.
# -------------------------
from masyg_extractor.utils.extensions import sio

@sio.event
async def connect(sid, environ, auth):
    scope = environ.get("asgi.scope", {})
    query_string = scope.get("query_string", b"").decode()
    print("Received query string:", query_string)
    query_params = urllib.parse.parse_qs(query_string)
    client_id = query_params.get('clientId', ['Guest'])[0]
    print(f"Connecting client: {client_id}, SID: {sid}")
    await sio.enter_room(sid, client_id)
    await sio.emit("welcome", {"message": f"Welcome, {client_id}!"}, room=client_id)
    global_executor.MAIN_LOOP = asyncio.get_running_loop()

@sio.event
async def disconnect(sid):
    print(f"Client disconnected: {sid}")

# -------------------------
# ASGI setup: Combine FastAPI and Socket.IO.
# -------------------------
from socketio import ASGIApp
asgi_app = ASGIApp(sio, app)

# -------------------------
# Entry point.
# -------------------------
if __name__ == "__main__":
    import uvicorn
    server_port = settings.server_port
    print(f"Starting ASGI server on port {server_port}")
    uvicorn.run("server:asgi_app", host="0.0.0.0", port=server_port, reload=(ENV == "development"))
