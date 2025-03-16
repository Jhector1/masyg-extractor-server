import logging
import asyncio

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
            from services.global_executor import MAIN_LOOP
            asyncio.run_coroutine_threadsafe(
                self.sio.emit('log_message', {'data': log_entry}, namespace='/'),
                MAIN_LOOP
            )
