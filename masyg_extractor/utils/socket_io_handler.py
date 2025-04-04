import logging
import asyncio

from masyg_extractor.services.global_executor import MAIN_LOOP


class AsyncSocketIOHandler(logging.Handler):
    def __init__(self, sio_instance):
        super().__init__()
        self.sio = sio_instance

    def emit(self, record):
        log_entry = self.format(record)
        try:
            asyncio.get_running_loop().create_task(
                self.sio.emit('log_message', {'data': log_entry}, namespace='/')
            )
        except RuntimeError:

            asyncio.run_coroutine_threadsafe(
                self.sio.emit('log_message', {'data': log_entry}, namespace='/'),
                MAIN_LOOP
            )
