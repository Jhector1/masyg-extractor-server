
# !/usr/bin/env python
"""
Run the FastAPI app with python-socketio’s native AsyncServer in ASGI mode.

Run with an ASGI server such as uvicorn:
    uvicorn server:app --host 0.0.0.0 --port 5000
"""

import os
from pathlib import Path
from datetime import datetime, timedelta

from dotenv import load_dotenv
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

# Load local development configuration deterministically before importing modules
# that read environment variables at import time. Runtime environment variables
# still win because override=False. Production should inject secrets at runtime.
_LOCAL_ENV = Path(__file__).resolve().parent / "masyg_extractor" / ".env"
if _LOCAL_ENV.exists():
    load_dotenv(_LOCAL_ENV, override=False)
else:
    load_dotenv(override=False)

ENV = os.getenv("FAST_API_ENV", "development").lower()

def _validate_runtime_config() -> None:
    if ENV != "production":
        return
    required = ["SECRET_KEY", "ALGORITHM", "CLIENT_URL", "MASYG_EXTRACTOR_STRIPE_SECRET_KEY"]
    missing = [name for name in required if not (os.getenv(name) or "").strip()]
    if missing:
        raise RuntimeError(f"Missing required production configuration: {', '.join(missing)}")
    if os.getenv("SECRET_KEY") in {"BAD_SECRET_KEY", "fallback-secret-key"}:
        raise RuntimeError("Refusing to start production with an insecure SECRET_KEY")

_validate_runtime_config()

# Initialize Firebase only after configuration has been loaded and validated.
from masyg_extractor.firebase.firebase_init import firebase_init
from masyg_extractor.services.maintenance import purge_expired_trash, roll_failed_to_trash, expire_free_trials

firebase_init()
from firebase_admin import firestore
from starlette.middleware.base import BaseHTTPMiddleware

from masyg_extractor.services.subscription_services import _recompute_is_subscribed

import logging
import uuid
import logging

from masyg_extractor.utils.access_log_redaction import (
    install_sensitive_oauth_access_log_filter,
)

install_sensitive_oauth_access_log_filter()
# if ENV != "development":
#     logging.getLogger("uvicorn").setLevel(logging.WARNING)
#     logging.getLogger("uvicorn.error").setLevel(logging.WARNING)
#     logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
#     logging.getLogger("socketio.server").setLevel(logging.WARNING)
#     logging.getLogger("engineio.server").setLevel(logging.WARNING)
#     # For HTTP client libraries (like httpx):
#     logging.getLogger("httpx").setLevel(logging.WARNING)

import stripe
import asyncio

from starlette.responses import JSONResponse
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from masyg_extractor.services.helper import init_mail
from masyg_extractor.services.cache import analytics_cache
from masyg_extractor.services.my_log import SocketIOHandler, logger, log_processor
from masyg_extractor.utils.extensions import sio
from masyg_extractor.config.origins import ALLOWED_ORIGINS
from masyg_extractor.services.socket_connections import (
    SocketIdentityError,
    resolve_session_client_id,
    socket_connections,
)


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

ENV = os.getenv("FAST_API_ENV", "development").lower()
SECRET_KEY = os.getenv("SECRET_KEY", "development-only-secret")
CLIENT_URL = os.getenv("CLIENT_URL", "http://localhost:4000")

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

@inner.get("/health", include_in_schema=False)
async def health():
    return {"status": "ok"}

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
  allow_origins=ALLOWED_ORIGINS,
  allow_credentials=True,
  allow_methods=["*"],
  allow_headers=["*"],
)
logging.getLogger("masyg.cors").info("Allowed browser origins=%s", ALLOWED_ORIGINS)
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
from masyg_extractor.integrations.accounting.shared.operation_progress import emit_accounting_operation_snapshot
from masyg_extractor.integrations.bank.webhook_processor import (
    process_pending_bank_webhooks,
)
from masyg_extractor.integrations.bank.webhook_repository import (
    BankWebhookRepository,
)
from masyg_extractor.integrations.document_sources.gmail.service import (
    renew_gmail_watches,
)





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

    # Repair reverse item ownership for Items connected before Plaid webhook
    # routing existed. This runs only on the designated scheduler instance.
    owner_backfill = await asyncio.to_thread(
        BankWebhookRepository().backfill_item_owners
    )
    logger.info(
        "Plaid bank item owner backfill scanned=%s registered=%s",
        owner_backfill["scanned"],
        owner_backfill["registered"],
    )

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

    scheduler.add_job(
        process_pending_bank_webhooks,
        trigger=IntervalTrigger(seconds=30),
        id="bank_webhook_inbox",
        replace_existing=True,
        coalesce=True,
        misfire_grace_time=30,
        max_instances=1,
    )

    scheduler.add_job(
        renew_gmail_watches,
        trigger=CronTrigger(
            hour=2,
            minute=15,
        ),
        id="gmail_watch_renewal_daily",
        replace_existing=True,
        coalesce=True,
        misfire_grace_time=3600,
        max_instances=1,
    )

    scheduler.start()
    inner.state.scheduler = scheduler


@inner.on_event("shutdown")
async def _shutdown_runtime():
    scheduler = getattr(inner.state, "scheduler", None)
    if scheduler and scheduler.running:
        scheduler.shutdown(wait=False)
    await analytics_cache.close()


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
  scope = environ.get("asgi.scope", {})
  try:
    # The signed Starlette session is the authority for room ownership. The
    # query/auth clientId may confirm it, but can never choose another room.
    client_id = resolve_session_client_id(scope, auth)
  except SocketIdentityError as exc:
    logging.getLogger("masyg.socket").warning(
        "Socket rejected sid=%s reason=%s", sid, str(exc)
    )
    return False

  previous_sid = await socket_connections.claim(client_id, sid)
  try:
    await sio.enter_room(sid, client_id)
    # Send the welcome only to the newly-connected SID so a reconnect cannot
    # duplicate it to the stale connection that is about to be replaced.
    await sio.emit("welcome", {"message": f"Welcome, {client_id}!"}, to=sid)

    if previous_sid:
      logging.getLogger("masyg.socket").info(
          "Replacing duplicate socket client_id=%s old_sid=%s new_sid=%s",
          client_id, previous_sid, sid
      )
      try:
        await sio.disconnect(previous_sid)
      except Exception as exc:
        logging.getLogger("masyg.socket").warning(
            "Failed to disconnect stale socket sid=%s error_type=%s",
            previous_sid, type(exc).__name__
        )
  except Exception:
    await socket_connections.release(sid)
    raise

  # make loop available elsewhere if you rely on it
  from masyg_extractor.services import global_executor
  global_executor.MAIN_LOOP = asyncio.get_running_loop()

@sio.event
async def disconnect(sid):
  client_id = await socket_connections.release(sid)
  logging.getLogger("masyg.socket").debug(
      "Client disconnected sid=%s client_id=%s", sid, client_id
  )


@sio.on("progress_request_snapshot")
async def progress_request_snapshot(sid, _payload=None):
  client_id = await socket_connections.client_id_for_sid(sid)
  if not client_id:
    return
  await emit_accounting_operation_snapshot(
      client_id,
      to_sid=sid,
  )

# ──────────────────────────────────────────────────────────────────────────────
# Export ONE ASGI app: Socket.IO wrapped around FastAPI
# ──────────────────────────────────────────────────────────────────────────────
# app = __import__("socketio").ASGIApp(sio, other_asgi_app=inner)
_socketio_app = __import__("socketio").ASGIApp(sio, socketio_path="socket.io")
inner.mount("/ws", _socketio_app)
app = inner

if __name__ == "__main__":
  import uvicorn

  # Direct `python server.py` favors a single stable process. Opt into reload with
  # UVICORN_RELOAD=1, or use `uvicorn server:app --reload` explicitly. Running the
  # reloader from an already-imported server module initializes Firebase/mail in
  # the parent and worker processes, which creates misleading duplicate startup logs.
  reload_enabled = (
      ENV == "development"
      and (os.getenv("UVICORN_RELOAD") or "").strip().lower() in {"1", "true", "yes", "on"}
  )
  # With reload disabled, pass the already-created ASGI app object. Using the
  # string "server:app" here would import this file a second time after it has
  # already executed as __main__, duplicating Firebase/mail initialization.
  target = "server:app" if reload_enabled else app
  uvicorn.run(
      target,
      host="0.0.0.0",
      port=int(os.getenv("SERVER_PORT", 5000)),
      reload=reload_enabled,
  )
