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
from werkzeug.utils import secure_filename
import camelot

from pdf2image import convert_from_path, convert_from_bytes
from google.cloud import vision
from io import BytesIO
import pytesseract
import fitz  # PyMuPDF

# Configure logging
logging.basicConfig(level=logging.INFO)

def extract_text_from_pdf(file):
    try:
        pdf_data = file.read()
        file.seek(0)
        with fitz.open(stream=pdf_data, filetype="pdf") as doc:
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
    try:
        client = vision.ImageAnnotatorClient()
        file_content = file.read()
        file.seek(0)
        images = convert_from_bytes(file_content)

        extracted_text = ""
        for image in images:
            image_byte_array = BytesIO()
            image.save(image_byte_array, format="JPEG")
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
    try:
        ocr_url = "https://api.ocr.space/parse/image"
        response = requests.post(
            ocr_url,
            files={'file': (file.filename, file.stream, file.mimetype)},
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
    try:
        images = convert_from_bytes(file.read())
        file.seek(0)
        extracted_text = ""
        for image in images:
            extracted_text += pytesseract.image_to_string(image)
        return extracted_text
    except Exception as e:
        logging.error(f"Pytesseract error: {e}")
        return ""

def extract_text_from_pdf_camelot(file):
    try:
        filename = secure_filename(file.filename)
        temp_file_path = os.path.join("/tmp", filename)
        file.save(temp_file_path)

        tables = camelot.read_pdf(temp_file_path, pages="all")
        os.remove(temp_file_path)

        if not tables:
            logging.warning("No tables found.")
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
    # Extract sections with their titles and totals
    sections = re.findall(r"Section Title:\s*(.*?)\n(.*?)\n(Section Amount:\s*\$\d+.\d+ USD)", text, re.DOTALL)

    # Extract product details: Product No, Unit Price, Qty, Amount

    product_pattern = re.compile(
        r"(?P<product_no>\S+)\s+\$\s*(?P<unit_price>\d+\.\d{2})\s+(?P<qty>\d+)\s+\$\s*(?P<amount>\d+\.\d{2})"
    )

    # Final structured data
    extracted_data = []

    for section_title, products_block, section_amount in sections:
        # Extract products in the section
        products = []
        for match in product_pattern.finditer(products_block):
            products.append({
                "Product No": match.group("product_no"),
                "Unit Price": float(match.group("unit_price")),
                "Qty": int(match.group("qty")),
                "Amount": float(match.group("amount")),
            })

        # Add the section data
        extracted_data.append({
            "Section Title": section_title.strip(),
            "Section Amount": section_amount.split(":")[1].strip(),
            "Products": products,
        })

    return extracted_data




