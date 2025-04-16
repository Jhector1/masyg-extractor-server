import asyncio
import random
from typing import Dict, Any

import fitz
import httpx
import spacy
from transformers import pipeline
import openai

# import pdfplumber
import pandas as pd

import os
from werkzeug.utils import secure_filename
import camelot
from pdf2image import convert_from_path
from google.cloud import vision
from io import BytesIO
from pdf2image import convert_from_bytes
import pytesseract
import logging
from masyg_extractor.services.my_log import logger, send_log
from masyg_extractor.services.progress_log import ExtractorProgressLog
from masyg_extractor.utils.extensions import sio

# PyMuPDF

from masyg_extractor.utils.tool import clean_text

# Configure logging
logging.basicConfig(level=logging.INFO)

# Load the NLP pipeline for summarization (if needed)
keyword_extractor = pipeline("summarization", model="facebook/bart-large-cnn")

# Set your OpenAI API key from environment variable
openai.api_key = api_key = os.environ.get("FILE_EXTRACTOR_API_KEY")
if not openai.api_key:
    raise KeyError("FILE_EXTRACTOR_API_KEY not set in environment variables.")


def parse_json_to_dataframe(json_content,
                            fallback_columns=["item_id", "date", "vendor_name", "description", "quantity", "unit_price"]):
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
    images = convert_from_bytes(file_content, poppler_path="/usr/local/bin" if os.getenv(
        'FAST_API_ENV') == 'development' else '/usr/bin')  # Poppler will use the default path

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
            print(f"Vision API error: {response.error.message}")
            return ""

        # Append detected text
        texts = response.text_annotations
        extracted_text += texts[0].description if texts else ""

    return extracted_text



async def extract_text_with_ocr_space(uploaded_file,  api_key='K84148755688957'):
    """
    Extract text using OCR.Space, queuing progress logs rather than emitting directly.
    """
    ocr_url = "https://api.ocr.space/parse/image"

    # 1️⃣ Indicate start of OCR step
    logger.info(f"🔄 Trying OCR on {uploaded_file.filename}")

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            files = {'file': (uploaded_file.filename, uploaded_file.stream, uploaded_file.mimetype)}
            data = {'apikey': api_key, 'language': 'eng', 'isOverlayRequired': False}
            response = await client.post(ocr_url, files=files, data=data)

        response.raise_for_status()
        result = response.json()

        if not result.get('ParsedResults'):
            logger.info(f"❌ OCR returned no results for {uploaded_file.filename}")
            return ""
            # , "")

        extracted_text = " ".join(res.get('ParsedText', '') for res in result['ParsedResults'])
        logger.info(f"✅ OCR succeeded for {uploaded_file.filename}")
        return extracted_text.strip()
        # , "OCR_SPACE")

    except httpx.ReadTimeout:
        logger.info(f"❌ OCR request timed out for {uploaded_file.filename}")
    except httpx.HTTPStatusError as e:
        logger.info(f"❌ OCR HTTP error ({e.response.status_code}) for {uploaded_file.filename}")
    except Exception as e:
        logging.exception("Unexpected error in OCR extraction")
        logger.info(f"❌ Error processing {uploaded_file.filename}: {e}")

    return ""
            # , "")

# except requests.exceptions.RequestException as e:
#     logging.error(f"Request exception during OCR.Space API call: {e}")
#     return ""
#
# except Exception as e:
#     logging.error(f"Unexpected error during OCR extraction: {e}")
#     return ""

 # PyMuPDF

 # PyMuPDF
import logging

def extract_text_from_pdf(file):
    """
    Extract all text from a PDF file using PyMuPDF (fitz).

    :param file: A synchronous file-like object (e.g. BytesIO) containing the PDF data.
    :return: The extracted text as a single string.
    """
    try:
        # Since 'file' is a BytesIO, its read() method is synchronous.
        pdf_data = file.read()
        file.seek(0)  # Reset pointer for potential reuse.
        with fitz.open(stream=pdf_data, filetype="pdf") as doc:
            all_text = []
            for page_index, page in enumerate(doc, start=1):
                page_text = page.get_text()
                if page_text:
                    all_text.append(page_text)
                else:
                    logging.warning(f"No text found on page {page_index} of {getattr(file, 'filename', 'unknown')}")
            return "\n".join(all_text)
    except Exception as e:
        logging.error(f"Error extracting text from PDF: {e}")
        return ""


# def _extract_text_from_pdf_sync(file_obj, filename):
#     try:
#         pdf_data = file_obj.read()
#         file_obj.seek(0)
#         with fitz.open(stream=pdf_data, filetype="pdf") as doc:
#             all_text = []
#             for page_index, page in enumerate(doc, start=1):
#                 page_text = page.get_text()
#                 if page_text:
#                     all_text.append(page_text)
#                 else:
#                     logging.warning(f"No text found on page {page_index} of {filename}")
#             return "\n".join(all_text)
#     except Exception as e:
#         logging.error(f"Error extracting text from PDF {filename}: {e}")
#         return ""
#



def extract_text_from_pdf_camelot(file):
    """
    Extract tables from a PDF file uploaded via request.files using Camelot.

    :param file: A file-like object from FAST_API `request.files`.
    :return: Extracted table text as a single string or a message indicating no tables found.
    """
    try:
        # Save the uploaded file to a temporary location
        filename = secure_filename(file.filename)
        temp_file_path = os.path.join("/tmp", filename)
        file.save(temp_file_path)

        # Extract tables from the PDF
        tables = camelot.read_pdf(temp_file_path, pages="all")

        # Remove the temporary file
        os.remove(temp_file_path)

        # Check if tables were extracted
        if not tables or len(tables) == 0:
            logging.warning(f"No tables found in PDF: {file.filename}")
            return "No tables found."

        # Combine all extracted tables into a single string
        all_tables = []
        for index, table in enumerate(tables):
            try:
                all_tables.append(f"Table {index + 1}:\n{table.df.to_string(index=False)}")
            except Exception as e:
                logging.warning(f"Error processing table {index + 1} in {file.filename}: {e}")

        return "\n\n".join(all_tables)

    except Exception as e:
        logging.error(f"Error extracting tables from PDF {file.filename}: {e}")
        return f"Error processing the PDF: {str(e)}"


def extract_text_from_scanned_pdf(file):
    """
    Extract text from a scanned PDF file using Tesseract OCR.

    :param file: A file-like object (e.g., from FAST_API `request.files`).
    :return: The extracted text as a single string.
    """
    try:
        # Secure the filename and save the file temporarily
        filename = secure_filename(file.filename)
        temp_file_path = os.path.join("/tmp", filename)
        file.save(temp_file_path)

        # Convert PDF pages to images
        images = convert_from_path(temp_file_path)

        # Extract text from each image using Tesseract OCR
        all_text = []
        for page_number, image in enumerate(images, start=1):
            try:
                text = pytesseract.image_to_string(image)
                if text.strip():
                    all_text.append(text)
                else:
                    logging.warning(f"No text detected on page {page_number} of {filename}")
            except Exception as ocr_error:
                logging.warning(f"Error processing page {page_number}: {ocr_error}")

        # Clean up the temporary file
        os.remove(temp_file_path)

        # Return the extracted text
        if all_text:
            return "\n\n".join(all_text)
        else:
            logging.warning(f"No text found in scanned PDF: {filename}")
            return ""

    except Exception as e:
        logging.error(f"Error extracting text from scanned PDF {file.filename}: {e}")
        return ""

#
# def extract_json_from_code_block(text):
#     """
#     Attempt to extract JSON content from a code block (` ```json ... ``` `) in text.
#     If not found, searches for any JSON structure in the text.
#     """
#     # 1) Try code-block-based extraction
#     match = re.search(r"```json\s*(\{.*?\}|\[.*?\])\s*```", text, re.DOTALL)
#     if match:
#         return match.group(1).strip()
#
#     # 2) Fallback to scanning for any JSON-like structure
#     match = re.search(r"(\{.*?\}|\[.*?\])", text, re.DOTALL)
#     if match:
#         return match.group(1).strip()
#
#     logging.error("No JSON content found in the GPT response.")
#     return None
#


import re
import json
import logging

def extract_json_from_code_block(text):
    """
    Attempt to extract JSON content from a code block (```json ... ```) in text.
    If not found, search for any JSON structure in the text and then parse it.
    """
    # 1) Try extraction from a code block
    match = re.search(r"```json\s*(\{.*?\}|\[.*?\])\s*```", text, re.DOTALL)
    if match:
        json_str = match.group(1).strip()
    else:
        # 2) Fallback: search for any JSON-like structure
        match = re.search(r"(\{.*?\}|\[.*?\])", text, re.DOTALL)
        if match:
            json_str = match.group(1).strip()
        else:
            logging.error("No JSON content found in the GPT response.")
            return None
    try:
        # print(json_str)
        return json.loads(json_str)
    except Exception as e:
        logging.error(f"Failed to parse JSON: {e}")
        return None


async def process_chunk(chunk_text,progress_logger: ExtractorProgressLog, file_progress_share: float):
    """
    Use OpenAI's GPT model (gpt-3.5-turbo) to extract data from the text in JSON format.
    The progress for each chunk is updated in the shared progress_dict.
    """
    """
     Process a text chunk with GPT. Here, we simulate progress updates in this chunk.
     'file_progress_share' is the share of overall progress that this chunk's stage contributes.
     """

    total_steps = 5
    local_progress = 0.0
    for step in range(total_steps):
        await asyncio.sleep(random.uniform(0.8, 1.5))
        local_progress = ((step + 1) / total_steps) * 100
        # Here, you could calculate a scaled progress: (local_progress/100)*file_progress_share
        overall_chunk_progress = (local_progress / 100) * file_progress_share
        await progress_logger.safe_emit_progress( overall_chunk_progress)
    # Once the simulated progress is complete, perform the actual GPT call.

    messages = [
        {
            "role": "system",
            "content": (
                "You are a specialized data extraction assistant. Your goal is to identify and extract all "
                "tabular data from the supplied text, and then present it as a well-structured and valid JSON object. "
                "The output should represent one receipt per file upload with the following structure: "
                "a top-level JSON object that includes the fields **documentType** (invoice, receipt, bill, or ..), **vendor_name**, **date**, **due_date**, and **line_items**. "
                "The `line_items` field is an array where each entry must capture these fields (or their close synonyms): "
                "[**item_name** (or **product_name**): if a specific item name is provided, use it; otherwise, derive a concise name not more than 3 words], "
                "**category**, **description**, **quantity**, **tax**(NON or TAX) **sku**: generate one if not provided 3 descriptive letter of ItemName and 5-random-digit (TLP-45035), and **unit_price(without currency)**. "
                "If the file contains multiple rows with the same vendor (and the same date and tax), group all these rows "
                "into one receipt object by combining their line items into the `line_items` array. "
                "Ensure that the output is a single valid JSON object enclosed within a code block."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Text:\n{chunk_text}\n\n"
                "Please extract the receipt data from the text and provide the result as a valid JSON object within a code block, "
                "using the following format:\n\n"
                "```json\n"
                "{\n"
                "  \"documentType\": \"...\",\n"
                "  \"vendor_name\": \"...\",\n"
                "  \"date\": \"...\",\n"
                "  \"due_date\": \"...\",\n"
                "  \"line_items\": [\n"
                "    {\n"
                "      \"item_name\": \"...\",\n"
                "      \"category\": \"...\",\n"
                "      \"description\": \"...\",\n"
                "      \"quantity\": \"...\",\n"
                "      \"tax\": \"...\",\n"
                "      \"sku\": \"...\",\n"
                "      \"unit_price\": \"...\"\n"
                "    }\n"
                "    // additional line items as needed\n"
                "  ]\n"
                "}\n"
                "```"
            ),
        },
    ]

    # Implement retry logic for the GPT call
    max_retries = 3
    base_delay = 1  # seconds
    for attempt in range(max_retries):
        try:
            response = openai.ChatCompletion.create(
                model="gpt-3.5-turbo",
                messages=messages,
                max_tokens=1500,
                temperature=0.0,
                n=1,
            )
            assistant_text = response['choices'][0]['message']['content'].strip()
            json_content = extract_json_from_code_block(assistant_text)
            if json_content:
                return json_content
            else:
                # If json_content is None or empty, raise an error to trigger retry
                raise ValueError("Empty JSON content")
        except Exception as e:
            logging.error(f"Attempt {attempt + 1} failed with error: {type(e).__name__}: {e}")
            if attempt < max_retries - 1:
                delay = base_delay * (2 ** attempt)
                logging.warning(f"Retrying in {delay} seconds...")
                await asyncio.sleep(delay)
            else:
                logging.error("All retry attempts failed. Returning None.")
                return None



async def process_text_with_gpt(pdf_text: str, progress_logger: ExtractorProgressLog, progress: Dict[str, float], files_count: int) -> Any:
    """
    Process the entire text with GPT.
    We'll divide the GPT processing stage equally among chunks.
    The stage weight is assumed to be 20% of overall progress.
    """
    pdf_text = clean_text(pdf_text)
    if len(pdf_text) <= 0:
        return None
    print(f"Processing chunk...")
    gpt_stage_weight = ExtractorProgressLog.GPT_PROCESSING_WEIGHT  # GPT stage contributes 50% to overall progress
    if len(pdf_text) <= 1500:
        # Single chunk: full share for GPT stage from this file.
        result = await process_chunk(pdf_text, progress_logger, gpt_stage_weight)
        progress["gpt_processing"] = gpt_stage_weight
        await progress_logger.safe_emit_progress(progress_logger.calculate_overall_progress(progress))
        return result

    # Multiple chunks
    chunks = [pdf_text[i:i+1500] for i in range(0, len(pdf_text), 1500)]
    chunk_share = gpt_stage_weight / len(chunks)
    tasks = [process_chunk(chunk, progress_logger, chunk_share) for chunk in chunks]
    results = await asyncio.gather(*tasks)
    progress["gpt_processing"] = gpt_stage_weight
    await progress_logger.safe_emit_progress( progress_logger.calculate_overall_progress(progress))
    results = [r for r in results if r is not None]
    if not results:
        return None
    # Merge results if needed (here, just return the first)
    combined_result = results[0]
    for result in results[1:]:
        if "line_items" in result:
            combined_result["line_items"].extend(result.get("line_items", []))
    return combined_result


# def process_text_with_gpt_sync(pdf_text):
#     """
#     Synchronous wrapper for process_text_with_gpt.
#     """
#     return asyncio.run(process_text_with_gpt(pdf_text))


# Example usage:



def process_text_with_regex(pdf_text):
    """
    Extract tabular data using regular expressions and return it in JSON format.

    Fields: ProductId, Description, Quantity, Price, Ext_Price
    """
    try:
        # Regex pattern for extracting structured data
        pattern = re.compile(r'(?P<ProductId>\d{3,})\s+(?P<Description>[\w\s]+?)\s+(?P<Qty>\d+)\s+(?P<Price>\d+\.\d{2})\s+(?P<Ext_Price>\d+\.\d{2})')

        matches = pattern.finditer(pdf_text)
        extracted_data = []

        for match in matches:
            extracted_data.append({
                "ProductId": match.group("ProductId"),
                "Description": match.group("Description").strip(),
                "Quantity": int(match.group("Qty")),
                "Price": float(match.group("Price")),
                "Ext_Price": float(match.group("Ext_Price"))
            })

        # Convert extracted data to JSON format
        return json.dumps(extracted_data, indent=2)

    except Exception as e:
        logging.error(f"Error processing text with regex: {type(e).__name__}: {e}")
        return None

def process_text_with_nlp(pdf_text):
    nlp = spacy.load("en_core_web_sm")
    """
    Use spaCy NLP to extract tabular data from the text and return it in JSON format.

    Fields: ProductId, Description, Quantity, Price, Ext_Price
    """
    try:
        doc = nlp(pdf_text)
        extracted_data = []

        # Iterate over sentences to identify potential tabular data
        for sentence in doc.sents:
            words = sentence.text.split()
            if len(words) >= 5:  # Ensure the sentence has enough components to represent tabular data
                try:
                    product_id = words[0]
                    description = " ".join(words[1:-3])
                    quantity = int(words[-3])
                    price = float(words[-2])
                    ext_price = float(words[-1])

                    extracted_data.append({
                        "ProductId": product_id,
                        "Description": description.strip(),
                        "Quantity": quantity,
                        "Price": price,
                        "Ext_Price": ext_price
                    })
                except (ValueError, IndexError):
                    # Skip invalid lines
                    continue

        # Convert extracted data to JSON format
        return json.dumps(extracted_data, indent=2)

    except Exception as e:
        logging.error(f"Error processing text with NLP: {type(e).__name__}: {e}")
        return None


import re


def remove_non_alphanumeric(text):
    """
    Removes any character from the input text that is not a letter, a digit, or a space.

    Args:
        text (str): The input string to be cleaned.

    Returns:
        str: A string containing only letters, digits, and spaces.
    """
    # The regular expression [^A-Za-z0-9 ] matches any character that is NOT a letter, digit, or space.
    return re.sub(r'[^A-Za-z0-9 ]', '', text)


# Example usage:
if __name__ == "__main__":
    sample_text = "Hello, World! 123. @$%"
    cleaned_text = remove_non_alphanumeric(sample_text)
     # Output: "Hello World 123"
