import asyncio
import io

import json
from typing import Tuple, Any, List, Dict

from firebase_admin import firestore
from masyg_extractor.services.my_log import send_log, logger
from masyg_extractor.utils.extensions import sio
from masyg_extractor.utils.helper import FakeUploadFile
from masyg_extractor.utils.tool import remove_sensitive_data
from masyg_extractor.services.image_extractor_service import extract_text_from_image
from masyg_extractor.services.file_extractor_service import (
    extract_text_from_pdf,
    extract_text_from_pdf_image,
    extract_text_with_ocr_space,
    extract_text_from_scanned_pdf,
    extract_text_from_pdf_camelot,
    process_text_with_gpt,
)

# Firestore client
firestore_db = firestore.client()

def get_extractor_list(file_type: str) -> List[Any]:
    """Return list of extractors based on file type."""
    if file_type == 'pdf':
        return [
            extract_text_from_pdf,
            extract_text_from_pdf_image,
            extract_text_with_ocr_space,
            extract_text_from_scanned_pdf,
            extract_text_from_pdf_camelot,
        ]
    return [extract_text_from_image]


async def extract_text(file_bytes: bytes, file_type: str, uploaded_file, client_id: str) -> Tuple[str, str]:
    extractors = get_extractor_list(file_type)
    extracted_text = ""
    extractor_used = ""
    extractor_index = 0

    while extractor_index < len(extractors):

        logger.info(f"🔄Trying extractor: {extractors[extractor_index].__name__}")
        # asyncio.create_task(
        #     send_log(f"🔄Trying extractor: {extractors[extractor_index].__name__}", user_room=client_id))

        extractor = extractors[extractor_index]
        if extractor.__name__ in ["extract_text_with_ocr_space", "extract_text_from_scanned_pdf",
                                  "extract_text_from_pdf_camelot"]:
            fake_file = FakeUploadFile(uploaded_file.filename, file_bytes, uploaded_file.content_type)
            if inspect.iscoroutinefunction(extractor):
                extracted_text = await extractor(fake_file)
            else:
                extracted_text = await asyncio.to_thread(extractor, fake_file)
        else:
            file_obj = io.BytesIO(file_bytes)
            if inspect.iscoroutinefunction(extractor):
                extracted_text = await extractor(file_obj)
            else:
                extracted_text = await asyncio.to_thread(extractor, file_obj)

        if asyncio.iscoroutine(extracted_text):
            extracted_text = await extracted_text

        # Log the type for debugging
        logger.info(f"Type of extracted_text before sanitization: {type(extracted_text)}")
        if not isinstance(extracted_text, str):
            logger.error("Extracted text is not a string; converting it to string.")
            extracted_text = str(extracted_text)

        # Remove sensitive data
        extracted_text, _ = await asyncio.to_thread(remove_sensitive_data, extracted_text)

        if extracted_text and extracted_text.strip():
            extractor_used = extractor.__name__.upper()
            # asyncio.create_task(
            #     send_log(f"✅ Text extraction succeeded with {extractor_used}", user_room=client_id))
            logger.info(f"Text extraction succeeded with {extractor_used}")
            break

        extractor_index += 1

    return extracted_text, extractor_used


import inspect

async def process_text_and_parse(
    text: str, file_bytes: bytes, uploaded_file, extractors: list, client_id: str
) -> dict:
    extractor_index = 0
    parsed_content = None

    while extractor_index < len(extractors):
        try:
            # Remove sensitive data
            text, _ = await asyncio.to_thread(remove_sensitive_data, text)
            logger.info("Sensitive data removed.")

            json_content = await process_text_with_gpt(text)
            if json_content is None:
                logger.error("No valid JSON response received from GPT API.")
                raise ValueError("Text processing failed")
            parsed_content = json_content
            logger.info(f"Parsed JSON content: {parsed_content}")

            # Validate required fields.
            if not isinstance(parsed_content.get('line_items'), list):
                raise ValueError("Invalid JSON format: 'line_items' is not a list")
            if 'vendor_name' not in parsed_content or 'date' not in parsed_content:
                raise ValueError("Invalid JSON format: Missing 'vendor_name' or 'date'")
            break

        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"Error parsing JSON with extractor {extractors[extractor_index].__name__}: {e}")
            extractor_index += 1

            if extractor_index < len(extractors):
                extractor = extractors[extractor_index]
                # For extractors that need file attributes, use FakeUploadFile.
                if extractor.__name__ in ["extract_text_with_ocr_space", "extract_text_from_scanned_pdf", "extract_text_from_pdf_camelot"]:
                    fake_file = FakeUploadFile(uploaded_file.filename, file_bytes, uploaded_file.content_type)
                    if inspect.iscoroutinefunction(extractor):
                        text = await extractor(fake_file)
                    else:
                        text = await asyncio.to_thread(extractor, fake_file)
                else:
                    # For other extractors, create a new BytesIO object.
                    new_file_obj = io.BytesIO(file_bytes)
                    if inspect.iscoroutinefunction(extractor):
                        text = await extractor(new_file_obj)
                    else:
                        text = await asyncio.to_thread(extractor, new_file_obj)
            else:
                asyncio.create_task(send_log("⚠️ No more extractors to retry", user_room=client_id))
                logger.warning("No more extractors to retry.")
                raise ValueError("❌ Text processing failed with all extractors")
    return parsed_content

async def update_firestore_file(user_id: str, group_id: str, sanitized_filename: str, parsed_content: Dict[str, Any]) -> None:
    """
    Update Firestore with the parsed content for a given file.
    """
    from masyg_extractor.utils.filename_utils import sanitize_generate_unique_filename  # local import to avoid circular dependency
    # sanitized_filename = sanitize_generate_unique_filename(filename)
    file_doc_ref = (
        firestore_db.collection("users")
        .document(user_id)
        .collection("groups")
        .document(group_id)
        .collection("files")
        .document(sanitized_filename)
    )
    file_doc_ref.set(parsed_content)

async def process_file_async(
    uploaded_file, user_id: str, group_id: str, client_id: str
) -> Tuple[Dict[str, Any], str]:
    """
    Main asynchronous file processing function.
    Reads the file, determines file type, extracts text, processes text and updates Firestore.
    Returns the parsed content and a sanitized filename.
    """
    try:
        logger.info(f"Processing file: {uploaded_file.filename}")
        asyncio.create_task(
            send_log(f"⚙️ Processing file: {uploaded_file.filename}...", user_room=client_id))
        filename_lower = uploaded_file.filename.lower()
        if filename_lower.endswith(('.png', '.jpg', '.jpeg', '.bmp', '.tiff')):
            file_type = 'image'
        elif filename_lower.endswith('.pdf'):
            file_type = 'pdf'
        else:
            asyncio.create_task(
                send_log("❌ Unsupported file type", user_room=client_id))
            return {'error': 'Unsupported file type'}, uploaded_file.filename
        await uploaded_file.seek(0)

        # Read the file bytes from the upload (this is non-empty as per your log)
        file_bytes = await uploaded_file.read()
        if not file_bytes:
            logger.error("Uploaded file is empty!")
            return {'error': 'Empty file'}, uploaded_file.filename

        # For each extraction attempt, create a new BytesIO from file_bytes
        extracted_text, extractor_used = await extract_text(file_bytes, file_type,uploaded_file, client_id)

        if not extracted_text or not extracted_text.strip():
            asyncio.create_task(
                send_log(f"⚠️ No text extracted from: {uploaded_file.filename}", user_room=client_id))
            logger.warning(f"No text extracted from: {uploaded_file.filename}")
            return {'error': 'Text extraction failed'}, uploaded_file.filename

        extractors = get_extractor_list(file_type)

        # Create a new BytesIO for further processing in process_text_and_parse
        file_obj = io.BytesIO(file_bytes)
        parsed_content = await process_text_and_parse(extracted_text, file_bytes, uploaded_file, extractors, client_id)

        if len(parsed_content['line_items']) == 0:
            return {'error': 'error while processing file'}, uploaded_file.filename

        # if len(parsed_content.line_items) <=0:
        #     return {'error': 'error while processing file'}, uploaded_file.filename
        logger.info(f"Final successful extractor used: {extractor_used}")
        from masyg_extractor.utils.filename_utils import sanitize_generate_unique_filename


        sanitized_filename = sanitize_generate_unique_filename(uploaded_file.filename)
        await update_firestore_file(user_id, group_id, sanitized_filename, parsed_content)
        return parsed_content, sanitized_filename

    except Exception as e:
        asyncio.create_task(send_log(f"❌ Error processing file: {uploaded_file.filename}", user_room=client_id))
        logger.exception(f"Error processing file: {uploaded_file.filename}")
        return {'error': str(e)}, uploaded_file.filename

async def process_file_wrapper(idx: int, file, user_id: str, group_id: str, client_id: str) -> Tuple[int, str, Any]:
    parsed_content, sanitized_filename = await process_file_async(file, user_id, group_id, client_id)
    return idx, sanitized_filename, parsed_content

async def process_files_in_parallel(
    files: List, user_id: str, group_id: str, client_id: str
) -> Dict[int, Any]:
    """
    Process multiple files concurrently.
    Returns a dictionary mapping file index to its processing result.
    """
    tasks = [
        process_file_wrapper(idx, f, user_id, group_id, client_id)
        for idx, f in enumerate(files)
    ]
    results_list = await asyncio.gather(*tasks, return_exceptions=True)
    results = {}
    step = 100 / len(files)
    for idx, res in enumerate(results_list):

        await sio.emit("progress_update", {"progress":( idx+1)* step}, room=client_id)
        await asyncio.sleep(1)

        if isinstance(res, Exception):
            logger.exception(f"Error processing file at index {idx}: {res}")
            results[idx] = {'error': str(res)}
        else:
            index, sanitized_filename, parsed_content = res
            results[index] = {
                'sanitized_filename': sanitized_filename,
                'parsed_content': parsed_content
            }
    return results
