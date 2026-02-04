import asyncio
import random  # retained if you reintroduce simulated steps later
from typing import Dict, Any, List, Tuple, Optional

import fitz
import httpx
# import spacy  # unused in this file; keep commented if you don't need it
import openai
import pandas as pd
import os
from werkzeug.utils import secure_filename
from pdf2image import convert_from_path
from google.cloud import vision
from io import BytesIO
from pdf2image import convert_from_bytes
import pytesseract
import logging
import re
import json
from firebase_admin import firestore as admin_fs

from masyg_extractor.services.firestore_helpers import get_firestore_client
from masyg_extractor.services.my_log import logger, send_log
from masyg_extractor.services.progress_log import ExtractorProgressLog
from masyg_extractor.utils.extensions import sio
from masyg_extractor.utils.tool import clean_text

# -----------------------------------------------------------------------------
# Config
# -----------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO)

openai.api_key = api_key = os.environ.get("FILE_EXTRACTOR_API_KEY")
if not openai.api_key:
    raise KeyError("FILE_EXTRACTOR_API_KEY not set in environment variables.")

# Ellipsis guard
ELLIPSIS_TYPES = (type(Ellipsis),)


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def parse_json_to_dataframe(
    json_content,
    fallback_columns=["item_id", "date", "vendor_name", "description", "quantity", "unit_price"],
):
    """Safely parse a JSON string into a Pandas DataFrame."""
    try:
        data = json.loads(json_content)
        return pd.DataFrame(data)
    except json.JSONDecodeError as e:
        logging.error(f"JSON decoding error: {e}")
        logging.error(f"JSON content received:\n{json_content}")
        with open("invalid_json_output.json", "w") as f:
            f.write(json_content)
        return pd.DataFrame(columns=fallback_columns)
    except Exception as e:
        logging.error(f"Unexpected JSON parsing error: {e}")
        return pd.DataFrame(columns=fallback_columns)


def extract_text_from_pdf_image(file):
    """
    Extract raw text from a PDF file-like object using Google Cloud Vision API.
    """
    client = vision.ImageAnnotatorClient()

    file_content = file.read()
    images = convert_from_bytes(
        file_content,
        poppler_path="/usr/local/bin" if os.getenv("FAST_API_ENV") == "development" else "/usr/bin",
    )

    extracted_text = ""
    for image in images:
        image_byte_array = BytesIO()
        image.save(image_byte_array, format="JPEG")
        image_content = image_byte_array.getvalue()

        vision_image = vision.Image(content=image_content)
        response = client.text_detection(image=vision_image)

        if response.error.message:

            return ""

        texts = response.text_annotations
        extracted_text += texts[0].description if texts else ""

    return extracted_text


async def extract_text_with_ocr_space(uploaded_file, api_key="K84148755688957"):
    """
    Extract text using OCR.Space (async).
    """
    ocr_url = "https://api.ocr.space/parse/image"
    logger.info(f"🔄 Trying OCR on {uploaded_file.filename}")

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            files = {"file": (uploaded_file.filename, uploaded_file.stream, uploaded_file.mimetype)}
            data = {"apikey": api_key, "language": "eng", "isOverlayRequired": False}
            response = await client.post(ocr_url, files=files, data=data)

        response.raise_for_status()
        result = response.json()

        if not result.get("ParsedResults"):
            logger.info(f"❌ OCR returned no results for {uploaded_file.filename}")
            return ""

        extracted_text = " ".join(res.get("ParsedText", "") for res in result["ParsedResults"])
        logger.info(f"✅ OCR succeeded for {uploaded_file.filename}")
        return extracted_text.strip()

    except httpx.ReadTimeout:
        logger.info(f"❌ OCR request timed out for {uploaded_file.filename}")
    except httpx.HTTPStatusError as e:
        logger.info(f"❌ OCR HTTP error ({e.response.status_code}) for {uploaded_file.filename}")
    except Exception as e:
        logging.exception("Unexpected error in OCR extraction")
        logger.info(f"❌ Error processing {uploaded_file.filename}: {e}")

    return ""


def extract_text_from_pdf(file):
    """
    Extract all text from a PDF file using PyMuPDF (fitz).
    """
    try:
        pdf_data = file.read()
        file.seek(0)
        with fitz.open(stream=pdf_data, filetype="pdf") as doc:
            all_text = []
            for page_index, page in enumerate(doc, start=1):
                page_text = page.get_text()
                if page_text:
                    all_text.append(page_text)
                else:
                    logging.warning(
                        f"No text found on page {page_index} of {getattr(file, 'filename', 'unknown')}"
                    )
            return "\n".join(all_text)
    except Exception as e:
        logging.error(f"Error extracting text from PDF: {e}")
        return ""


def extract_text_from_scanned_pdf(file):
    """
    Extract text from a scanned PDF file using Tesseract OCR.
    """
    try:
        filename = secure_filename(file.filename)
        temp_file_path = os.path.join("/tmp", filename)
        file.save(temp_file_path)

        images = convert_from_path(temp_file_path)

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

        os.remove(temp_file_path)

        if all_text:
            return "\n\n".join(all_text)
        else:
            logging.warning(f"No text found in scanned PDF: {filename}")
            return ""

    except Exception as e:
        logging.error(f"Error extracting text from scanned PDF {file.filename}: {e}")
        return ""


def extract_json_from_code_block(text: str):
    """
    Tolerant JSON extraction:
      - accepts ```json ... ``` or ``` ... ```
      - tolerates trailing commas
    """
    m = re.search(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", text, re.DOTALL | re.IGNORECASE)
    if not m:
        m = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
        if not m:
            logging.error("No JSON fenced block found")
            return None
    json_str = m.group(1).strip()
    # remove trailing commas before } or ]
    json_str = re.sub(r",\s*([}\]])", r"\1", json_str)
    try:
        return json.loads(json_str)
    except Exception as e:
        logging.error(f"Failed to parse JSON: {e}")
        return None


def process_text_with_regex(pdf_text: str):
    """
    Extract tabular-looking lines using regex as a last-resort fallback.
    Fields: ProductId, Description, Quantity, Price, Ext_Price
    """
    try:
        pattern = re.compile(
            r"(?P<ProductId>\d{3,})\s+(?P<Description>[\w\s]+?)\s+(?P<Qty>\d+)\s+"
            r"(?P<Price>\d+\.\d{2})\s+(?P<Ext_Price>\d+\.\d{2})"
        )

        matches = pattern.finditer(pdf_text)
        extracted_data = []

        for match in matches:
            extracted_data.append(
                {
                    "ProductId": match.group("ProductId"),
                    "Description": match.group("Description").strip(),
                    "Quantity": int(match.group("Qty")),
                    "Price": float(match.group("Price")),
                    "Ext_Price": float(match.group("Ext_Price")),
                }
            )

        return json.dumps(extracted_data, indent=2) if extracted_data else None
    except Exception as e:
        logging.error(f"Error processing text with regex: {type(e).__name__}: {e}")
        return None


def remove_non_alphanumeric(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9 ]", "", text)


# -----------------------------------------------------------------------------
# GPT Orchestration (fast, tolerant, sanitized)
# -----------------------------------------------------------------------------
def _s(v) -> str:
    """Sanitize any value into a safe string for the OpenAI API."""
    try:
        if v is None or v is Ellipsis or isinstance(v, ELLIPSIS_TYPES):
            return ""
        return str(v)
    except Exception:
        return ""


async def _gpt_call(messages: List[Dict[str, str]]):
    """Threaded OpenAI call; returns assistant content or None."""
    model = "gpt-4o-mini"  # strong + fast; use consistently in dev/prod
    try:
        resp = await asyncio.to_thread(
            openai.ChatCompletion.create,
            model=model,
            messages=messages,
            max_tokens=1500,
            temperature=0.0,
            n=1,
        )
        return resp["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.warning(f"OpenAI call failed: {e}")
        return None


async def process_chunk(chunk_text: str, progress_logger: ExtractorProgressLog, file_progress_share: float):
    """
    Process one chunk with GPT quickly (no artificial sleeps).
    Returns parsed JSON (dict) or None.
    """
    SYSTEM_PROMPT = (
        "You extract structured purchasing documents. Supported documentType values include: "
        "invoice, bill, receipt, quote, bid, estimate, purchase_order, statement. "
        "Output a single JSON object in a code block. "
        "Required top-level keys: documentType, vendor_name (string or null), date (string or null), "
        "due_date (string or null), line_items (array). "
        "Each line_items entry should include: item_name (<=3 words if synthesized), category (string or null), "
        "description (string), quantity (number or string), tax ('NON' or 'TAX' if visible else 'NON'), "
        "sku (string; generate like 'TLP-45035' if absent), unit_price (number as string, no currency symbol). "
        "If the PDF is a quote/bid/estimate/purchase order, still map items into line_items. "
        "If some fields are not present, return null for them instead of failing. "
        "Return ONLY one JSON object inside a fenced code block."
    )

    messages = [
        {"role": "system", "content": _s(SYSTEM_PROMPT)},
        {"role": "user", "content": _s(f"Extract the data from this text:\n\n{chunk_text}\n\nReturn JSON only.")},
    ]

    # one-shot fast call (retry light)
    max_retries = 2
    base_delay = 0.75
    for attempt in range(max_retries):
        assistant_text = await _gpt_call(messages)
        if assistant_text:
            logger.debug("GPT RAW:\n" + assistant_text[:4000])
            json_content = extract_json_from_code_block(assistant_text)
            if json_content:
                return json_content
        if attempt < max_retries - 1:
            await asyncio.sleep(base_delay * (2 ** attempt))

    return None


async def process_text_with_gpt(
    pdf_text: str,
    progress_logger: ExtractorProgressLog,
    progress: Dict[str, float],
    files_count: int,
        file_id: Optional[str] = None,  # <-- add this

) -> Any:
    """
    Process the entire text with GPT.
    """
    pdf_text = clean_text(pdf_text)
    if pdf_text is Ellipsis or isinstance(pdf_text, ELLIPSIS_TYPES):
        pdf_text = ""
    if len(pdf_text) <= 0:
        return None

    gpt_stage_weight = ExtractorProgressLog.GPT_PROCESSING_WEIGHT

    # Single chunk
    if len(pdf_text) <= 1500:
        result = await process_chunk(pdf_text, progress_logger, gpt_stage_weight)
        progress["gpt_processing"] = gpt_stage_weight
        await _emit_progress_resilient(progress_logger, progress)

        return result

    # Multiple chunks
    chunks = [pdf_text[i : i + 1500] for i in range(0, len(pdf_text), 1500)]
    if not chunks:
        return None

    chunk_share = gpt_stage_weight / len(chunks)
    tasks = [process_chunk(chunk, progress_logger, chunk_share) for chunk in chunks]
    results = await asyncio.gather(*tasks)

    progress["gpt_processing"] = gpt_stage_weight
    await _emit_progress_resilient(progress_logger, progress)

    results = [r for r in results if r is not None]
    if not results:
        return None

    # Merge results (first + extend line_items)
    combined_result = results[0]
    for result in results[1:]:
        if isinstance(result, dict) and "line_items" in result and isinstance(
            result.get("line_items"), list
        ):
            combined_result.setdefault("line_items", [])
            combined_result["line_items"].extend(result.get("line_items", []))

    return combined_result



from typing import Optional

def _calc_overall(progress: Dict[str, float]) -> float:
    try:
        return float(sum(progress.values()))
    except Exception:
        return 0.0

async def _emit_progress_resilient(
    progress_logger,
    progress: Dict[str, float],
    *,
    threshold: float = 1.0,
    file_id: Optional[str] = None,
):
    """
    Best-effort progress emitter:
    - If progress_logger has `safe_emit_progress`, call it (try both old/new signatures).
    - Otherwise, emit directly via socket.io with a reasonable default payload.
    """
    overall = _calc_overall(progress)

    # 1) Try the nice path (method exists)
    if hasattr(progress_logger, "safe_emit_progress"):
        try:
            # Old signature: safe_emit_progress(progress_value, threshold=...)
            return await progress_logger.safe_emit_progress(overall, threshold=threshold)
        except TypeError:
            # Newer signature in some codebases: safe_emit_progress(progress_value, threshold=..., file_id=...)
            try:
                return await progress_logger.safe_emit_progress(overall, threshold=threshold, file_id=file_id)
            except Exception:
                pass
        except Exception:
            pass

    # 2) Fallback: emit directly
    try:
        room = getattr(progress_logger, "client_id", "Guest")
        log_key = getattr(progress_logger, "log_key", "data-progress")
        payload = {"progress": overall}
        if file_id is not None:
            payload["file_id"] = file_id
        await sio.emit(log_key, payload, room=room)
    except Exception:
        # Swallow errors to avoid crashing the pipeline on progress-only failures.
        logger.debug("Progress emit fallback failed; continuing without emitting.")
# safe_emit_progress



# ── record a failed file into Firestore (no refactor, safe to call anywhere) ──
async def record_failed_file(
    user_id: str,
    group_id: str,
    filename: str,
    error_message: str,
    *,
    stage: str | None = None,
) -> str:
    """Creates/merges a file doc with status=failed so UI can show it."""
    from masyg_extractor.utils.filename_utils import sanitize_generate_unique_filename
    client = await get_firestore_client()
    file_id = sanitize_generate_unique_filename(filename)

    fref = (
        client.collection("users")
        .document(user_id)
        .collection("groups")
        .document(group_id)
        .collection("files")
        .document(file_id)
    )

    data = {
        # "status": "failed",
        # "error": error_message,
        # "stage": stage,                       # e.g., "pipeline", "text_extraction", "gpt_parse"
        # "original_filename": filename,
        # "trashed": False,
        # "createdAt": admin_fs.SERVER_TIMESTAMP,

        "status": "failed",
        "original_filename": filename,
        "error": error_message,
        "stage": stage,
        "trashed": False,
        "createdAt":  admin_fs.SERVER_TIMESTAMP,
        "updatedAt":  admin_fs.SERVER_TIMESTAMP,
    }

    # use set(..., merge=True) to avoid requiring an existing doc
    await asyncio.to_thread(fref.set, data, True)
    return file_id
