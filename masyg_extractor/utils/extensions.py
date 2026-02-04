import os
import socketio
import redis
import logging

ENV = os.getenv("FAST_API_ENV", "development").lower()

# Build a precise allowlist of browser origins (scheme + host + port)
def _allowed_origins():
    # Always include common local dev origins
    allow = []

    # Include CLIENT_URL if set and looks like a full origin
    client = (os.getenv("CLIENT_URL") or "").strip()
    if client.startswith("http://") or client.startswith("https://"):
        allow.append(client)

    # Optionally allow more (comma-separated) via CORS_EXTRA
    extra = [o.strip() for o in (os.getenv("CORS_EXTRA") or "").split(",") if o.strip()]
    allow.extend(extra)

    # De-dup while preserving order
    out, seen = [], set()
    for o in allow:
        if o and o not in seen:
            out.append(o); seen.add(o)
    return out

ALLOWED_ORIGINS = _allowed_origins() if ENV != "production" else _allowed_origins()



# Optional: crank up socketio/engineio logging to see rejected origin values
logging.getLogger("socketio").setLevel(logging.DEBUG)
logging.getLogger("engineio").setLevel(logging.DEBUG)

# Redis (keep your existing logic)
if ENV == "production":
    redis_url = os.getenv('REDIS_URL', 'redis://127.0.0.1:6379')
    print(f"[INFO] Production environment detected. Connecting to Redis at {redis_url}.")
    try:
        redis_conn = redis.from_url(redis_url)
        redis_conn.ping()
        print("[SUCCESS] Connected to Redis successfully.")
    except redis.ConnectionError as e:
        print(f"[ERROR] Redis connection failed: {e}")
else:
    print("[INFO] Development environment detected. Redis is disabled.")

# IMPORTANT: these are in SECONDS (not ms)
sio = socketio.AsyncServer(
    async_mode="asgi",
    cors_allowed_origins=ALLOWED_ORIGINS,  # explicit list fixes 403s
    ping_timeout=60,
    ping_interval=25,
    logger=True,
    engineio_logger=True,
)
