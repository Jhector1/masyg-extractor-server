import asyncio
import io

import json
from typing import Tuple, Any, List, Dict

from firebase_admin import firestore

from masyg_extractor.services.my_log import send_log, logger
from masyg_extractor.services.progress_log import ExtractorProgressLog
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







async def extract_text(
    file_bytes: bytes, file_type: str, uploaded_file, progress_logger: ExtractorProgressLog, progress: Dict[str, float], files_count: int
) -> Tuple[str, str]:
    extractors = get_extractor_list(file_type)
    extracted_text = ""
    extractor_used = ""
    extractor_index = 0
    # progress["text_extraction"] = 0.0
    # await safe_emit_progress(client_id, calculate_overall_progress(progress))
    extraction_steps = 5
    print(f"text_extraction...")
    for step in range(extraction_steps):
        await asyncio.sleep(0.3)
        progress["text_extraction"] = ((step + 1) / extraction_steps) * ExtractorProgressLog.TEXT_EXTRACTION_WEIGHT
        await progress_logger.safe_emit_progress(progress_logger. calculate_overall_progress(progress))

    while extractor_index < len(extractors):
        logger.info(f"Trying extractor: {extractors[extractor_index].__name__}")
        extractor = extractors[extractor_index]
        if extractor.__name__ in ["extract_text_with_ocr_space", "extract_text_from_scanned_pdf", "extract_text_from_pdf_camelot"]:
            fake_file = FakeUploadFile(uploaded_file.filename, file_bytes, uploaded_file.content_type)
            if asyncio.iscoroutinefunction(extractor):
                extracted_text = await extractor(fake_file)
            else:
                extracted_text = await asyncio.to_thread(extractor, fake_file)
        else:
            file_obj = io.BytesIO(file_bytes)
            if asyncio.iscoroutinefunction(extractor):
                extracted_text = await extractor(file_obj)
            else:
                extracted_text = await asyncio.to_thread(extractor, file_obj)
        if asyncio.iscoroutine(extracted_text):
            extracted_text = await extracted_text
        if not isinstance(extracted_text, str):
            logger.error("Extracted text is not a string; converting it to string.")
            extracted_text = str(extracted_text)
        extracted_text, _ = await asyncio.to_thread(remove_sensitive_data, extracted_text)
        # progress["text_extraction"] = ((extractor_index + 1) / len(extractors)) * 100
        # await safe_emit_progress(client_id, calculate_overall_progress(progress))
        if extracted_text and extracted_text.strip():
            extractor_used = extractor.__name__.upper()
            logger.info(f"Text extraction succeeded with {extractor_used}")
            break
        extractor_index += 1
    progress["text_extraction"] = ExtractorProgressLog.TEXT_EXTRACTION_WEIGHT
    await progress_logger.safe_emit_progress(progress_logger.calculate_overall_progress(progress))
    return extracted_text, extractor_used


import inspect


async def process_text_and_parse(
    text: str,
        file_bytes: bytes,
        uploaded_file, extractors: List[Any],
        progress_loger: ExtractorProgressLog,
        progress: Dict[str, float],
        files_count: int
) -> dict:
    """
    Process text by cleaning, removing sensitive data, and then running GPT.
    Assume text extraction (from extract_text) sets progress to 100% for that stage.
    Here, GPT processing (handled by process_text_with_gpt) is another stage.
    """
    # Let’s assume text extraction stage is 20% of overall progress.
    extractor_index = 0
    parsed_content = None
    # Mark that text cleaning is complete (e.g. 50% for text extraction stage)
    # progress["text_extraction"] = 20.0
    await progress_loger.safe_emit_progress(progress_loger.calculate_overall_progress(progress))
    while extractor_index < len(extractors):
        try:
            text, _ = await asyncio.to_thread(remove_sensitive_data, text)
            logger.info("Sensitive data removed.")
            json_content = await process_text_with_gpt(text, progress_loger, progress, files_count)
            if json_content is None:
                logger.error("No valid JSON response received from GPT API.")
                raise ValueError("Text processing failed")
            parsed_content = json_content
            logger.info(f"Parsed JSON content: {parsed_content}")
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
                if extractor.__name__ in ["extract_text_with_ocr_space", "extract_text_from_scanned_pdf",
                                          "extract_text_from_pdf_camelot"]:
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
                # asyncio.create_task(send_log("⚠️ No more extractors to retry", user_room=client_id))
                logger.warning("No more extractors to retry.")
                raise ValueError("❌ Text processing failed with all extractors")
    progress["gpt_processing"] = ExtractorProgressLog.GPT_PROCESSING_WEIGHT
    await progress_loger.safe_emit_progress( progress_loger.calculate_overall_progress(progress))
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
    uploaded_file, user_id: str, group_id: str, progress_logger: ExtractorProgressLog, progress: Dict[str, float], files_count: int

) -> Tuple[Dict[str, Any], str]:
    try:
        logger.info(f"Processing file: {uploaded_file.filename}")
        # asyncio.create_task(send_log(f"⚙️ Processing file: {uploaded_file.filename}...", user_room=client_id))
        # progress["file_read"] = 0.0
        await progress_logger.safe_emit_progress( progress_logger.calculate_overall_progress(progress))
        filename_lower = uploaded_file.filename.lower()
        if filename_lower.endswith(('.png', '.jpg', '.jpeg', '.bmp', '.tiff')):
            file_type = 'image'
        elif filename_lower.endswith('.pdf'):
            file_type = 'pdf'
        else:
            # asyncio.create_task(send_log("❌ Unsupported file type", user_room=client_id))
            return {'error': 'Unsupported file type'}, uploaded_file.filename

        await uploaded_file.seek(0)
        file_bytes = await uploaded_file.read()
        if not file_bytes:
            logger.error("Uploaded file is empty!")
            return {'error': 'Empty file'}, uploaded_file.filename
        # progress["file_read"] = 20.0
        # await safe_emit_progress(client_id, calculate_overall_progress(progress))
        read_steps = 5*files_count
        print(f"file_read...")
        for step in range(read_steps):
            await asyncio.sleep(0.2)
            progress["file_read"] = ((step + 1) / read_steps) * ExtractorProgressLog.FILE_READ_WEIGHT
            await progress_logger.safe_emit_progress(progress_logger.calculate_overall_progress(progress))

        extracted_text, extractor_used = await extract_text(file_bytes, file_type, uploaded_file, progress_logger, progress, files_count)
        if not extracted_text or not extracted_text.strip():
            # asyncio.create_task(send_log(f"⚠️ No text extracted from: {uploaded_file.filename}", user_room=client_id))
            logger.warning(f"No text extracted from: {uploaded_file.filename}")
            return {'error': 'Text extraction failed'}, uploaded_file.filename
        extractors = get_extractor_list(file_type)
        parsed_content = await process_text_and_parse(extracted_text, file_bytes, uploaded_file,extractors, progress_logger, progress, files_count)
        if len(parsed_content.get('line_items', [])) == 0:
            return {'error': 'error while processing file'}, uploaded_file.filename
        logger.info(f"Final successful extractor used: {extractor_used}")

        comp_steps = 5*files_count
        print(f"compression...")
        for step in range(comp_steps):
            await asyncio.sleep(0.2)
            progress["compression"] = ((step + 1) / comp_steps) * ExtractorProgressLog.COMPRESSION_WEIGHT
            await progress_logger.safe_emit_progress( progress_logger.calculate_overall_progress(progress))

        from masyg_extractor.utils.filename_utils import sanitize_generate_unique_filename  # Replace with your actual import.
        sanitized_filename = sanitize_generate_unique_filename(uploaded_file.filename)
        await update_firestore_file(user_id, group_id, sanitized_filename, parsed_content)

        fs_steps = 5*files_count
        print(f"firestore_update...")
        for step in range(fs_steps):
            await asyncio.sleep(0.2)
            progress["firestore_update"] = ((step + 1) / fs_steps) * ExtractorProgressLog.FIRESTORE_UPDATE_WEIGHT
            await progress_logger.safe_emit_progress( progress_logger.calculate_overall_progress(progress))

        return parsed_content, sanitized_filename
    except Exception as e:
        # asyncio.create_task(send_log(f"❌ Error processing file: {uploaded_file.filename}", user_room=client_id))
        logger.exception(f"Error processing file: {uploaded_file.filename}")
        return {'error': str(e)}, uploaded_file.filename

async def process_file_wrapper(
    idx: int, file, user_id: str, group_id: str, progress_logger: ExtractorProgressLog, global_progress: Dict[str, float], files_count: int
) -> Tuple[int, str, Any]:
    parsed_content, sanitized_filename = await process_file_async(file, user_id, group_id, progress_logger, global_progress, files_count)
    return idx, sanitized_filename, parsed_content

async def process_files_in_parallel(
    files: List, user_id: str, group_id: str, progress_logger: ExtractorProgressLog, global_progress: Dict[str, float]
) -> Dict[int, Any]:
    total_files = len(files)
    file_progress_trackers = {idx: ExtractorProgressLog.get_file_progress_dict() for idx in range(total_files)}

    tasks = [
        process_file_wrapper(idx, f, user_id, group_id, progress_logger, global_progress, total_files)
        for idx, f in enumerate(files)
    ]
    results_list = await asyncio.gather(*tasks, return_exceptions=True)
    results = {}
    # Update overall progress based on number of files completed
    # Compute overall progress as the average over files.
    overall_progress = sum(progress_logger.calculate_overall_progress(fp) for fp in file_progress_trackers.values()) / total_files
    await progress_logger.safe_emit_progress(overall_progress)
    await asyncio.sleep(0.5)
    for idx, res in enumerate(results_list):
        # overall_batch_progress = calculate_overall_progress(global_progress)
        # await safe_emit_progress(client_id, overall_batch_progress)
        # await asyncio.sleep(0.5)
        if isinstance(res, Exception):
            results[idx] = {'error': str(res)}
        else:
            index, sanitized_filename, parsed_content = res
            results[index] = {
                'sanitized_filename': sanitized_filename,
                'parsed_content': parsed_content
            }
    return results

