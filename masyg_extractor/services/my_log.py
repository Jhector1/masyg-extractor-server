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

async def send_log(message: str, log_key = "log_message",user_room=None):
    """Queues a log message for async emission."""

    # with queue_lock:
    #     log_queue.append((message, user_room))
    await sio.emit(log_key, {'data': message}, namespace='/', room=user_room)

    # Optional local log
    logger.info(f"Queued log: {message} (room={user_room})")


class ClientFilter(logging.Filter):
    def __init__(self, client_room):
        super().__init__()
        self.client_room = client_room

    def filter(self, record):
        # Check if a target room is specified in the log record.
        # If not specified, or if it matches this handler's room, allow the record.
        target = getattr(record, 'target_room', None)
        return target is None or target == self.client_room

class SocketIOHandler(logging.Handler):
    def __init__(self, sio, room):
        super().__init__()
        self.sio = sio
        self.room = room
        # Add our client-specific filter.
        self.addFilter(ClientFilter(room))

    def emit(self, record):
        # If the filters do not pass, skip emitting.
        if not self.filter(record):
            return
        try:
            log_entry = self.format(record)
            # Asynchronously emit the log message to the specific room.
            asyncio.create_task(
                self.sio.emit("log_message", {"data": log_entry}, room=self.room)
            )
        except Exception:
            self.handleError(record)

# logging_queue.py

from collections import deque

# Global queue for log messages: each entry is a tuple (message, room)


async def log_processor(sio, total_steps=10):
    """Background task that processes the log queue and sends progress updates."""
    current_step = 0
    while True:
        # Process and emit all queued log messages.
        while log_queue:
            message, room = log_queue.popleft()
            await sio.emit('log_message', {'data': message}, namespace='/', room=room)
            current_step += 1  # increment if each log represents a step
            progress = min(100, int((current_step / total_steps) * 100))
            await sio.emit('progress_update', {'progress': progress}, namespace='/', room=room)
        await asyncio.sleep(0.1)

# Usage: start the log processor with a total step count (e.g., 10)
# asyncio.create_task(log_processor(sio, total_steps=10))

def queue_log(message: str, room: str):
    """Enqueue a log message to be sent to the given room."""
    log_queue.append((message, room))
