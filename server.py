
# !/usr/bin/env python
"""
Run the FastAPI app with python-socketio’s native AsyncServer in ASGI mode.

Run with an ASGI server such as uvicorn:
    uvicorn server:app --host 0.0.0.0 --port 5000
"""

import os
from datetime import datetime, timedelta

from apscheduler.triggers.cron import CronTrigger

# Initialize Firebase early.
from masyg_extractor.firebase.firebase_init import firebase_init
from masyg_extractor.services.maintenance import purge_expired_trash, roll_failed_to_trash, expire_free_trials

firebase_init()
from firebase_admin import firestore
from starlette.middleware.base import BaseHTTPMiddleware

from masyg_extractor.services.subscription_services import _recompute_is_subscribed

ENV = os.getenv("FAST_API_ENV", "development").lower()
import logging
import uuid
import logging
# if ENV != "development":
#     logging.getLogger("uvicorn").setLevel(logging.WARNING)
#     logging.getLogger("uvicorn.error").setLevel(logging.WARNING)
#     logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
#     logging.getLogger("socketio.server").setLevel(logging.WARNING)
#     logging.getLogger("engineio.server").setLevel(logging.WARNING)
#     # For HTTP client libraries (like httpx):
#     logging.getLogger("httpx").setLevel(logging.WARNING)
print(os.getenv("SERVER_URL"))
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
import psutil
def log_mem(step):
    proc = psutil.Process(os.getpid())
    print(f"MEMORY @ {step}: {(proc.memory_info().rss/1e6):.1f} MB")

# after each init
log_mem("firebase init")


# server.py
#!/usr/bin/env python
import os, asyncio, uuid, urllib.parse, logging
from datetime import datetime, timedelta
from typing import Optional

from dotenv import load_dotenv, find_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import JSONResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from firebase_admin import firestore
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# 🔌 use the SAME sio instance everywhere
from masyg_extractor.utils.extensions import sio
from masyg_extractor.routes import register_routers
from masyg_extractor.services.helper import init_mail

load_dotenv(find_dotenv())

ENV = os.getenv("FAST_API_ENV", "development").lower()
SECRET_KEY = os.getenv("SECRET_KEY", "BAD_SECRET_KEY")
CLIENT_URL = os.getenv("CLIENT_URL", "http://localhost:3000")

# ──────────────────────────────────────────────────────────────────────────────
# Cookie defaults (keeps domain/samesite consistent in prod)
# ──────────────────────────────────────────────────────────────────────────────
class DefaultCookieMiddleware(BaseHTTPMiddleware):
  def __init__(self, app: FastAPI, default_domain: str, default_samesite: str = "strict",
               default_secure: bool = True, default_httponly: bool = True, default_max_age: Optional[int] = None):
    super().__init__(app)
    self.default_domain = default_domain
    self.default_samesite = default_samesite
    self.default_secure = default_secure
    self.default_httponly = default_httponly
    self.default_max_age = default_max_age

  async def dispatch(self, request: Request, call_next):
    resp: Response = await call_next(request)
    cookies = resp.headers.getlist("set-cookie")
    if cookies:
      new_ = []
      for c in cookies:
        httponly = False if "csrf_token=" in c else self.default_httponly
        if "Domain=" not in c: c += f"; Domain={self.default_domain}"
        if "SameSite=" not in c: c += f"; SameSite={self.default_samesite}"
        if self.default_secure and "Secure" not in c: c += "; Secure"
        if httponly and "HttpOnly" not in c: c += "; HttpOnly"
        if self.default_max_age is not None and "Max-Age=" not in c: c += f"; Max-Age={self.default_max_age}"
        new_.append(c)
      resp.headers.__delitem__("set-cookie")
      for c in new_: resp.headers.append("set-cookie", c)
    return resp

# ──────────────────────────────────────────────────────────────────────────────
# FastAPI core app
# ──────────────────────────────────────────────────────────────────────────────
inner = FastAPI()
init_mail(inner)

if ENV == "production":
  inner.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, same_site="none", https_only=True)
  inner.add_middleware(DefaultCookieMiddleware,
                       default_domain=".masyglink.com",
                       default_samesite="none",
                       default_secure=True,
                       default_httponly=True,
                       default_max_age=1800)
  inner.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")
else:
  inner.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)

inner.add_middleware(
  CORSMiddleware,
  allow_origins=[CLIENT_URL],
  allow_credentials=True,
  allow_methods=["*"],
  allow_headers=["*"],
)
stripe.set_app_info(
    'stripe-samples/checkout-single-subscription',
    version='0.0.1',
    url='https://github.com/stripe-samples/checkout-single-subscription'
)
stripe.api_key = os.getenv('MASYG_EXTRACTOR_STRIPE_SECRET_KEY')
# Register all HTTP routes (including /extractor/* that emit progress)
register_routers(inner)

# Firestore + daily job (unchanged)

# def expire_free_trials():
#   cutoff = datetime.utcnow() - timedelta(minutes=2)
#   for user_snap in db.collection("users").stream():
#     uid = user_snap.id
#     trial_ref = db.collection("users").document(uid).collection("plan").document("trial")
#     snap = trial_ref.get()
#     if not snap.exists: continue
#     trial = snap.to_dict()
#     if trial.get("hasUsed") and trial.get("date") <= cutoff:
#       db.collection("users").document(uid).update({"isSubscribed": False})
#   print("✅ Expired any >30-day trials.")
from datetime import timezone

from datetime import timezone, timedelta





# @inner.on_event("startup")
# def _startup():
#   sch = AsyncIOScheduler(timezone="America/Chicago")
#   from datetime import datetime as dt, timezone as tz
#
#   # sch.add_job(expire_free_trials, "date", run_date=dt.now(tz.utc), id="trial_expire_boot", replace_existing=True)
#
#   sch.add_job(expire_free_trials, "cron", hour=0, minute=0)
#   sch.start()
RUN_SCHEDULER = os.getenv("IS_SCHEDULER", "0") == "1"  # only one instance should schedule

def _wrap_async(coro_func):
  # APScheduler 3.x runs callables; we wrap to schedule the coroutine on the event loop.
  def runner():
    asyncio.get_event_loop().create_task(coro_func())

  return runner


# RUN_SCHEDULER = os.getenv("IS_SCHEDULER", "0") == "1"  # only one instance should schedule

@inner.on_event("startup")
async def _startup():
    if not RUN_SCHEDULER:
        return

    # attach scheduler to the current event loop
    scheduler = AsyncIOScheduler(timezone="America/Chicago")

    # if expire_free_trials / roll_failed_to_trash / purge_expired_trash are async,
    # AsyncIOScheduler will await them properly
    scheduler.add_job(
        expire_free_trials,
        trigger=CronTrigger(hour=0, minute=0),
        id="trial_expire_daily",
        replace_existing=True,
        coalesce=True,
        misfire_grace_time=3600,
        max_instances=1,
    )

    scheduler.add_job(
        roll_failed_to_trash,
        trigger=CronTrigger(hour=3, minute=0),
        id="roll_failed_to_trash",
        replace_existing=True,
        coalesce=True,
        misfire_grace_time=3600,
        max_instances=1,
    )

    scheduler.add_job(
        purge_expired_trash,
        trigger=CronTrigger(hour=4, minute=0),
        id="purge_expired_trash",
        replace_existing=True,
        coalesce=True,
        misfire_grace_time=3600,
        max_instances=1,
    )

    scheduler.start()


# @inner.on_event("startup")
# def _startup():
#   if not RUN_SCHEDULER:
#     return
#
#   sch = AsyncIOScheduler(timezone="America/Chicago")
#
#   # example existing job
#   sch.add_job(_wrap_async(expire_free_trials), trigger=CronTrigger(hour=0, minute=0),
#               id="trial_expire_daily", replace_existing=True, coalesce=True,
#               misfire_grace_time=3600, max_instances=1)
#
#   # 03:00 CT – move failed → trash
#   sch.add_job(
#     _wrap_async(roll_failed_to_trash),
#     trigger=CronTrigger(hour=3, minute=0),
#     id="roll_failed_to_trash",
#     replace_existing=True,
#     coalesce=True,
#     misfire_grace_time=3600,
#     max_instances=1,
#   )
#
#   # 04:00 CT – purge expired trash
#   sch.add_job(
#     _wrap_async(purge_expired_trash),
#     trigger=CronTrigger(hour=4, minute=0),
#     id="purge_expired_trash",
#     replace_existing=True,
#     coalesce=True,
#     misfire_grace_time=3600,
#     max_instances=1,
#   )
#
#   sch.start()

# ──────────────────────────────────────────────────────────────────────────────
# Session client-id endpoint (used by SocketProvider before connecting)
# ──────────────────────────────────────────────────────────────────────────────
@inner.post("/client-id")
async def get_client_id(request: Request):
  cid = request.session.get("client_id")
  if not cid:
    cid = str(uuid.uuid4())
    request.session["client_id"] = cid
  return JSONResponse({"clientId": cid})

# ──────────────────────────────────────────────────────────────────────────────
# Socket.IO events (ONE connect handler only)
# ──────────────────────────────────────────────────────────────────────────────
@sio.event
async def connect(sid, environ, auth):
  # Prefer the query param ?clientId= set by your SocketProvider
  qs = environ.get("asgi.scope", {}).get("query_string", b"").decode()
  params = urllib.parse.parse_qs(qs)
  client_id = (auth or {}).get("client_id") or params.get("clientId", ["Guest"])[0]

  await sio.enter_room(sid, client_id)
  await sio.emit("welcome", {"message": f"Welcome, {client_id}!"}, room=client_id)

  # make loop available elsewhere if you rely on it
  from masyg_extractor.services import global_executor
  global_executor.MAIN_LOOP = asyncio.get_running_loop()

@sio.event
async def disconnect(sid):
  print(f"Client disconnected: {sid}")

# ──────────────────────────────────────────────────────────────────────────────
# Export ONE ASGI app: Socket.IO wrapped around FastAPI
# ──────────────────────────────────────────────────────────────────────────────
# app = __import__("socketio").ASGIApp(sio, other_asgi_app=inner)
_socketio_app = __import__("socketio").ASGIApp(sio, socketio_path="socket.io")
inner.mount("/ws", _socketio_app)
app = inner

if __name__ == "__main__":
  import uvicorn
  uvicorn.run("server:app", host="0.0.0.0", port=int(os.getenv("SERVER_PORT", 5000)), reload=(ENV=="development"))
