
# !/usr/bin/env python
"""
Run the FastAPI app with python-socketio’s native AsyncServer in ASGI mode.

Run with an ASGI server such as uvicorn:
    uvicorn server:app --host 0.0.0.0 --port 5000
"""

import os

from starlette.middleware.base import BaseHTTPMiddleware

ENV = os.getenv("FAST_API_ENV", "development").lower()
import logging
import uuid
import logging
if ENV != "development":
    logging.getLogger("uvicorn").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.error").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("socketio.server").setLevel(logging.WARNING)
    logging.getLogger("engineio.server").setLevel(logging.WARNING)
    # For HTTP client libraries (like httpx):
    logging.getLogger("httpx").setLevel(logging.WARNING)

import stripe
import asyncio

import urllib.parse
from dotenv import load_dotenv, find_dotenv
from starlette.responses import JSONResponse
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from masyg_extractor.services.helper import init_mail
from masyg_extractor.services.my_log import SocketIOHandler, logger, log_processor
from masyg_extractor.utils.extensions import sio

# Load environment variables.
load_dotenv(find_dotenv())

print("Environment:", ENV)

# Initialize Firebase early.
from masyg_extractor.firebase.firebase_init import firebase_init
firebase_init()

from fastapi import FastAPI,Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.httpsredirect import HTTPSRedirectMiddleware
from starlette.responses import Response


from starlette.middleware.base import BaseHTTPMiddleware
from fastapi import FastAPI, Request
from starlette.responses import Response

class DefaultCookieMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app: FastAPI,
        default_domain: str,
        default_samesite: str = "strict",
        default_secure: bool = True,
        default_httponly: bool = True,
        default_max_age: int = None
    ):
        super().__init__(app)
        self.default_domain = default_domain
        self.default_samesite = default_samesite
        self.default_secure = default_secure
        self.default_httponly = default_httponly
        self.default_max_age = default_max_age

    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)
        cookies = response.headers.getlist("set-cookie")
        if cookies:
            new_cookies = []
            for cookie in cookies:
                # For example, you may want to treat "csrf_token" differently:
                if "csrf_token=" in cookie:
                    # If the cookie is for CSRF, maybe you don't want HttpOnly.
                    desired_httponly = False
                else:
                    desired_httponly = self.default_httponly

                # Append Domain if missing
                if "Domain=" not in cookie:
                    cookie += f"; Domain={self.default_domain}"
                # Append SameSite if missing
                if "SameSite=" not in cookie:
                    cookie += f"; SameSite={self.default_samesite}"
                # Append Secure if needed
                if self.default_secure and "Secure" not in cookie:
                    cookie += "; Secure"
                # Append HttpOnly if needed
                if desired_httponly and "HttpOnly" not in cookie:
                    cookie += "; HttpOnly"
                # Append Max-Age if specified and missing
                if self.default_max_age is not None and "Max-Age=" not in cookie:
                    cookie += f"; Max-Age={self.default_max_age}"
                new_cookies.append(cookie)

            response.headers.__delitem__("set-cookie")
            for cookie in new_cookies:
                response.headers.append("set-cookie", cookie)
        return response
# Create FastAPI app.
app = FastAPI()
secret_key = os.getenv('SECRET_KEY', 'BAD_SECRET_KEY')
init_mail(app)
if ENV == "production":

    # Starlette’s SessionMiddleware uses a cookie-based session.
    # For a Redis-backed store you could integrate a third-party library.
    app.add_middleware(
        SessionMiddleware,
        secret_key=secret_key,
        same_site="none",
        https_only=True  # SESSION_COOKIE_SECURE=True
    )

    app.add_middleware(
        DefaultCookieMiddleware,
        default_domain=".masyglink.com",
        default_samesite="none",
        default_secure=True, # dme
        default_httponly=True,
        default_max_age=1800  # e.g., 30 minutes
    )

    # app.add_middleware(DefaultCookieMiddleware, default_domain=".masyglink.com", default_samesite="strict")

    # Add middleware to fix proxy headers (similar to ProxyFix in Flask)
    app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")

    # Force HTTPS redirection.
    app.add_middleware(HTTPSRedirectMiddleware)

    # Optionally set cookie domain based on SERVER_URL.
    server_url = os.getenv('SERVER_URL')
    if server_url:
        # Custom middleware or response customization would be required
        # to set SESSION_COOKIE_DOMAIN since SessionMiddleware doesn't expose that directly.
        pass
else:
    app.add_middleware(SessionMiddleware, secret_key=secret_key)

    # Load development configuration if needed.
    pass

# Set up CORS.
origins = [os.getenv('CLIENT_URL'), "http://localhost:3000"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# For caching you could integrate a FastAPI caching library.
# For now, this code omits a direct caching equivalent.

# Talisman equivalent: You may want to add custom middleware or use libraries
# to set secure headers as Talisman does in Flask.
# (This example does not include an exact Talisman replacement.)

# Initialize Stripe.
stripe.set_app_info(
    'stripe-samples/checkout-single-subscription',
    version='0.0.1',
    url='https://github.com/stripe-samples/checkout-single-subscription'
)
stripe.api_key = os.getenv('MASYG_EXTRACTOR_STRIPE_SECRET_KEY')

# Register your routers (Flask blueprints equivalent).
# Your register_routers function should import and include your routes.
from masyg_extractor.routes import register_routers
register_routers(app)

# # Set up logging.
# logger = logging.getLogger(__name__)
# logger.setLevel(logging.DEBUG if ENV == "development" else logging.INFO)

# Uncomment and adjust logging as needed.
# if ENV == "development":
#     logger.setLevel(logging.DEBUG)
#     console_handler = logging.StreamHandler()
#     console_handler.setLevel(logging.DEBUG)
#     formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
#     console_handler.setFormatter(formatter)
#     logger.addHandler(console_handler)
# else:
#     logger.setLevel(logging.INFO)

# Set up Socket.IO.
import socketio

@app.post("/client-id")
async def get_client_id(request: Request):
    client_id = request.session.get("client_id")
    if not client_id:
        client_id = str(uuid.uuid4())
        request.session["client_id"] = client_id
    return JSONResponse({"clientId": client_id})


# Create an async Socket.IO server with allowed CORS origins.
# sio = socketio.AsyncServer(async_mode='asgi', cors_allowed_origins=origins)
# @app.on_event("startup")
# async def startup_event():
#     # Start the background log processor.
#     asyncio.create_task(log_processor(sio))
# --- Socket.IO events ---
@sio.event
async def connect(sid, environ, auth):
    # Extract query string parameters.
    # In FastAPI the query string is available in the ASGI scope.
    query_string = environ.get("asgi.scope", {}).get("query_string", b"").decode()
    query_params = urllib.parse.parse_qs(query_string)
    client_id = query_params.get('clientId', ['Guest'])[0]

    await sio.enter_room(sid, client_id)

    await sio.emit("welcome", {"message": f"Welcome, {client_id}!"}, room=client_id)

    # Update the global event loop if needed.
    from masyg_extractor.services import global_executor
    global_executor.MAIN_LOOP = asyncio.get_running_loop()


''''@sio.event
async def connect(sid, environ, auth):
    await sio.enter_room(sid, sid)  # Use the sid as the room.
    print(f"Client connected: {sid}")

    # Configure logging for this connection.
    socket_handler = SocketIOHandler(sio, sid)
    socket_handler.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    socket_handler.setFormatter(formatter)
    logger.addHandler(socket_handler)

    # logger.info(f"Welcome, client with sid {sid}!")
    from masyg_extractor.services import global_executor
    global_executor.MAIN_LOOP = asyncio.get_running_loop()'''


@sio.event
async def disconnect(sid):
    print(f"Client disconnected: {sid}")

# --- ASGI integration ---
# Wrap the FastAPI app with Socket.IO’s ASGIApp.
# This combines the Socket.IO server with your FastAPI routes.
app = socketio.ASGIApp(sio, other_asgi_app=app)

if __name__ == "__main__":
    import uvicorn
    server_port = int(os.getenv("SERVER_PORT", 5000))
    print(f"Starting ASGI server on port {server_port}")
    if ENV == "development":
        uvicorn.run("server:app", host="0.0.0.0", port=server_port, reload=True)
    else:
        uvicorn.run("server:app", host="0.0.0.0", port=server_port)
