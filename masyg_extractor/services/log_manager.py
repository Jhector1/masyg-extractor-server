import asyncio

from masyg_extractor.services.my_log import logger
from masyg_extractor.utils.extensions import sio


class LogManager:
    def __init__(self, ):
        """
        Initialize the LogManager with a Socket.IO server instance and a logger.
        """
        self.log_queue = []
        self.log_lock = asyncio.Lock()


    async def send_log(self, message: str, log_key="log_message", user_room=None):
        """
        Queues a log message for asynchronous emission over Socket.IO.
        Also appends the message to an in-memory log queue.
        """
        async with self.log_lock:
            self.log_queue.append((message, user_room))
        await sio.emit(log_key, {'data': message}, namespace='/', room=user_room)
        # Socket.IO owns the user-facing message. Keep local
        # observability metadata-only so customer/file content is not
        # echoed into application logs.
        logger.debug("Queued Socket.IO log event")

    async def clear_queue(self):
        """
        Clears the in-memory log queue.
        """
        async with self.log_lock:
            self.log_queue.clear()
        logger.info("Log queue cleared.")