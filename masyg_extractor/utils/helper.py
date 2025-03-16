
import io

import io

class FakeUploadFile:
    def __init__(self, filename, file_bytes, content_type):
        self.filename = filename
        self._stream = io.BytesIO(file_bytes)
        self.mimetype = content_type  # Ensure you have a 'mimetype' attribute

    @property
    def stream(self):
        return self._stream

    def read(self, size=-1):
        return self._stream.read(size)

    def seek(self, offset, whence=0):
        return self._stream.seek(offset, whence)

    def save(self, destination):
        with open(destination, "wb") as f:
            f.write(self._stream.getvalue())

