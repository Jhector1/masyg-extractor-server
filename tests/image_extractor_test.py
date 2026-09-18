import io
import base64
import pytest
from test_support.sync_asgi_client import SyncASGIClient
from PIL import Image

# Import your app and override dependencies as needed.
# Adjust the import based on your project structure.
from server import app  # or "from masyg_extractor.server import app" if that's your entry point
from masyg_extractor.config.jwt_config import get_current_user_from_cookie
import masyg_extractor.routes.data_extractor_routes as data_extractor_routes
from masyg_extractor.services import document_ingestion
from masyg_extractor.services.subscription_access import require_active_subscription


# Override the current cookie-auth dependency for testing
def override_get_current_user_from_cookie():
    return {"userId": "test_user"}


app.dependency_overrides[get_current_user_from_cookie] = override_get_current_user_from_cookie

def override_require_active_subscription():
    return {"userId": "test_user"}


app.dependency_overrides[
    require_active_subscription
] = override_require_active_subscription


def create_test_image():
    """Create an in-memory test image (red 100x100 PNG)."""
    image = Image.new("RGB", (100, 100), color="red")
    byte_arr = io.BytesIO()
    image.save(byte_arr, format="PNG")
    byte_arr.seek(0)
    return byte_arr


client = SyncASGIClient(app)


def test_extract_data_with_image(monkeypatch):
    class FakeFirestoreReference:
        def collection(self, _name):
            return self

        def document(self, _doc_id):
            return self

    async def fake_process_files_in_parallel(**_kwargs):
        return {
            0: {
                "sanitized_filename": "test_image.png",
                "parsed_content": {
                    "vendor_name": "Test Vendor",
                    "line_items": [],
                },
            }
        }

    async def fake_document_set(*_args, **_kwargs):
        return None

    async def fake_emit(*_args, **_kwargs):
        return None

    async def fake_send_log(*_args, **_kwargs):
        return None

    def fake_compress_file_blob(*_args, **_kwargs):
        return io.BytesIO(b"\xff\xd8test-jpeg")

    monkeypatch.setattr(
        document_ingestion,
        "process_files_in_parallel",
        fake_process_files_in_parallel,
    )
    monkeypatch.setattr(
        document_ingestion,
        "generate_group_id",
        lambda: "test_group",
    )
    monkeypatch.setattr(
        document_ingestion.firestore,
        "client",
        lambda: FakeFirestoreReference(),
    )
    monkeypatch.setattr(
        document_ingestion,
        "document_set",
        fake_document_set,
    )
    monkeypatch.setattr(
        document_ingestion,
        "compress_file_blob",
        fake_compress_file_blob,
    )
    monkeypatch.setattr(
        document_ingestion.sio,
        "emit",
        fake_emit,
    )
    monkeypatch.setattr(
        document_ingestion,
        "send_log",
        fake_send_log,
    )

    test_image = create_test_image()
    files = [
        ("files", ("test_image.png", test_image, "image/png"))
    ]

    response = client.post(
        "/api/extractor/extract-data",
        files=files,
    )

    assert response.status_code == 201, response.text

    data = response.json()
    assert data["group_id"] == "test_group"
    assert data["test_image.png"] == {
        "vendor_name": "Test Vendor",
        "line_items": [],
    }

    metadata = data["metadata"]
    assert metadata["file_count"] == 1
    assert metadata["group_name"] == "test_group"
    assert metadata["isViewed"] is False

    assert len(metadata["files"]) == 1
    preview = metadata["files"][0]
    assert preview["filename"] == "test_image.png"
    decoded = base64.b64decode(preview["content"])
    assert decoded.startswith(b"\xff\xd8")


if __name__ == "__main__":
    pytest.main(["-v", __file__])
