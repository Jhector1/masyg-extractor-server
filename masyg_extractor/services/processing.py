import asyncio
import io

import json
from typing import Tuple, Any, List, Dict, Callable, Awaitable

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
    # extract_text_from_pdf_camelot,
    process_text_with_gpt, process_text_with_regex,
)

# Firestore client
firestore_db = firestore.client()







# ──────────────────────────────────────────────────────────────────────────────
# Smooth stage helpers
# ──────────────────────────────────────────────────────────────────────────────

async def _stage_progress(
    progress_logger: ExtractorProgressLog,
    progress: Dict[str, float],
    stage_key: str,
    stage_weight: float,
    *,
    ticks: int = 100,
    sleep_secs: float = 0.05,
    file_id: str,
):
    """
    Emit many small, cumulative updates inside a stage so the UI animates 1–2–3%.
    Also includes the stage name in each emit so the client can label the row.
    """
    stage_label = {
        "file_read": "Reading file",
        "text_extraction": "Extracting text",
        "gpt_processing": "Understanding text",
        "compression": "Compressing preview",
        "firestore_update": "Saving to cloud",
    }.get(stage_key, stage_key)

    start_val = float(progress.get(stage_key, 0.0))
    for i in range(1, ticks + 1):
        progress[stage_key] = min(start_val + (i / ticks) * stage_weight, stage_weight)
        await progress_logger.emit(
            progress_logger.calculate_overall_progress(progress),
            file_id=file_id,
            stage=stage_label,
        )
        await asyncio.sleep(sleep_secs)


async def _gpt_stage_with_progress(
    text: str,
    progress_logger: ExtractorProgressLog,
    progress: Dict[str, float],
    file_id: str,
    *,
    run_gpt: Callable[[], Awaitable[dict | None]],
):
    """
    While GPT runs, trickle progress so the 50% weight advances smoothly AND label shows.
    """
    weight = ExtractorProgressLog.GPT_PROCESSING_WEIGHT  # 50
    stage_key = "gpt_processing"
    stage_label = "Understanding text"
    ticks, sleep = 200, 0.03
    done = False

    async def spinner():
        nonlocal done
        i = 0
        while not done and i < ticks:
            i += 1
            target = min(weight * (i / ticks), weight - 0.1)
            progress[stage_key] = max(progress.get(stage_key, 0.0), target)
            await progress_logger.emit(
                progress_logger.calculate_overall_progress(progress),
                file_id=file_id,
                stage=stage_label,
            )
            await asyncio.sleep(sleep)

    spin_task = asyncio.create_task(spinner())
    try:
        result = await run_gpt()
        return result
    finally:
        done = True
        spin_task.cancel()
        # snap to full stage completion and emit
        progress[stage_key] = weight
        await progress_logger.emit(
            progress_logger.calculate_overall_progress(progress),
            file_id=file_id,
            stage=stage_label,
        )


async def _finalize_to_100(
    progress_logger: ExtractorProgressLog,
    per_file: Dict[str, float],
    file_id: str,
    message_stage: str = "Finalizing",
):
    """
    Ensure a file's progress bar reaches 100% even if an error occurred mid-pipeline.
    """
    total = sum(per_file.values())
    if total >= 100.0:
        return
    # Nudge remaining amount in tiny steps
    remaining = 100.0 - total
    steps = 40
    for i in range(1, steps + 1):
        # distribute into the last stage slot (firestore_update)
        per_file["firestore_update"] = min(
            per_file.get("firestore_update", 0.0) + remaining / steps,
            ExtractorProgressLog.FIRESTORE_UPDATE_WEIGHT,
        )
        await progress_logger.emit(
            sum(per_file.values()),
            file_id=file_id,
            stage=message_stage,
        )
        await asyncio.sleep(0.015)


def get_extractor_list(file_type: str) -> List[Any]:
    """Return list of extractors based on file type."""
    if file_type == 'pdf':
        return [
            extract_text_from_pdf,
            extract_text_from_pdf_image,
            extract_text_with_ocr_space,
            extract_text_from_scanned_pdf,
            # extract_text_from_pdf_camelot,
        ]
    return [extract_text_from_image]






# replace your extract_text with this:
async def extract_text(
    file_bytes: bytes,
    file_type: str,
    uploaded_file,
    progress_logger: ExtractorProgressLog,
    progress: Dict[str, float],
    files_count: int,
    file_id: str,
) -> Tuple[str, str]:
    extractors = get_extractor_list(file_type)
    extracted_text = ""
    extractor_used = ""

    # emit stage start
    await progress_logger.emit(progress_logger.calculate_overall_progress(progress), file_id=file_id)

    for extractor in extractors:
        try:
            if extractor.__name__ in ["extract_text_with_ocr_space", "extract_text_from_scanned_pdf", "extract_text_from_pdf_camelot"]:
                fake_file = FakeUploadFile(uploaded_file.filename, file_bytes, uploaded_file.content_type)
                extracted_text = await extractor(fake_file) if asyncio.iscoroutinefunction(extractor) else await asyncio.to_thread(extractor, fake_file)
            else:
                file_obj = io.BytesIO(file_bytes)
                extracted_text = await extractor(file_obj) if asyncio.iscoroutinefunction(extractor) else await asyncio.to_thread(extractor, file_obj)

            if asyncio.iscoroutine(extracted_text):
                extracted_text = await extracted_text

            if not isinstance(extracted_text, str):
                logger.error("Extracted text is not a string; converting it to string.")
                extracted_text = str(extracted_text)

            # keep sensitive data removal here if it doesn't kill numerics.
            extracted_text, _ = await asyncio.to_thread(remove_sensitive_data, extracted_text)

            if extracted_text.strip():
                extractor_used = extractor.__name__.upper()
                logger.info(f"Text extraction succeeded with {extractor_used}")
                break
        except Exception as e:
            logger.warning(f"Extractor {extractor.__name__} failed: {e}")

    progress["text_extraction"] = ExtractorProgressLog.TEXT_EXTRACTION_WEIGHT
    await progress_logger.emit(progress_logger.calculate_overall_progress(progress), file_id=file_id)
    return extracted_text, extractor_used



import inspect

# replace your process_text_and_parse with this:
async def process_text_and_parse(
    text: str,
    file_bytes: bytes,
    uploaded_file,
    extractors: List[Any],
    progress_loger: ExtractorProgressLog,
    progress: Dict[str, float],
    files_count: int,
    file_id: str,
) -> dict:
    """
    Clean text, run GPT once, apply tolerant validation,
    and fallback to regex if GPT fails.
    """
    # If remove_sensitive_data is too aggressive, comment this out temporarily.
    text, _ = await asyncio.to_thread(remove_sensitive_data, text)
    logger.info("Sensitive data removed (parsing).")

    # stage start
    await progress_loger.emit(progress_loger.calculate_overall_progress(progress), file_id=file_id)

    # 1) GPT
    json_content = await process_text_with_gpt(text, progress_loger, progress, files_count, file_id=file_id)

    # 2) fallback: regex heuristic
    if not json_content:
        heuristic = process_text_with_regex(text)
        if heuristic:
            try:
                heuristic_list = json.loads(heuristic) if isinstance(heuristic, str) else heuristic
            except Exception:
                heuristic_list = []
            json_content = {
                "documentType": None,
                "vendor_name": None,
                "date": None,
                "due_date": None,
                "line_items": heuristic_list if isinstance(heuristic_list, list) else [],
            }

    if not json_content:
        return None

    # 3) tolerant validation
    if not isinstance(json_content.get("line_items"), list):
        json_content["line_items"] = []

    for k in ("vendor_name", "date", "due_date", "documentType"):
        json_content.setdefault(k, None)

    progress["gpt_processing"] = ExtractorProgressLog.GPT_PROCESSING_WEIGHT
    await progress_loger.emit(progress_loger.calculate_overall_progress(progress), file_id=file_id)
    return json_content


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

        for step in range(comp_steps):
            await asyncio.sleep(0.2)
            progress["compression"] = ((step + 1) / comp_steps) * ExtractorProgressLog.COMPRESSION_WEIGHT
            await progress_logger.safe_emit_progress( progress_logger.calculate_overall_progress(progress))

        from masyg_extractor.utils.filename_utils import sanitize_generate_unique_filename  # Replace with your actual import.
        sanitized_filename = sanitize_generate_unique_filename(uploaded_file.filename)
        await update_firestore_file(user_id, group_id, sanitized_filename, parsed_content)

        fs_steps = 5*files_count

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







# services/processing.py
import os

from masyg_extractor.utils.filename_utils import sanitize_generate_unique_filename
async def _process_one(
    idx: int,
    uploaded_file,            # UploadFile
    file_bytes: bytes,        # pre-read bytes
    user_id: str,
    group_id: str,
    progress_logger: ExtractorProgressLog,
) -> Tuple[int, str, Any]:
    """
    Processes a single file and emits per-file progress using file_id.
    Includes a final guarantee to 100% (never sticks at 65%).
    """
    file_id = sanitize_generate_unique_filename(uploaded_file.filename)

    per_file: Dict[str, float] = {
        "file_read": 0.0,
        "text_extraction": 0.0,
        "gpt_processing": 0.0,
        "compression": 0.0,
        "firestore_update": 0.0,
    }

    try:
        # FILE READ (simulate smooth animation even though bytes are pre-read)
        await _stage_progress(
            progress_logger, per_file, "file_read",
            ExtractorProgressLog.FILE_READ_WEIGHT,
            ticks=50, sleep_secs=0.04, file_id=file_id
        )

        # resolve file type
        fn = uploaded_file.filename.lower()
        file_type = "pdf" if fn.endswith(".pdf") else ("image" if any(fn.endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".bmp", ".tiff")) else None)
        if not file_type:
            logger.warning(f"Unsupported type: {uploaded_file.filename}")
            await _finalize_to_100(progress_logger, per_file, file_id, "Unsupported type")
            return idx, file_id, {"error": "Unsupported file type"}

        # TEXT EXTRACTION
        trickle = asyncio.create_task(_stage_progress(
            progress_logger, per_file, "text_extraction",
            ExtractorProgressLog.TEXT_EXTRACTION_WEIGHT,
            ticks=80, sleep_secs=0.03, file_id=file_id
        ))
        text, extractor_used = await extract_text(
            file_bytes, file_type, uploaded_file,
            progress_logger, per_file, files_count=1, file_id=file_id
        )
        trickle.cancel()
        per_file["text_extraction"] = ExtractorProgressLog.TEXT_EXTRACTION_WEIGHT
        await progress_logger.emit(sum(per_file.values()), file_id=file_id, stage="Extracting text")

        if not (text and text.strip()):
            logger.warning(f"No text extracted: {uploaded_file.filename}")
            await _finalize_to_100(progress_logger, per_file, file_id, "No text extracted")
            return idx, file_id, {"error": "Text extraction failed", "stage": "Extracting text"}

        # GPT PROCESS
        async def _run_gpt():
            return await process_text_and_parse(
                text, file_bytes, uploaded_file,
                get_extractor_list(file_type),
                progress_logger, per_file, files_count=1, file_id=file_id
            )

        parsed_content = await _gpt_stage_with_progress(text, progress_logger, per_file, file_id, run_gpt=_run_gpt)
        if not parsed_content or len(parsed_content.get("line_items", [])) == 0:
            await _finalize_to_100(progress_logger, per_file, file_id, "Parsing failed")
            return idx, file_id, {"error": "error while processing file", "stage": "Parsing failed"}

        logger.info(f"Final successful extractor used: {extractor_used}")

        # COMPRESSION
        await _stage_progress(
            progress_logger, per_file, "compression",
            ExtractorProgressLog.COMPRESSION_WEIGHT,
            ticks=120, sleep_secs=0.02, file_id=file_id
        )

        # FIRESTORE UPDATE
        await update_firestore_file(user_id, group_id, file_id, parsed_content)
        await _stage_progress(
            progress_logger, per_file, "firestore_update",
            ExtractorProgressLog.FIRESTORE_UPDATE_WEIGHT,
            ticks=100, sleep_secs=0.02, file_id=file_id
        )

        # Done → ensure clean 100% (in case of float rounding)
        await _finalize_to_100(progress_logger, per_file, file_id, "Done")
        return idx, file_id, parsed_content

    except Exception as e:
        logger.exception("File task failed")
        # Even on unexpected crash, finish the bar so it never hangs mid-way
        await _finalize_to_100(progress_logger, per_file, file_id, "Failed")
        return idx, file_id, {"error": str(e), "stage": "File task failed"}

# ──────────────────────────────────────────────────────────────────────────────
# Batch orchestrator
# ──────────────────────────────────────────────────────────────────────────────

async def process_files_in_parallel(
    file_buffers: List[Tuple[int, Any, bytes]],  # (idx, UploadFile, bytes)
    user_id: str,
    group_id: str,
    progress_logger: ExtractorProgressLog,
    max_concurrency: int | None = None,
) -> Dict[int, Any]:
    """
    Run each file with a small semaphore. Thanks to ProgressLog's registry,
    overall emits on every per-file update (no more stuck overall bar).
    """
    results: Dict[int, Any] = {}

    if max_concurrency is None:
        max_concurrency = max(2, min(4, (os.cpu_count() or 2)))

    sem = asyncio.Semaphore(max_concurrency)

    async def limited(_idx, _uf, _b):
        async with sem:
            return await _process_one(_idx, _uf, _b, user_id, group_id, progress_logger)

    results_list = await asyncio.gather(*(limited(i, uf, b) for (i, uf, b) in file_buffers), return_exceptions=True)

    for item in results_list:
        if isinstance(item, Exception):
            logger.exception("File task failed", exc_info=item)
            continue
        idx, file_id, payload = item
        results[idx] = {"sanitized_filename": file_id, "parsed_content": payload}

    # Final overall 100% (in case the last per-file emit was throttled)
    await progress_logger.emit(100.0, file_id=None)
    return results
# async def process_files_in_parallel(
#     files: List, user_id: str, group_id: str, progress_logger: ExtractorProgressLog, global_progress: Dict[str, float]
# ) -> Dict[int, Any]:
#     total_files = len(files)
#     file_progress_trackers = {idx: ExtractorProgressLog.get_file_progress_dict() for idx in range(total_files)}
#
#     tasks = [
#         process_file_wrapper(idx, f, user_id, group_id, progress_logger, global_progress, total_files)
#         for idx, f in enumerate(files)
#     ]
#     results_list = await asyncio.gather(*tasks, return_exceptions=True)
#     results = {}
#     # Update overall progress based on number of files completed
#     # Compute overall progress as the average over files.
#     overall_progress = sum(progress_logger.calculate_overall_progress(fp) for fp in file_progress_trackers.values()) / total_files
#     await progress_logger.safe_emit_progress(overall_progress)
#     await asyncio.sleep(0.5)
#     for idx, res in enumerate(results_list):
#         # overall_batch_progress = calculate_overall_progress(global_progress)
#         # await safe_emit_progress(client_id, overall_batch_progress)
#         # await asyncio.sleep(0.5)
#         if isinstance(res, Exception):
#             results[idx] = {'error': str(res)}
#         else:
#             index, sanitized_filename, parsed_content = res
#             results[index] = {
#                 'sanitized_filename': sanitized_filename,
#                 'parsed_content': parsed_content
#             }
#     return results

