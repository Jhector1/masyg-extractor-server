import io
import base64
import pytest
from fastapi.testclient import TestClient
from PIL import Image

# Import your app and override dependencies as needed.
# Adjust the import based on your project structure.
from masyg_extractor.server import app  # or "from masyg_extractor.server import app" if that's your entry point
from masyg_extractor.routes.data_extractor_routes import get_firebase_user


# Override get_firebase_user for testing
def override_get_firebase_user(request):
    return {"userId": "test_user"}


app.dependency_overrides[get_firebase_user] = override_get_firebase_user


def create_test_image():
    """Create an in-memory test image (red 100x100 PNG)."""
    image = Image.new("RGB", (100, 100), color="red")
    byte_arr = io.BytesIO()
    image.save(byte_arr, format="PNG")
    byte_arr.seek(0)
    return byte_arr


client = TestClient(app)


def test_extract_data_with_image():
    # Create a test image
    test_image = create_test_image()

    # Prepare the file tuple as expected by TestClient:
    # ("field name", (filename, file object, content type))
    files = [("files", ("test_image.png", test_image, "image/png"))]

    # Send a POST request to the endpoint.
    response = client.post("/api/extractor/extract-data", files=files)

    # Assert that the response status code is 201 Created.
    assert response.status_code == 201, response.text

    data = response.json()
    # Check that the response contains the expected keys.
    assert "group_id" in data
    assert "files" in data
    assert "upload_time" in data
    assert "file_count" in data
    # Since we uploaded one file, file_count should be 1.
    assert data["file_count"] == 1

    # Check the processed file result.
    # Files are keyed by indices (e.g., "0")
    file_result = data["files"].get("0")
    assert file_result is not None, "No result for file index 0"
    assert "sanitized_filename" in file_result

    # Check that compressed content is base64 encoded and non-empty.
    encoded_content = file_result.get("content")
    assert encoded_content and len(encoded_content) > 0, "Compressed content is empty"

    # Decode the base64 string and verify it starts with JPEG header bytes (0xFF 0xD8).
    decoded = base64.b64decode(encoded_content)
    assert decoded.startswith(b'\xff\xd8'), "Compressed file is not a valid JPEG image"


if __name__ == "__main__":
    pytest.main(["-v", __file__])
