import base64
import io
import json
import logging
import math
from datetime import datetime
from http.client import HTTPException

from fastapi import APIRouter, Request, File, UploadFile, Depends, status
from typing import List, Dict, Tuple, Any
import asyncio
import random

from masyg_extractor.services.my_log import send_log
from masyg_extractor.utils.extensions import sio

# Assume that this is your Socket.IO instance already configured.

router = APIRouter()


# Dummy dependency to simulate firebase user extraction
async def get_firebase_user():
    # Simulated user data; in real use, extract from the token or request
    return {"client_id": "dummy_client_room"}


@router.post("/dummy_endpoint", status_code=status.HTTP_201_CREATED)
async def dummy_extract_data(
        request: Request,
        files: List[UploadFile] = File(...),
        firebase_user: dict = Depends(get_firebase_user)
):
    client_id = request.session.get("client_id")
    total_files = len(files)
    total_steps = 5  # Number of inner loop iterations per file

    # Initialize a dictionary to track progress (fraction) for each file.
    progress_dict = {idx: 0 for idx in range(total_files)}

    async def process_file(file: UploadFile, file_index: int):
        for i in range(1, total_steps + 1):
            # Simulate some processing delay per chunk.
            await asyncio.sleep(random.uniform(0.8, 10.2))

            # Update progress for this file as a fraction (from 0 to 1).
            progress_dict[file_index] = i / total_steps

            # Calculate overall progress across all files.
            overall_progress = math.ceil((sum(progress_dict.values()) / total_files) * 100)

            # Emit progress update.
            await sio.emit(
                'data-progress',
                {'progress': overall_progress, 'file_index': file_index},
                room=client_id
            )

        # Return a dummy response for this file.
        return {
            "vendor_name": f"DUMMY VENDOR {file_index}",
            "date": "2023-01-01",
            "tax": "0",
            "line_items": [
                {
                    "item_name": "DUMMY ITEM",
                    "category": "DUMMY CATEGORY",
                    "description": "This is a dummy description",
                    "quantity": "1",
                    "unit_price": "0"
                }
            ]
        }

    # Process all files concurrently.
    tasks = [process_file(file, idx) for idx, file in enumerate(files)]
    results = await asyncio.gather(*tasks)

    # Return the combined dummy responses.
    return {"results": results}






def get_file_progress_dict() -> Dict[str, float]:
    """
    Returns a new progress dictionary for one file.
    Each stage (file_read, text_extraction, gpt_processing, compression, firestore_update)
    contributes 20% when complete.
    """
    return {
        "file_read": 0.0,
        "text_extraction": 0.0,
        "gpt_processing": 0.0,
        "compression": 0.0,
        "firestore_update": 0.0,
    }

def calculate_overall_progress(progress: Dict[str, float]) -> float:
    """
    For a single file, the overall progress is the sum of its stages.
    (Max is 100 if all stages are complete.)
    """
    return sum(progress.values())

# A dictionary to store last emitted progress per client to avoid duplicate emissions.
_last_emitted_overall: Dict[str, float] = {}

async def safe_emit_progress(client_id: str, progress_value: float, threshold: float = 1.0):
    """
        Emit a progress update that never goes backwards.
        """
    last_val = _last_emitted_overall.get(client_id, 0)
    # Ensure that the new value is at least as high as the last emitted one.
    monotonic_progress = max(progress_value, last_val)
    if abs(monotonic_progress - last_val) >= threshold:
        await sio.emit("data-progress", {"progress": monotonic_progress}, room=client_id)
        _last_emitted_overall[client_id] = monotonic_progress

# --- Dummy / Stub Implementations (Replace with Real Ones) ---

def clean_text(text: str) -> str:
    return text.strip()

def remove_sensitive_data(text: str) -> Tuple[str, Any]:
    return text, None

def compress_file_blob(file_stream: io.BytesIO, filename: str) -> io.BytesIO:
    return file_stream

def generate_group_id() -> str:
    return "group123"

class DummyFirestoreDB:
    def collection(self, name: str):
        return self
    def document(self, doc_id: str):
        return self
    def set(self, data: dict, merge: bool = False):
        logging.warning(f"Firestore set: {data}")

firestore_db = DummyFirestoreDB()

async def update_firestore_file(user_id: str, group_id: str, sanitized_filename: str, parsed_content: Dict[str, Any]) -> None:
    file_doc_ref = (
        firestore_db.collection("users")
        .document(user_id)
        .collection("groups")
        .document(group_id)
        .collection("files")
        .document(sanitized_filename)
    )
    try:
        file_doc_ref.set(parsed_content)
    except Exception as e:
        logging.error(f"Error saving data to Firestore: {e}")

def get_extractor_list(file_type: str) -> List[Any]:
    if file_type == 'pdf':
        def dummy_pdf_extractor(file_obj):
            return "Extracted PDF text"
        dummy_pdf_extractor.__name__ = "dummy_pdf_extractor"
        return [dummy_pdf_extractor]
    else:
        def dummy_image_extractor(file_obj):
            return "Extracted image text"
        dummy_image_extractor.__name__ = "dummy_image_extractor"
        return [dummy_image_extractor]

def extract_json_from_code_block(text: str) -> Any:
    try:
        return json.loads(text)
    except Exception:
        return {"vendor_name": "Dummy Vendor", "date": "2024-11-01", "tax": "0", "line_items": []}

# --- Processing Pipeline Functions ---

async def process_chunk(chunk_text: str, client_id: str, file_progress_share: float) -> Any:
    """
    Simulate GPT processing for a text chunk.
    file_progress_share: the percentage that this chunk contributes (e.g. for GPT stage, full stage = 20%).
    """
    total_steps = 5
    local_progress = 0.0
    for step in range(total_steps):
        await asyncio.sleep(random.uniform(0.8, 1.5))
        local_progress = ((step + 1) / total_steps) * 100  # progress within the chunk (0 to 100)
        overall_chunk_progress = (local_progress / 100) * file_progress_share
        await safe_emit_progress(client_id, overall_chunk_progress)
    try:
        dummy_result = {
            "vendor_name": "Dummy Vendor",
            "date": "2024-11-01",
            "tax": "0",
            "line_items": [{"item_name": "Dummy Item", "category": "Misc", "description": "Dummy", "quantity": "1", "unit_price": "0"}]
        }
        return dummy_result
    except Exception as e:
        logging.error(f"Error in process_chunk: {e}")
        return None

async def process_text_with_gpt(pdf_text: str, client_id: str, progress: Dict[str, float], files_count: int) -> Any:
    """
    Process text with GPT. The GPT stage is allocated 20% of a file's progress.
    """
    pdf_text = clean_text(pdf_text)
    if len(pdf_text) <= 0:
        return None
    gpt_stage_weight = 20.0
    if len(pdf_text) <= 1500:
        result = await process_chunk(pdf_text, client_id, gpt_stage_weight)
        progress["gpt_processing"] = gpt_stage_weight
        await safe_emit_progress(client_id, calculate_overall_progress(progress))
        return result
    chunks = [pdf_text[i:i+1500] for i in range(0, len(pdf_text), 1500)]
    chunk_share = gpt_stage_weight / len(chunks)
    tasks = [process_chunk(chunk, client_id, chunk_share) for chunk in chunks]
    results = await asyncio.gather(*tasks)
    progress["gpt_processing"] = gpt_stage_weight
    await safe_emit_progress(client_id, calculate_overall_progress(progress))
    results = [r for r in results if r is not None]
    if not results:
        return None
    combined_result = results[0]
    for result in results[1:]:
        if "line_items" in result:
            combined_result["line_items"].extend(result.get("line_items", []))
    return combined_result

async def extract_text(
    file_bytes: bytes, file_type: str, uploaded_file, client_id: str, progress: Dict[str, float], files_count: int
) -> Tuple[str, str]:
    """
    Extract text using one of several extractors.
    The text extraction stage is allocated 20% of a file's progress.
    """

    # Clear any previous progress for this client
    # _last_emitted.pop(client_id, None)
    _last_emitted_overall.pop(client_id, None)
    extractors = get_extractor_list(file_type)
    extracted_text = ""
    extractor_used = ""
    extractor_index = 0
    extraction_steps = 5
    for step in range(extraction_steps):
        await asyncio.sleep(0.3)
        progress["text_extraction"] = ((step + 1) / extraction_steps) * 20.0
        await safe_emit_progress(client_id, calculate_overall_progress(progress))
    while extractor_index < len(extractors):
        logging.warning(f"Trying extractor: {extractors[extractor_index].__name__}")
        extractor = extractors[extractor_index]
        try:
            if extractor.__name__ in ["extract_text_with_ocr_space", "extract_text_from_scanned_pdf", "extract_text_from_pdf_camelot"]:
                fake_file = uploaded_file  # Use a proper file wrapper if needed
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
                logging.error("Extracted text is not a string; converting it.")
                extracted_text = str(extracted_text)
            extracted_text, _ = await asyncio.to_thread(remove_sensitive_data, extracted_text)
            if extracted_text and extracted_text.strip():
                extractor_used = extractor.__name__.upper()
                logging.warning(f"Text extraction succeeded with {extractor_used}")
                break
        except Exception as e:
            logging.error(f"Extractor error: {e}")
        extractor_index += 1
    progress["text_extraction"] = 20.0
    await safe_emit_progress(client_id, calculate_overall_progress(progress))
    return extracted_text, extractor_used

async def process_text_and_parse(
    text: str, file_bytes: bytes, uploaded_file, extractors: List[Any], client_id: str, progress: Dict[str, float], files_count: int
) -> dict:
    """
    Process the text by first cleaning and then running GPT.
    The GPT processing stage is allocated 20% of a file's progress.
    """
    extractor_index = 0
    parsed_content = None
    await safe_emit_progress(client_id, calculate_overall_progress(progress))
    while extractor_index < len(extractors):
        try:
            text, _ = await asyncio.to_thread(remove_sensitive_data, text)
            logging.warning("Sensitive data removed.")
            json_content = await process_text_with_gpt(text, client_id, progress, files_count)
            if json_content is None:
                logging.error("No valid JSON response from GPT.")
                raise ValueError("Text processing failed")
            parsed_content = json_content
            logging.warning(f"Parsed JSON content: {parsed_content}")
            if not isinstance(parsed_content.get('line_items'), list):
                raise ValueError("Invalid JSON: 'line_items' not list")
            if 'vendor_name' not in parsed_content or 'date' not in parsed_content:
                raise ValueError("Invalid JSON: missing fields")
            break
        except (json.JSONDecodeError, ValueError) as e:
            logging.warning(f"Error parsing JSON with extractor {extractors[extractor_index].__name__}: {e}")
            extractor_index += 1
            if extractor_index < len(extractors):
                extractor = extractors[extractor_index]
                if extractor.__name__ in ["extract_text_with_ocr_space", "extract_text_from_scanned_pdf", "extract_text_from_pdf_camelot"]:
                    fake_file = uploaded_file
                    if asyncio.iscoroutinefunction(extractor):
                        text = await extractor(fake_file)
                    else:
                        text = await asyncio.to_thread(extractor, fake_file)
                else:
                    new_file_obj = io.BytesIO(file_bytes)
                    if asyncio.iscoroutinefunction(extractor):
                        text = await extractor(new_file_obj)
                    else:
                        text = await asyncio.to_thread(extractor, new_file_obj)
            else:
                await send_log("⚠️ No more extractors to retry", user_room=client_id)
                logging.warning("No more extractors to retry.")
                raise ValueError("Text processing failed with all extractors")
    progress["gpt_processing"] = 20.0
    await safe_emit_progress(client_id, calculate_overall_progress(progress))
    return parsed_content

async def process_file_async(
    uploaded_file, user_id: str, group_id: str, client_id: str, progress: Dict[str, float], files_count: int
) -> Tuple[Dict[str, Any], str]:
    try:
        logging.warning(f"Processing file: {uploaded_file.filename}")
        await safe_emit_progress(client_id, calculate_overall_progress(progress))
        filename_lower = uploaded_file.filename.lower()
        if filename_lower.endswith(('.png', '.jpg', '.jpeg', '.bmp', '.tiff')):
            file_type = 'image'
        elif filename_lower.endswith('.pdf'):
            file_type = 'pdf'
        else:
            await send_log("❌ Unsupported file type", user_room=client_id)
            return {'error': 'Unsupported file type'}, uploaded_file.filename
        await uploaded_file.seek(0)
        file_bytes = await uploaded_file.read()
        if not file_bytes:
            logging.error("Empty file!")
            return {'error': 'Empty file'}, uploaded_file.filename
        # Simulate file reading progress (20% stage)
        read_steps = 5
        for step in range(read_steps):
            await asyncio.sleep(0.2)
            progress["file_read"] = ((step + 1) / read_steps) * 20.0
            await safe_emit_progress(client_id, calculate_overall_progress(progress))
        extracted_text, extractor_used = await extract_text(file_bytes, file_type, uploaded_file, client_id, progress, files_count)
        if not extracted_text or not extracted_text.strip():
            await send_log(f"⚠️ No text extracted from: {uploaded_file.filename}", user_room=client_id)
            logging.warning(f"No text extracted from: {uploaded_file.filename}")
            return {'error': 'Text extraction failed'}, uploaded_file.filename
        extractors = get_extractor_list(file_type)
        parsed_content = await process_text_and_parse(extracted_text, file_bytes, uploaded_file, extractors, client_id, progress, files_count)
        if len(parsed_content.get('line_items', [])) == 0:
            return {'error': 'Processing error'}, uploaded_file.filename
        logging.warning(f"Final extractor used: {extractor_used}")
        # Simulate compression progress (20% stage)
        comp_steps = 5
        for step in range(comp_steps):
            await asyncio.sleep(0.2)
            progress["compression"] = ((step + 1) / comp_steps) * 20.0
            await safe_emit_progress(client_id, calculate_overall_progress(progress))
        from masyg_extractor.utils.filename_utils import sanitize_generate_unique_filename
        sanitized_filename = sanitize_generate_unique_filename(uploaded_file.filename)
        await update_firestore_file(user_id, group_id, sanitized_filename, parsed_content)
        # Simulate Firestore update progress (20% stage)
        fs_steps = 5
        for step in range(fs_steps):
            await asyncio.sleep(0.2)
            progress["firestore_update"] = ((step + 1) / fs_steps) * 20.0
            await safe_emit_progress(client_id, calculate_overall_progress(progress))
        return parsed_content, sanitized_filename
    except Exception as e:
        await send_log(f"❌ Error processing file: {uploaded_file.filename}", user_room=client_id)
        logging.exception(f"Error processing file: {uploaded_file.filename}")
        return {'error': str(e)}, uploaded_file.filename

async def process_file_wrapper(
    idx: int, file, user_id: str, group_id: str, client_id: str, file_progress: Dict[str, float], files_count: int
) -> Tuple[int, str, Any]:
    parsed_content, sanitized_filename = await process_file_async(file, user_id, group_id, client_id, file_progress, files_count)
    return idx, sanitized_filename, parsed_content

async def process_files_in_parallel(
    files: List, user_id: str, group_id: str, client_id: str
) -> Dict[int, Any]:
    total_files = len(files)
    # Create a separate progress tracker for each file.
    file_progress_trackers = {idx: get_file_progress_dict() for idx in range(total_files)}
    tasks = [
        process_file_wrapper(idx, f, user_id, group_id, client_id, file_progress_trackers[idx], total_files)
        for idx, f in enumerate(files)
    ]
    results_list = await asyncio.gather(*tasks, return_exceptions=True)
    results = {}
    # Compute overall progress as the average over files.
    overall_progress = sum(calculate_overall_progress(fp) for fp in file_progress_trackers.values()) / total_files
    await safe_emit_progress(client_id, overall_progress)
    await asyncio.sleep(0.5)
    for idx, res in enumerate(results_list):
        if isinstance(res, Exception):
            results[idx] = {'error': str(res)}
        else:
            index, sanitized_filename, parsed_content = res
            results[index] = {'sanitized_filename': sanitized_filename, 'parsed_content': parsed_content}
    return results

# --- Endpoint ---
from fastapi import APIRouter


@router.post("/extract-data", status_code=status.HTTP_201_CREATED)
async def extract_data(
    request: Request,
    files: List[UploadFile] = File(...),
    firebase_user: dict = Depends(lambda: {"userId": "dummy_user"})
):
    client_id = request.session.get("client_id") or 'Guest'
    user_id = firebase_user.get('userId')
    if not user_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User ID not found")
    group_id = generate_group_id()
    # Read file bytes once and store for later use
    file_contents = {file.filename: await file.read() for file in files}
    for filename, data in file_contents.items():
        logging.debug(f"File {filename} size: {len(data)} bytes")
    results = await process_files_in_parallel(files, user_id, group_id, client_id)
    files_metadata = []
    for file in files:
        result = results.get(files.index(file), {})
        if 'error' in result.get('parsed_content', {}):
            await send_log(f'❌ {file.filename} failed to process. Please submit a valid invoice, bill, or receipt', user_room=client_id)
            continue
        else:
            await send_log(f'✅ {file.filename} processed successfully!', user_room=client_id)
        sanitized_filename = result.get('sanitized_filename')
        compression_stream = io.BytesIO(file_contents[file.filename])
        compressed_file = await asyncio.to_thread(compress_file_blob, compression_stream, file.filename)
        compressed_file.seek(0)
        content = compressed_file.read()
        encoded_content = base64.b64encode(content).decode('utf-8')
        files_metadata.append({'filename': sanitized_filename, 'content': encoded_content})
    if len(files_metadata) == 0:
        return {'error': '❌ Files Processing Failed'}
    group_doc_ref = firestore_db.collection("users").document(user_id).collection("groups").document(group_id)
    metadata = {
        'upload_time': datetime.now().isoformat(),
        'file_count': len(files) - len(files_metadata),
        'group_name': group_id,
        'isViewed': False
    }
    group_doc_ref.set({'metadata': metadata})
    metadata['files'] = files_metadata
    group_doc_ref.set({'metadata': metadata}, merge=True)
    group_obj = {}
    for group in results.values():
        group_obj[group['sanitized_filename']] = group['parsed_content']
    group_obj['group_id'] = group_id
    group_obj['metadata'] = metadata
    return group_obj

# Add the router to the FastAPI app
# app.include_router(router, prefix="/api")