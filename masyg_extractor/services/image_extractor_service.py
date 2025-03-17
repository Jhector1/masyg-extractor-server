
import pytesseract


import logging

import subprocess
import tempfile

from PIL import Image
from io import BytesIO


def extract_text_from_image(file):
    """
    Extract all text from an image file using pytesseract.
    :param file: A file-like object.
    :return: The extracted text as a single string.
    """
    filename = getattr(file, 'filename', 'unknown')
    try:
        file.seek(0)
        # Log file pointer position and read a small chunk for validation
        initial_bytes = file.read(64)
        logging.info(f"Initial bytes of file {filename}: {initial_bytes}")
        file.seek(0)  # Reset pointer after reading

        image = Image.open(file)
        logging.info(f"Opened image: format={image.format}, mode={image.mode}, size={image.size}")

        text = pytesseract.image_to_string(image)
        if not text.strip():
            logging.warning(f"No text found in the image {filename}")
        return text
    except Exception as e:
        logging.error(f"Error extracting text from image {filename}: {e}")
        return ""


def compress_image(file, quality=30, max_size=(800, 800)):
    file.seek(0)
    # Use the .file attribute if available; otherwise, use the file directly.
    image_file = getattr(file, 'file', file)
    image = Image.open(image_file)
    image.thumbnail(max_size)
    if image.mode != "RGB":
        image = image.convert("RGB")
    output = BytesIO()
    image.save(output, format="JPEG", quality=quality)
    output.seek(0)
    return output


def compress_pdf(file, quality='screen'):
    """
    Aggressively compress a PDF using Ghostscript.
    - Writes the original PDF to a temporary file.
    - Runs Ghostscript with the provided quality preset.
    - Reads the compressed PDF back into memory.

    :param file: A file-like object containing PDF data.
    :param quality: Ghostscript quality preset; 'screen' yields maximum compression.
    :return: BytesIO stream containing the compressed PDF.
    """
    file.seek(0)
    with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as temp_in:
        temp_in.write(file.read())
        temp_in.flush()
        input_path = temp_in.name

    output_path = input_path.replace('.pdf', '_compressed.pdf')
    command = [
        'gs', '-sDEVICE=pdfwrite', '-dCompatibilityLevel=1.4',
        f'-dPDFSETTINGS=/{quality}', '-dNOPAUSE', '-dQUIET', '-dBATCH',
        f'-sOutputFile={output_path}', input_path
    ]
    subprocess.run(command, check=True)
    with open(output_path, 'rb') as f:
        compressed_data = f.read()
    return BytesIO(compressed_data)
def compress_file_blob(file, original_filename, quality='screen'):
    """
    Compress the uploaded file based on its type (image or PDF) using aggressive settings.
    """
    # Use file.filename if available; otherwise, use the provided original_filename.
    filename = getattr(file, 'filename', original_filename)
    filename_lower = filename.lower()
    file.seek(0)
    if filename_lower.endswith(('.png', '.jpg', '.jpeg', '.bmp', '.tiff')):
        return compress_image(file, quality=30, max_size=(800, 800))
    elif filename_lower.endswith('.pdf'):
        return compress_pdf(file, quality=quality)
    else:
        file.seek(0)
        return file
