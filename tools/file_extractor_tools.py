import re
from pathlib import Path
import logging
from transformers import pipeline
import PyPDF2
import openai
import json
from functools import reduce
from flask_cors import CORS
import pdfplumber
import pandas as pd
import os


# Configure logging
logging.basicConfig(level=logging.INFO)

# Load the NLP pipeline for summarization
keyword_extractor = pipeline("summarization", model="facebook/bart-large-cnn")



# Set your OpenAI API key
openai.api_key = api_key = os.environ.get("FILE_EXTRACTOR_API_KEY")

# Alternatively, raise an exception if the key is not found
if "FILE_EXTRACTOR_API_KEY" not in os.environ:
    raise KeyError("API_KEY environment variable not set")



def parse_json_to_dataframe(json_content):
    try:
        data = json.loads(json_content)
        return pd.DataFrame(data)
    except Exception as e:
        logging.error(f"JSON parsing error: {e}")
        return pd.DataFrame()

def extract_text_from_pdf(file):
    """
    Extract all text from a PDF file.
    """
    try:
        with pdfplumber.open(file) as pdf:
            pdf_text = ""
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    pdf_text += page_text + "\n"
                else:
                    logging.warning(f"No text found on page {page.page_number} of {file.filename}")
            return pdf_text
    except Exception as e:
        logging.error(f"An error occurred while extracting text from the PDF: {e}")
        return None

def extract_json_from_code_block(text):
    """
    Extract JSON content from a code block in the text.
    """
    # Use regex to find content between ```json and ```
    match = re.search(r"```json\s*(\{.*?\}|\[.*?\])\s*```", text, re.DOTALL)
    if match:
        json_content = match.group(1)
        return json_content.strip()
    else:
        # If no code block is found, attempt to find JSON in the text
        match = re.search(r"(\{.*?\}|\[.*?\])", text, re.DOTALL)
        if match:
            json_content = match.group(1)

            return json_content.strip()
        else:
            logging.error("No JSON content found in the GPT response.")
            return None

def process_text_with_gpt(pdf_text):
    """
    Use OpenAI's GPT model to extract data from the text.
    """
    try:
        # Check the length of the input text
        if len(pdf_text) > 3500:
            logging.warning("Input text is too long; truncating to fit within token limits.")
            pdf_text = pdf_text[:3500]

        messages = [
            {
                "role": "system",
                "content": (
                    "You are a data extraction assistant. Extract all the tabular data from the provided text and "
                    "return it as a complete and valid JSON array. Ensure that the JSON is properly formatted and "
                    "includes all necessary closing brackets. The fields should be: ProductId, Description, Qty, Price, and Ext_Price."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Text:\n{pdf_text}\n\n"
                    "Please provide the extracted data in a complete JSON array format within a code block, like so:\n"
                    "```json\n"
                    "[\n"
                    "  {{\n"
                    "    \"ProductId\": \"...\",\n"
                    "    \"Description\": \"...\",\n"
                    "    \"Quantity\": \"...\",\n"
                    "    \"Price\": \"...\",\n"
                    "    \"EXT_Price\": \"...\"\n"
                    "  }},\n"
                    "  // more items\n"
                    "]\n"
                    "```\n"
                ),
            },
        ]

        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=messages,
            max_tokens=1500,  # Adjust as necessary, total tokens must be <= 4096
            temperature=0.0,
            n=1,
        )

        # Extract the assistant's message
        extracted_data = response['choices'][0]['message']['content'].strip()

        # Print the GPT response for debugging
        # print("GPT Response:", extracted_data)

        # Extract JSON from code block
        json_content = extract_json_from_code_block(extracted_data)
        if not json_content:
            logging.error("No JSON content found in the GPT response.")
            return None

        return json_content
    except Exception as e:
        logging.error(f"Error while processing text with GPT: {type(e).__name__}: {e}")
        return None

def parse_json_content(json_content):
    try:
        data = json.loads(json_content)
        return pd.DataFrame(data)
    except json.JSONDecodeError as e:
        logging.error(f"JSON decoding error: {e}")
        logging.error(f"JSON content received:\n{json_content}")
        # Optionally write the invalid JSON to a file for analysis
        with open('invalid_json_output.json', 'w') as f:
            f.write(json_content)
        return pd.DataFrame(columns=["ProductId", "Description", "Quantity", "Price", "EXT_Price"])

def extract_pdf_data(file):
    """
    Extract structured data from a PDF file using OpenAI's GPT model.
    """
    # Extract all text from the PDF
    pdf_text = extract_text_from_pdf(file)
    if not pdf_text:
        logging.warning(f"No text extracted from {file.filename}")
        return pd.DataFrame(columns=["ProductId", "Description", "Quantity", "Price", "EXT_Price"])

    # Use GPT to process the text and extract data
    gpt_result = process_text_with_gpt(pdf_text)
    if gpt_result:
        # Remove any extraneous whitespace
        gpt_result = gpt_result.strip()
        try:
            # Parse GPT output into a DataFrame
            df = parse_json_content(gpt_result)
            if not df.empty:
                logging.info(f"Extracted {len(df)} rows from {file.filename} using GPT")
                return df
            else:
                logging.warning(f"No data parsed into DataFrame from {file.filename}")
                return pd.DataFrame(columns=["ProductId", "Description", "Quantity", "Price", "EXT_Price"])
        except Exception as e:
            logging.error(f"Failed to parse GPT output into DataFrame: {type(e).__name__}: {e}")
            return pd.DataFrame(columns=["ProductId", "Description", "Quantity", "Price", "EXT_Price"])
    else:
        logging.warning(f"No data extracted from {file.filename} using GPT")
        return pd.DataFrame(columns=["ProductId", "Description", "Quantity", "Price", "EXT_Price"])
