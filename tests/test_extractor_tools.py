import io
import json
import os
import subprocess
import pytest
from PIL import Image
import pytesseract
import spacy

# Import the functions to test.
# Replace the module paths with your actual project structure.
from masyg_extractor.services.file_extractor_service import (
    process_text_with_gpt,
    process_text_with_regex,
    # process_text_with_nlp,
    extract_json_from_code_block
)
from masyg_extractor.services.image_extractor_service import (
    extract_text_from_image,
    compress_image,
    compress_pdf,
    compress_file_blob,
)

# --- Helpers for tests ---

def create_test_image(text: str = "Hello"):
    """
    Create an in-memory PNG image with some text drawn on it.
    """
    from PIL import ImageDraw
    # Create a white image
    image = Image.new("RGB", (200, 100), color="white")
    draw = ImageDraw.Draw(image)
    # Use a default font (Pillow uses a basic font if none is specified)
    draw.text((10, 40), text, fill="black")
    img_io = io.BytesIO()
    image.save(img_io, format="PNG")
    img_io.seek(0)
    # Simulate an object with a filename attribute.
    img_io.filename = "test_image.png"
    return img_io

def get_real_test_image():
    """
    Load a real image file from disk.
    Ensure that the file exists at the specified path.
    """
    file_path = "/Users/admin/PycharmProjects/MasygExtractorFastAPI/tests/data/real_test_image.png"  # Adjust the path as needed.
    if not os.path.exists(file_path):
        pytest.skip(f"Real test image file not found at {file_path}")
    with open(file_path, "rb") as f:
        image = Image.open(f)
        image.verify()  # Validate that it's a proper image.
        f.seek(0)
        data = io.BytesIO(f.read())
        data.filename = os.path.basename(file_path)
        return data

def create_test_pdf():
    """
    Create a minimal PDF in memory.
    For testing, we'll write a simple PDF file to a temporary file.
    """
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import letter

    pdf_io = io.BytesIO()
    c = canvas.Canvas(pdf_io, pagesize=letter)
    c.drawString(100, 750, "This is a test PDF.")
    c.showPage()
    c.save()
    pdf_io.seek(0)
    # Simulate an object with a filename attribute.
    pdf_io.filename = "test_document.pdf"
    return pdf_io

# --- Tests for extraction functions ---

def test_extract_text_from_image_success(monkeypatch):
    # Use an in-memory test image with the text "Hello"
    test_image = create_test_image("Hello")

    # Optionally, monkeypatch pytesseract.image_to_string if OCR is not reliable in CI.
    def fake_image_to_string(image):
        return "Hello"
    monkeypatch.setattr(pytesseract, "image_to_string", fake_image_to_string)

    extracted = extract_text_from_image(test_image)
    assert "Hello" in extracted, f"Expected text 'Hello' in output, got: {extracted}"

def test_extract_text_from_real_image(monkeypatch):
    # Use a real image file from disk.
    test_image = get_real_test_image()
    # Monkeypatch pytesseract to return a known value (to keep tests deterministic)
    def fake_image_to_string(image):
        return "Real Image Text"
    monkeypatch.setattr(pytesseract, "image_to_string", fake_image_to_string)

    extracted = extract_text_from_image(test_image)
    assert "Real Image Text" in extracted, f"Expected 'Real Image Text', got: {extracted}"

def test_extract_text_from_image_failure():
    # Create an empty BytesIO (simulate an invalid image)
    empty_stream = io.BytesIO(b"")
    empty_stream.filename = "empty.png"
    extracted = extract_text_from_image(empty_stream)
    # Expect an empty string on failure
    assert extracted == "", "Expected empty output for invalid image."

# --- Tests for compression functions ---

def test_compress_image():
    # Create an in-memory test image
    test_image = create_test_image("Test")
    test_image.seek(0)
    compressed = compress_image(test_image, quality=30, max_size=(800, 800))
    # Check that the output is a BytesIO and that it starts with JPEG header (0xFF, 0xD8)
    assert isinstance(compressed, io.BytesIO)
    compressed.seek(0)
    data = compressed.read(2)
    assert data == b'\xff\xd8', "Compressed image is not a valid JPEG."

def test_compress_pdf(monkeypatch):
    # Create a test PDF using reportlab.
    test_pdf = create_test_pdf()

    # To avoid calling Ghostscript, monkey-patch subprocess.run
    def fake_subprocess_run(command, check):
        # Instead of actually compressing, create a dummy compressed PDF file.
        output_path = None
        input_path = None
        for arg in command:
            if arg.startswith("-sOutputFile="):
                output_path = arg.split("=", 1)[1]
            elif arg.endswith(".pdf"):
                input_path = arg
        with open(input_path, "rb") as fin, open(output_path, "wb") as fout:
            fout.write(fin.read())

    monkeypatch.setattr(subprocess, "run", fake_subprocess_run)

    compressed_pdf = compress_pdf(test_pdf, quality='screen')
    # Check that compressed_pdf is a BytesIO object and contains PDF header "%PDF"
    assert isinstance(compressed_pdf, io.BytesIO)
    compressed_pdf.seek(0)
    header = compressed_pdf.read(4)
    assert header == b"%PDF", "Compressed PDF does not have a valid PDF header."

def test_compress_file_blob_image():
    # Create a test image and simulate a file-like object with a filename attribute.
    test_image = create_test_image("CompressMe")
    compressed = compress_file_blob(test_image, test_image.filename, quality='screen')
    assert isinstance(compressed, io.BytesIO)
    compressed.seek(0)
    data = compressed.read(2)
    # Check that it is a JPEG.
    assert data == b'\xff\xd8', "File blob compression for image did not produce a JPEG."

def test_compress_file_blob_pdf(monkeypatch):
    # Create a test PDF.
    test_pdf = create_test_pdf()

    def fake_subprocess_run(command, check):
        output_path = None
        input_path = None
        for arg in command:
            if arg.startswith("-sOutputFile="):
                output_path = arg.split("=", 1)[1]
            elif arg.endswith(".pdf"):
                input_path = arg
        with open(input_path, "rb") as fin, open(output_path, "wb") as fout:
            fout.write(fin.read())

    monkeypatch.setattr(subprocess, "run", fake_subprocess_run)

    compressed_pdf = compress_file_blob(test_pdf, test_pdf.filename, quality='screen')
    assert isinstance(compressed_pdf, io.BytesIO)
    compressed_pdf.seek(0)
    header = compressed_pdf.read(4)
    assert header == b"%PDF", "File blob compression for PDF did not produce a valid PDF."

# --- Tests for text processing functions ---

def test_process_text_with_regex():
    sample_text = "12345 ProductA 10 19.99 199.90"
    json_output = process_text_with_regex(sample_text)
    data = json.loads(json_output)
    # Expect one match with the given keys.
    assert isinstance(data, list) and len(data) == 1
    entry = data[0]
    assert "ProductId" in entry
    assert "Description" in entry
    assert entry["ProductId"] == "12345"
    # Optionally verify additional keys, e.g. Quantity as an integer.
    assert isinstance(entry.get("Quantity"), int), "Quantity should be an integer"

def test_process_text_with_nlp():
    # Attempt to load the spaCy models. If it's not available, skip the test.
    try:
        spacy.load("en_core_web_sm")
    except OSError:
        pytest.skip("spaCy models 'en_core_web_sm' not installed. Run 'python -m spacy download en_core_web_sm' to install it.")

    sample_text = "12345 ProductA 10 19.99 199.90. Some extra text."
    json_output = "no" #process_text_with_nlp(sample_text)
    assert json_output is not None, "The function returned None instead of JSON content."

    try:
        data = json.loads(json_output)
    except json.JSONDecodeError as e:
        pytest.fail(f"JSON decoding failed: {e}")

    assert isinstance(data, list), "Expected the JSON output to be a list of entries."
    if data:
        entry = data[0]
        for field in ["ProductId", "Description", "Quantity", "Price", "Ext_Price"]:
            assert field in entry, f"Missing expected field '{field}' in the entry."

def test_extract_json_from_code_block():
    text = """
    Here is the result:
    ```json
    {"key": "value"}
    ```
    Some extra text.
    """
    json_content = extract_json_from_code_block(text)
    assert json_content == '{"key": "value"}'

def test_process_text_with_gpt(monkeypatch):
    # Monkey-patch openai.ChatCompletion.create to return a fixed response.
    fake_response = {
        "choices": [{
            "message": {
                "content": "```json\n{\"vendor_name\": \"Test Vendor\", \"date\": \"2025-03-07\", \"tax\": \"0.00\", \"line_items\": []}\n```"
            }
        }]
    }
    def fake_create(*args, **kwargs):
        return fake_response

    monkeypatch.setattr("openai.ChatCompletion.create", fake_create)
    sample_text = "Sample text for GPT processing"
    json_content = process_text_with_gpt(sample_text)
    data = json.loads(json_content)
    assert data.get("vendor_name") == "Test Vendor"
    assert data.get("line_items") == []

# To run all tests from the command line, execute: pytest -v
