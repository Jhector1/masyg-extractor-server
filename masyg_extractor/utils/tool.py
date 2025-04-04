import re

SENSITIVE_PATTERN = re.compile(
    r"(?P<EMAIL>\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b)|"
    r"(?P<PHONE>\b\d{10,15}\b)|"
    r"(?P<CREDIT_CARD>\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b)|"
    r"(?P<ADDRESS>\d+\s+\w+\s+(?:Street|St|Avenue|Ave|Road|Rd)\b)|"
    r"(?P<INVOICE>Invoice\s*#?\s*\d+)",
    flags=re.IGNORECASE
)


def remove_sensitive_data(text):
    """
    Removes sensitive data from the text by replacing it with a placeholder.
    Also collects and returns the sensitive data that was removed.

    Returns:
        sanitized_text (str): Text with sensitive info replaced by placeholders.
        sensitive_info (dict): Dictionary with keys as sensitive types and values as lists
                               of the sensitive items that were removed.
    """
    sensitive_info = {}

    def replace(match):
        data_type = match.lastgroup.upper()  # e.g., 'EMAIL', 'PHONE'
        matched_text = match.group()
        sensitive_info.setdefault(data_type, []).append(matched_text)
        return f'[{data_type}]'

    sanitized_text = SENSITIVE_PATTERN.sub(replace, text)
    return sanitized_text, sensitive_info


import re
import spacy
import ftfy

# Precompile regex patterns for efficiency.
PAGE_PATTERN = re.compile(r'(?i)\bpage\s+\d+\s+of\s+\d+\b')
WHITESPACE_PATTERN = re.compile(r'\s+')
MIN_WORDS = 3

# Load spaCy model with only the essential components for sentence segmentation.
# (Disabling 'ner', 'tagger', and 'lemmatizer' can speed up processing.)
nlp = spacy.load("en_core_web_sm", disable=["ner", "tagger", "lemmatizer"])


def clean_text(raw_text: str) -> str:
    # Normalize text: fix encoding and collapse whitespace.
    text = ftfy.fix_text(raw_text)
    text = WHITESPACE_PATTERN.sub(' ', text)

    # Use spaCy to segment text into sentences.
    doc = nlp(text)
    # Filter out sentences that appear to be page headers/footers or are too short.
    sentences = [
        sent.text.strip()
        for sent in doc.sents
        if not PAGE_PATTERN.search(sent.text) and len(sent.text.strip().split()) >= MIN_WORDS
    ]
    return " ".join(sentences)


# Example usage:
if __name__ == "__main__":
    raw_text = """
    B R A N C H  -  P O R T L A N D
    P O R T L A N D ,  O R  9 7 2 1 7
    ( 5 0 3 )  2 8 3 - 3 3 3 3
    ALL BIDDERS

    Nobody expects more from us than we do©
    Bid ID: BPLB20240300013264
    Issue Date: 09/27/2024
    Version: 1.00
    Job Name: America`s Best Contacts & Eyeglasses - SW Pacific Hwy Tigard OR
    Salesperson: ...
    Location: 16200 SW Pacific Hwy, Tigard, OR 97224, USA

    PAGE 1 of 8
    PN   IMAGE   MFC   DESCRIPTION   SPEC   QTY   U/M   UNIT PRICE   EXT PRICE
    NOTE: THIS QUOTE HAS BEEN PREPARED BASED ON OUR INTERPRETATION OF THE INFORMATION PROVIDED.
    ...
    PAGE 8 of 8
    TERMS AND CONDITIONS
    ...
    """
    cleaned = clean_text(raw_text)

import re


def get_original_filename(sanitized_filename: str) -> str:
    """
    Reconstructs the original filename.

    If the filename has a UUID prefix (e.g., "4bf4aee4-949b-4fdf-8e13-c406b4a87879_"),
    it is removed. Then the last underscore in the remaining string is replaced with a dot,
    turning, for example, "invoice_S012182476_001_pdf" into "invoice_S012182476_001.pdf".

    Args:
        sanitized_filename (str): The sanitized filename to be converted.

    Returns:
        str: The reconstructed original filename.
    """
    if not sanitized_filename:
        return 'unknown.unknown'

    # Regex to match a UUID followed by an underscore.
    uuid_prefix_regex = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}_', re.IGNORECASE)

    # Remove the UUID prefix if it exists.
    sanitized_filename = uuid_prefix_regex.sub('', sanitized_filename)

    # Find the last underscore to identify the extension separator.
    last_underscore_index = sanitized_filename.rfind('_')

    # If found, replace the last underscore with a dot.
    if last_underscore_index != -1:
        return sanitized_filename[:last_underscore_index] + '.' + sanitized_filename[last_underscore_index + 1:]

    # If no underscore is found, return the filename as-is.
    return sanitized_filename
