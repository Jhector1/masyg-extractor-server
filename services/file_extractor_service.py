import re
from pathlib import Path
import logging
from transformers import pipeline
import PyPDF2
import openai
import json
import boto3
from functools import reduce
from flask_cors import CORS
import pdfplumber
import pandas as pd
import os
import requests
import logging

from pdf2image import convert_from_path
from google.cloud import vision
from io import BytesIO
from pdf2image import convert_from_bytes
import pytesseract
import logging

import fitz  # PyMuPDF

# Configure logging
logging.basicConfig(level=logging.INFO)

# Load the NLP pipeline for summarization (if needed)
keyword_extractor = pipeline("summarization", model="facebook/bart-large-cnn")

# Set your OpenAI API key from environment variable
openai.api_key = api_key = os.environ.get("FILE_EXTRACTOR_API_KEY")
if not openai.api_key:
    raise KeyError("FILE_EXTRACTOR_API_KEY not set in environment variables.")

def parse_json_to_dataframe(json_content,
                            fallback_columns=["ProductId", "Description", "Quantity", "Price", "EXT_Price"]):
    """
    Safely parse a JSON string into a Pandas DataFrame.
    If parsing fails, logs the error and writes the invalid JSON to a file.
    """
    try:
        data = json.loads(json_content)
        return pd.DataFrame(data)
    except json.JSONDecodeError as e:
        logging.error(f"JSON decoding error: {e}")
        logging.error(f"JSON content received:\n{json_content}")
        # Optionally write the invalid JSON to a file for debugging
        with open('invalid_json_output.json', 'w') as f:
            f.write(json_content)
        return pd.DataFrame(columns=fallback_columns)
    except Exception as e:
        logging.error(f"Unexpected JSON parsing error: {e}")
        return pd.DataFrame(columns=fallback_columns)

# def extract_text_from_pdf(file):
#     """
#     Extract all text from a PDF file using pdfplumber.
#     """
#     try:
#         with pdfplumber.open(file) as pdf:
#             pdf_text = []
#             for page in pdf.pages:
#                 page_text = page.extract_text()
#                 if page_text:
#                     pdf_text.append(page_text)
#                 else:
#                     logging.warning(f"No text found on page {page.page_number} of {file.filename}")
#             return "\n".join(pdf_text)
#     except Exception as e:
#         logging.error(f"Error extracting text from PDF {file.filename}: {e}")
#         return ""

import requests
import logging



from pdf2image import convert_from_bytes
from google.cloud import vision
from io import BytesIO

from pdf2image import convert_from_bytes
from google.cloud import vision
from io import BytesIO

def extract_text_from_pdf_image(file):
    """
    Extract raw text from a PDF file-like object using Google Cloud Vision API.

    :param file: A file-like object (e.g., from request.files) containing the PDF.
    :return: Extracted text as a string.
    """
    # Initialize the Vision API client
    client = vision.ImageAnnotatorClient()

    # Convert PDF (from bytes) to images
    file_content = file.read()  # Read the uploaded file content
    images = convert_from_bytes(file_content)  # Poppler will use the default path

    extracted_text = ""

    # Process each image and extract text
    for image in images:
        # Convert image to bytes for Vision API
        image_byte_array = BytesIO()
        image.save(image_byte_array, format="JPEG")
        image_content = image_byte_array.getvalue()

        # Send image to Vision API
        vision_image = vision.Image(content=image_content)
        response = client.text_detection(image=vision_image)

        if response.error.message:
            raise Exception(f"Vision API error: {response.error.message}")

        # Append detected text
        texts = response.text_annotations
        extracted_text += texts[0].description if texts else ""

    return extracted_text







def extract_text_with_ocr_space( uploaded_file, api_key='K84148755688957'):
    """
    Extract text from an image-based PDF using OCR.Space API, accepting a file from Flask `request.files`.

    :param api_key: Your OCR.Space API key.
    :param uploaded_file: A file-like object from `request.files`.
    :return: Extracted text as a single string.
    """
    try:
        # Define the API endpoint
        ocr_url = "https://api.ocr.space/parse/image"

        # Send the file to OCR.Space API
        response = requests.post(
            ocr_url,
            files={'file': (uploaded_file.filename, uploaded_file.stream, uploaded_file.mimetype)},
            data={
                'apikey': api_key,
                'language': 'eng',  # Default language (English)
                'isOverlayRequired': False  # True if text position overlay is required
            },
            timeout=60  # Timeout for the request
        )

        # Check HTTP response status
        if response.status_code != 200:
            logging.error(f"OCR.Space API error (HTTP {response.status_code}): {response.text}")
            return ""

        # Parse JSON response
        result = response.json()
        if not result.get('ParsedResults'):
            logging.error(f"OCR.Space returned no results: {result}")
            return ""

        # Extract text from all parsed results
        extracted_text = " ".join(res.get('ParsedText', '') for res in result['ParsedResults'])

        return extracted_text.strip()

    except requests.exceptions.RequestException as e:
        logging.error(f"Request exception during OCR.Space API call: {e}")
        return ""

    except Exception as e:
        logging.error(f"Unexpected error during OCR extraction: {e}")
        return ""









def extract_text_from_pdf(file):
    """
    Extract all text from a PDF file using PyMuPDF (fitz).

    :param file: A file-like object (e.g., from Flask `request.files`).
    :return: The extracted text as a single string.
    """
    try:
        # Read the file into memory
        pdf_data = file.read()
        # Reset the file pointer for future use
        file.seek(0)
        # Open the PDF from the in-memory bytes
        with fitz.open(stream=pdf_data, filetype="pdf") as doc:
            all_text = []
            for page_index, page in enumerate(doc, start=1):
                page_text = page.get_text()
                if page_text:
                    all_text.append(page_text)
                else:
                    logging.warning(f"No text found on page {page_index} of {file.filename}")
            return "\n".join(all_text)

    except Exception as e:
        logging.error(f"Error extracting text from PDF {file.filename}: {e}")
        return ""


def extract_json_from_code_block(text):
    """
    Attempt to extract JSON content from a code block (` ```json ... ``` `) in text.
    If not found, searches for any JSON structure in the text.
    """
    # 1) Try code-block-based extraction
    match = re.search(r"```json\s*(\{.*?\}|\[.*?\])\s*```", text, re.DOTALL)
    if match:
        return match.group(1).strip()

    # 2) Fallback to scanning for any JSON-like structure
    match = re.search(r"(\{.*?\}|\[.*?\])", text, re.DOTALL)
    if match:
        return match.group(1).strip()

    logging.error("No JSON content found in the GPT response.")
    return None

def process_text_with_gpt(pdf_text):
    """
    Use OpenAI's GPT model (gpt-3.5-turbo) to extract data from the text in JSON format.
    """
    try:
        # If text is very long, truncate to avoid exceeding token limits
        if len(pdf_text) > 3500:
            logging.warning("Input text is too long; truncating to ~3500 characters.")
            pdf_text = pdf_text[:3500]

        messages = [
            {
                "role": "system",
                "content": (
                    "You are a data extraction assistant. Extract all the tabular data from the provided text and "
                    "return it as a complete and valid JSON array. The fields should be: "
                    "ProductId, Description, Qty, Price, and Ext_Price."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Text:\n{pdf_text}\n\n"
                    "Please provide the extracted data in a complete JSON array format within a code block, like so:\n"
                    "```json\n"
                    "[\n"
                    "  {\n"
                    "    \"ProductId\": \"...\",\n"
                    "    \"Description\": \"...\",\n"
                    "    \"Quantity\": \"...\",\n"
                    "    \"Price\": \"...\",\n"
                    "    \"EXT_Price\": \"...\"\n"
                    "  },\n"
                    "  // more items\n"
                    "]\n"
                    "```\n"
                ),
            },
        ]

        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=messages,
            max_tokens=1500,
            temperature=0.0,
            n=1,
        )

        # Get GPT's response
        assistant_text = response['choices'][0]['message']['content'].strip()
        json_content = extract_json_from_code_block(assistant_text)
        return json_content if json_content else None

    except Exception as e:
        logging.error(f"Error processing text with GPT: {type(e).__name__}: {e}")
        return None
#
# def extract_pdf_data(file):
#     """
#     High-level function to:
#       1. Extract all text from a PDF
#       2. Process text with GPT to get JSON
#       3. Parse JSON into a DataFrame
#       4. Return a DataFrame with columns:
#          [ProductId, Description, Quantity, Price, EXT_Price]
#     """
#     pdf_text = extract_text_from_pdf(file)
#     if not pdf_text:
#         logging.warning(f"No text extracted from {file.filename}")
#         return pd.DataFrame(columns=["ProductId", "Description", "Quantity", "Price", "EXT_Price"])
#
#     json_result = process_text_with_gpt(pdf_text)
#     if not json_result:
#         logging.warning(f"No data extracted by GPT from {file.filename}")
#         return pd.DataFrame(columns=["ProductId", "Description", "Quantity", "Price", "EXT_Price"])
#
#     # Parse the JSON result into a DataFrame
#     df = parse_json_to_dataframe(json_result)
#     if df.empty:
#         logging.warning(f"No valid data parsed into DataFrame from {file.filename}")
#
#     logging.info(f"Extracted {len(df)} rows from {file.filename} using GPT")
#     return df
