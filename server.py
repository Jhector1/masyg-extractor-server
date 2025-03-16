#!/usr/bin/env python
"""
Run the FastAPI app with python‑socketio’s native AsyncServer in ASGI mode.

Run with an ASGI server such as uvicorn:
    uvicorn server:asgi_app --host 0.0.0.0 --port 5000
"""

import os
import logging
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
    secret_key: str = "BAD_SECRET_KEY"
    redis_url: str = "redis://127.0.0.1:6379"
    client_url: str = "http://localhost:3000"
    server_url: str = None  # Optional; set if needed
    MASYG_EXTRACTOR_STRIPE_SECRET_KEY: str = Field("", env="MASYG_EXTRACTOR_STRIPE_SECRET_KEY")

    server_port: int = 5000

    class Config:
        env_file = "masyg_extractor/.env"
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
from starlette.middleware.sessions import SessionMiddleware
app.add_middleware(SessionMiddleware, secret_key=settings.secret_key)
init_mail(app)
# -------------------------
# (Optional) Add production security middleware.
# -------------------------

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import RedirectResponse


class ConditionalHTTPSRedirectMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        # If this is a websocket connection, bypass redirection.
        if request.scope["type"] == "websocket":
            return await call_next(request)

        # Check if request is not secure (HTTP)
        if request.url.scheme != "https":
            url = request.url.replace(scheme="https")
            return RedirectResponse(url)

        return await call_next(request)


# Then add this middleware in production:
if ENV == "production":
    app.add_middleware(ConditionalHTTPSRedirectMiddleware)

# if ENV == "production":
#     from starlette.middleware.httpsredirect import HTTPSRedirectMiddleware
#     app.add_middleware(HTTPSRedirectMiddleware)
    app.state.session_redis = redis.from_url(settings.redis_url)
else:
    app.state.session_redis = None

# -------------------------
# Set up CORS middleware.
# -------------------------
from fastapi.middleware.cors import CORSMiddleware

origins = [settings.client_url, "http://localhost:3000"] if settings.client_url else ["http://localhost:3000"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
    # Extract query string parameters.
    scope = environ.get("asgi.scope", {})
    query_string = scope.get("query_string", b"").decode()
    query_params = urllib.parse.parse_qs(query_string)
    client_id = query_params.get('clientId', ['Guest'])[0]

    await sio.enter_room(sid, client_id)
    print(f"*************{client_id}**********************")

    print(f"Client connected: {sid}, Client ID: {client_id}")
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
