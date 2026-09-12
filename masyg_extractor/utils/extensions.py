import os
import socketio
import redis
import logging

ENV = os.getenv("FAST_API_ENV", "development").lower()

from masyg_extractor.config.origins import ALLOWED_ORIGINS


# Optional: crank up socketio/engineio logging to see rejected origin values
SOCKET_DEBUG = (os.getenv("SOCKET_DEBUG", "0").strip().lower() in {"1", "true", "yes", "on"})
logging.getLogger("socketio").setLevel(logging.DEBUG if SOCKET_DEBUG else logging.WARNING)
logging.getLogger("engineio").setLevel(logging.DEBUG if SOCKET_DEBUG else logging.WARNING)

socket_manager = None
if ENV == "production":
    redis_url = os.getenv("REDIS_URL", "redis://127.0.0.1:6379")
    try:
        redis_conn = redis.from_url(
            redis_url,
            socket_connect_timeout=1,
            socket_timeout=1,
        )
        redis_conn.ping()
        # Share Socket.IO rooms/events across workers or containers.
        socket_manager = socketio.AsyncRedisManager(redis_url)
        logging.getLogger(__name__).info("Redis-backed Socket.IO manager enabled")
    except redis.RedisError as exc:
        # Keep one-instance deployments available, but make degraded scaling explicit.
        logging.getLogger(__name__).error(
            "Redis unavailable; Socket.IO is process-local error_type=%s", type(exc).__name__
        )
else:
    logging.getLogger(__name__).info("Development environment: Redis Socket.IO manager disabled")

sio = socketio.AsyncServer(
    async_mode="asgi",
    client_manager=socket_manager,
    cors_allowed_origins=ALLOWED_ORIGINS,
    ping_timeout=60,
    ping_interval=25,
    logger=SOCKET_DEBUG,
    engineio_logger=SOCKET_DEBUG,
)
