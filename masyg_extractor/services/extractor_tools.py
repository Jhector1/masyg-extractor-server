import re
import io
import os
import logging
import requests
import camelot
 # PyMuPDF
import pytesseract
from pytesseract import image_to_string
from google.cloud import vision
from pdf2image import convert_from_bytes
from werkzeug.utils import secure_filename
from pathlib import Path
from transformers import pipeline
import PyPDF2
import openai
import json
import boto3
from functools import reduce
import pandas as pd

# Configure logging
logging.basicConfig(level=logging.INFO)

def extract_text_from_pdf(file):
    """
    Reads an entire PDF file into memory as bytes
    and uses PyMuPDF (fitz) to extract text.
    """
    try:
        pdf_data = file.read()
        pdf_stream = io.BytesIO(pdf_data)  # Convert bytes to BytesIO
        with fitz.open(stream=pdf_stream, filetype="pdf") as doc:
            all_text = []
            for page in doc:
                page_text = page.get_text()
                if page_text:
                    all_text.append(page_text)
            return "\n".join(all_text)
    except Exception as e:
        logging.error(f"PyMuPDF extraction error: {e}")
        return ""

def extract_text_from_pdf_image(file):
    """
    Uses Google Cloud Vision to detect text from a PDF
    by converting each page to an image, then sending it
    to Vision API.
    """
    try:
        # 1) Read all PDF bytes into memory
        pdf_bytes = file.read()
        pdf_stream = io.BytesIO(pdf_bytes)

        # 2) Convert PDF pages to images
        images = convert_from_bytes(pdf_stream.getvalue())

        # 3) Initialize Vision client
        client = vision.ImageAnnotatorClient()

        extracted_text = ""
        for image in images:
            image_byte_array = io.BytesIO()
            image.save(image_byte_array, format="PNG")
            image_content = image_byte_array.getvalue()

            vision_image = vision.Image(content=image_content)
            response = client.text_detection(image=vision_image)

            if response.error.message:
                logging.error(f"Vision API error: {response.error.message}")
                return ""

            texts = response.text_annotations
            extracted_text += texts[0].description if texts else ""
        return extracted_text
    except Exception as e:
        logging.error(f"Google Vision API error: {e}")
        return ""

def extract_text_with_ocr_space(file, api_key='K84148755688957'):
    """
    Sends a file to OCR.Space API for text extraction.
    The file is posted as multipart form data.
    """
    try:
        file_bytes = file.read()
        # 'file.filename' and 'file.mimetype' depend on your file object type
        # For example, if using FAST_API's `FileStorage`, you can get these attributes directly.
        # Adjust below as needed for your environment:

        ocr_url = "https://api.ocr.space/parse/image"
        response = requests.post(
            ocr_url,
            files={'file': ('uploaded_file.pdf', file_bytes, 'application/pdf')},
            data={
                'apikey': api_key,
                'language': 'eng',
                'isOverlayRequired': False
            },
            timeout=60
        )

        if response.status_code != 200:
            logging.error(f"OCR.Space API error: {response.status_code}")
            return ""

        result = response.json()
        if not result.get('ParsedResults'):
            logging.error("OCR.Space returned no results.")
            return ""

        extracted_text = " ".join(res.get('ParsedText', '') for res in result['ParsedResults'])
        return extracted_text.strip()
    except Exception as e:
        logging.error(f"OCR.Space error: {e}")
        return ""

def extract_text_from_scanned_pdf(file):
    """
    Converts a scanned PDF's pages to images in memory,
    then uses pytesseract to extract text from each image.
    """
    try:
        pdf_data = file.read()
        images = convert_from_bytes(pdf_data)
        extracted_text = ""
        for image in images:
            extracted_text += pytesseract.image_to_string(image)
        return extracted_text
    except Exception as e:
        logging.error(f"Pytesseract error: {e}")
        return ""

# extract_text_with_ocr_space

def extract_text_from_pdf_camelot(file):
    """
    Extracts table data from a PDF using Camelot.
    Saves file to a temporary location, then removes it after reading.
    """
    try:
        pdf_data = file.read()
        filename = secure_filename(getattr(file, 'filename', 'uploaded.pdf'))
        temp_file_path = os.path.join("/tmp", filename)

        # Write bytes to temp file
        with open(temp_file_path, "wb") as f:
            f.write(pdf_data)

        # Read tables using Camelot
        tables = camelot.read_pdf(temp_file_path, pages="all")

        # Cleanup
        os.remove(temp_file_path)

        if not tables or tables.n == 0:
            logging.warning("No tables found by Camelot.")
            return ""

        all_tables = []
        for index, table in enumerate(tables):
            try:
                all_tables.append(f"Table {index + 1}:\n{table.df.to_string(index=False)}")
            except Exception as e:
                logging.warning(f"Error processing table {index + 1}: {e}")

        return "\n\n".join(all_tables)
    except Exception as e:
        logging.error(f"Camelot error: {e}")
        return ""

def extract_relevant_data(text):
    """
    Example function that uses regex to parse 'Sections'
    and 'Product' lines from a block of text.
    """
    sections = re.findall(
        r"Section Title:\s*(.*?)\n(.*?)\n(Section Amount:\s*\$\d+\.\d+ USD)",
        text, re.DOTALL
    )

    product_pattern = re.compile(
        r"(?P<product_no>\S+)\s+\$\s*(?P<unit_price>\d+\.\d{2})\s+(?P<qty>\d+)\s+\$\s*(?P<amount>\d+\.\d{2})"
    )

    extracted_data = []
    for section_title, products_block, section_amount in sections:
        products = []
        for match in product_pattern.finditer(products_block):
            products.append({
                "Product No": match.group("product_no"),
                "Unit Price": float(match.group("unit_price")),
                "Qty": int(match.group("qty")),
                "Amount": float(match.group("amount")),
            })
        extracted_data.append({
            "Section Title": section_title.strip(),
            "Section Amount": section_amount.split(":")[1].strip(),
            "Products": products,
        })
    return extracted_data

def extract_text_after_conversion_to_image(file):
    """
    Converts each page of the PDF to a PNG image in memory,
    then extracts text from each image using Tesseract (pytesseract).
    """
    try:
        pdf_data = file.read()
        images = convert_from_bytes(pdf_data, fmt="png")
        extracted_text = ""
        for image in images:
            extracted_text += image_to_string(image)
        return extracted_text
    except Exception as e:
        logging.error(f"Error during text extraction: {e}")
        return ""

def extract_vendor_and_date(file):
    """
    Example function that uses Tesseract to find a 'Vendor' name
    and 'Date' in a PDF. Then uses regex to parse from the OCR text.
    """
    try:
        pdf_data = file.read()
        images = convert_from_bytes(pdf_data)
        full_text = ""
        for image in images:
            full_text += image_to_string(image)

        # Example regex patterns
        vendor_pattern = r"Vendor Name: ([A-Za-z\s]+)"
        date_pattern = r"\b(\d{4}-\d{2}-\d{2})\b"

        vendor_match = re.search(vendor_pattern, full_text)
        date_match = re.search(date_pattern, full_text)

        return {
            "vendor_name": vendor_match.group(1).strip() if vendor_match else None,
            "date": date_match.group(1).strip() if date_match else None,
        }
    except Exception as e:
        logging.error(f"Error extracting vendor and date: {e}")
        return {"vendor_name": None, "date": None}
