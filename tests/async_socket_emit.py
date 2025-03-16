import asyncio
import logging
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from tool.socket_io_handler import AsyncSocketIOHandler

class TestAsyncSocketIOHandler(unittest.TestCase):
    def setUp(self):
        # Use AsyncMock so that sio.emit returns a coroutine.
        self.fake_sio = AsyncMock()
        self.handler = AsyncSocketIOHandler(self.fake_sio)
        formatter = logging.Formatter('%(levelname)s:%(message)s')
        self.handler.setFormatter(formatter)
        # Prepare a test LogRecord.
        self.record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="dummy_path",
            lineno=10,
            msg="Test message",
            args=(),
            exc_info=None
        )

    @patch("asyncio.get_running_loop", side_effect=RuntimeError("No running loop"))
    @patch("asyncio.run_coroutine_threadsafe")
    def test_emit_with_no_running_loop(self, mock_run_coroutine_threadsafe, mock_get_running_loop):
        """
        When get_running_loop() raises RuntimeError, the handler should fall back to using
        asyncio.run_coroutine_threadsafe to schedule sio.emit.
        """
        self.handler.emit(self.record)
        mock_run_coroutine_threadsafe.assert_called_once()
        # Retrieve the coroutine passed to run_coroutine_threadsafe.
        coro = mock_run_coroutine_threadsafe.call_args[0][0]
        # Now we expect that the value is an actual coroutine.
        self.assertTrue(asyncio.iscoroutine(coro))

    def test_emit_with_running_loop(self):
        """
        When a running loop is available, the handler should use create_task.
        """
        fake_loop = MagicMock()
        # Patch get_running_loop to return our fake_loop.
        with patch("asyncio.get_running_loop", return_value=fake_loop):
            self.handler.emit(self.record)
            fake_loop.create_task.assert_called_once()
            # Retrieve the coroutine passed to create_task.
            coro = fake_loop.create_task.call_args[0][0]
            self.assertTrue(asyncio.iscoroutine(coro))
            # Now simulate running the coroutine in a real event loop.
            loop = asyncio.new_event_loop()
            try:
                # Make sio.emit return a simple coroutine.
                self.fake_sio.emit.return_value = asyncio.sleep(0, result="done")
                result = loop.run_until_complete(coro)
                # Assert that sio.emit was called with the expected parameters.
                self.fake_sio.emit.assert_called_once_with(
                    'log_message',
                    {'data': "INFO:Test message"},
                    namespace='/'
                )
            finally:
                loop.close()

if __name__ == "__main__":
    unittest.main()
