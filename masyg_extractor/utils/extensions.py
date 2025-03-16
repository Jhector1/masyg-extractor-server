#!/usr/bin/env python
import os
import redis
import socketio


# Load environment variables
ENV = os.getenv("FAST_API_ENV", "development").lower()

# Set allowed origins dynamically
ALLOWED_ORIGINS = os.getenv(
    "CLIENT_URL",
    "localhost"
) if ENV == "production" else "*"

if ENV == "production":
    redis_url = os.getenv('REDIS_URL', 'redis://127.0.0.1:6379')
    print(f"[INFO] Production environment detected. Connecting to Redis at {redis_url}.")

    try:
        redis_conn = redis.from_url(redis_url)
        redis_conn.ping()  # Test the Redis connection
        print("[SUCCESS] Connected to Redis successfully.")

        sio = socketio.AsyncServer(
            cors_allowed_origins=ALLOWED_ORIGINS,
            async_mode="asgi",
            ping_timeout=60000,
            ping_interval=30000,
            logger=True,
            engineio_logger=True
        )
    except redis.ConnectionError as e:
        print(f"[ERROR] Redis connection failed: {e}")
else:
    # Development mode: No Redis
    print("[INFO] Development environment detected. Redis is disabled.")

    sio = socketio.AsyncServer(
        async_mode="asgi",
        cors_allowed_origins="*",
        ping_timeout=60000,
        ping_interval=30000,
        logger=True,
        engineio_logger=True
    )
