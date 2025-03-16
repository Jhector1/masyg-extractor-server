import asyncio
import logging
from collections import deque
from threading import Lock
from masyg_extractor.utils.extensions import sio

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
if not logger.hasHandlers():
    handler = logging.StreamHandler()
    formatter = logging.Formatter('%(levelname)s:%(name)s: %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)

log_queue = deque()
queue_lock = Lock()

async def log_consumer():
    """Async background task to process queued messages and emit them via Socket.IO."""
    while True:
        await asyncio.sleep(0.1)
        items = []
        with queue_lock:
            while log_queue:
                items.append(log_queue.popleft())

        for msg, user_room in items:
            try:
                await sio.emit('log_message', {'data': msg}, namespace='/', room=user_room)
            except Exception as e:
                logger.warning(f"Failed to emit log message: {e}")

async def send_log(message: str, user_room=None):
    """Queues a log message for async emission."""
    # with queue_lock:
    #     log_queue.append((message, user_room))
    await sio.emit('log_message', {'data': message}, namespace='/', room=user_room)
    # Optional local log
    logger.info(f"Queued log: {message} (room={user_room})")
