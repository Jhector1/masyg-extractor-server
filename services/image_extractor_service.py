from PIL import Image
import pytesseract
import logging

def extract_text_from_image(file):
    """
    Extract all text from an image file using pytesseract.

    :param file: A file-like object (e.g., from Flask `request.files`).
    :return: The extracted text as a single string.
    """
    try:
        # Open the image using PIL
        image = Image.open(file)

        # Extract text using pytesseract
        text = pytesseract.image_to_string(image)

        if not text.strip():
            logging.warning(f"No text found in the image {file.filename}")
        return text

    except Exception as e:
        logging.error(f"Error extracting text from image {file.filename}: {e}")
        return ""
