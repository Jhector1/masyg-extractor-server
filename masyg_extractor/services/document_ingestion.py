from __future__ import annotations

import asyncio
import base64
import io
import os
from datetime import datetime
from typing import Any, Protocol, Sequence

from firebase_admin import firestore

from masyg_extractor.services.dependencies import generate_group_id
from masyg_extractor.services.file_extractor_service import record_failed_file
from masyg_extractor.services.firestore_helpers import document_set
from masyg_extractor.services.image_extractor_service import compress_file_blob
from masyg_extractor.services.my_log import logger, send_log
from masyg_extractor.services.processing import process_files_in_parallel
from masyg_extractor.services.progress_log import ExtractorProgressLog
from masyg_extractor.utils.extensions import sio


EVENT_PROGRESS = "data-progress"
DEFAULT_MAX_IMPORT_FILES = 200
MAX_IMPORT_FILES_ENV = "MASYG_MAX_IMPORT_FILES"
DEFAULT_IMPORT_CHUNK_SIZE = 10
IMPORT_CHUNK_SIZE_ENV = "MASYG_IMPORT_CHUNK_SIZE"


class DocumentImportLimitError(ValueError):
    pass


def max_import_files() -> int:
    raw = str(os.getenv(MAX_IMPORT_FILES_ENV, DEFAULT_MAX_IMPORT_FILES)).strip()
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            f"{MAX_IMPORT_FILES_ENV} must be a positive integer"
        ) from exc
    if value <= 0:
        raise RuntimeError(f"{MAX_IMPORT_FILES_ENV} must be greater than zero")
    return value


def validate_import_count(count: int) -> int:
    maximum = max_import_files()
    if count <= 0:
        raise DocumentImportLimitError("Choose at least one document")
    if count > maximum:
        raise DocumentImportLimitError(
            f"You can import up to {maximum} files at a time"
        )
    return maximum


def import_chunk_size() -> int:
    raw = str(os.getenv(IMPORT_CHUNK_SIZE_ENV, DEFAULT_IMPORT_CHUNK_SIZE)).strip()
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            f"{IMPORT_CHUNK_SIZE_ENV} must be a positive integer"
        ) from exc
    if value <= 0:
        raise RuntimeError(f"{IMPORT_CHUNK_SIZE_ENV} must be greater than zero")
    return value


class ReadableUpload(Protocol):
    filename: str | None

    async def read(self) -> bytes:
        ...

    async def seek(self, offset: int) -> object:
        ...


async def buffer_upload_files(
    files: Sequence[ReadableUpload],
    *,
    start_index: int = 0,
) -> list[tuple[int, ReadableUpload, bytes]]:
    """Read one bounded chunk, rewind it, and preserve global pipeline indexes."""
    buffered: list[tuple[int, ReadableUpload, bytes]] = []
    for offset, upload in enumerate(files):
        content = await upload.read()
        await upload.seek(0)
        buffered.append((start_index + offset, upload, content))
    return buffered


def _filename(upload: ReadableUpload, index: int) -> str:
    value = str(getattr(upload, "filename", "") or "").strip()
    return value or f"document-{index + 1}"


async def persist_failed_ingestion_artifact(
    *,
    user_id: str,
    group_id: str,
    filename: str,
    error_message: str,
    stage: str | None = None,
    file_id: str = "terminal-failure",
) -> str:
    """Persist one idempotent user-visible terminal ingestion failure."""

    normalized_user_id = str(user_id or "").strip()
    normalized_group_id = str(group_id or "").strip()

    if not normalized_user_id:
        raise ValueError("user_id is required")

    if not normalized_group_id:
        raise ValueError("group_id is required")

    failed_id = await record_failed_file(
        normalized_user_id,
        normalized_group_id,
        filename,
        error_message,
        stage=stage,
        file_id=file_id,
    )

    firestore_client = firestore.client()
    group_doc_ref = (
        firestore_client.collection("users")
        .document(normalized_user_id)
        .collection("groups")
        .document(normalized_group_id)
    )

    fail_meta = {
        "status": "failed",
        "upload_time": datetime.now().isoformat(),
        "file_count": 0,
        "group_name": normalized_group_id,
        "isViewed": False,
        "failed_count": 1,
        "failed_files": [failed_id],
    }

    await document_set(
        group_doc_ref,
        {"metadata": fail_meta},
        merge=True,
    )

    return failed_id


async def ingest_documents(
    *,
    files: Sequence[ReadableUpload],
    user_id: str,
    client_id: str,
    progress_logger: ExtractorProgressLog,
    group_id: str | None = None,
    persist_failure_artifacts: bool = True,
) -> dict[str, Any]:
    normalized_user_id = str(user_id or "").strip()
    if not normalized_user_id:
        raise ValueError("user_id is required")

    total_files = len(files)
    validate_import_count(total_files)
    chunk_size = min(import_chunk_size(), total_files)

    room = str(client_id or "").strip() or "Guest"
    progress_logger.clear()
    progress_logger.set_expected_file_count(total_files)
    group_id = str(group_id or "").strip() or generate_group_id()

    results: dict[int, Any] = {}
    files_metadata: list[dict[str, str]] = []
    failed_files_ids: list[str] = []
    failure_details: list[dict[str, str]] = []
    failed = 0

    async def _record_failure(
        filename: str,
        error_message: str,
        failure_stage: str,
    ) -> str:
        if not persist_failure_artifacts:
            return ""

        return await record_failed_file(
            normalized_user_id,
            group_id,
            filename,
            error_message,
            stage=failure_stage,
        )

    for start_index in range(0, total_files, chunk_size):
        chunk_files = files[start_index : start_index + chunk_size]
        file_buffers = await buffer_upload_files(
            chunk_files,
            start_index=start_index,
        )

        chunk_results = await process_files_in_parallel(
            file_buffers=file_buffers,
            user_id=normalized_user_id,
            group_id=group_id,
            progress_logger=progress_logger,
            emit_final_overall=False,
        )
        results.update(chunk_results)

        for orig_idx, upload, raw in file_buffers:
            filename = _filename(upload, orig_idx)
            result = chunk_results.get(orig_idx)

            if not result:
                failed += 1
                error_message = "Pipeline returned no result"
                failure_stage = "pipeline"
                failed_id = await _record_failure(
                    filename,
                    error_message,
                    failure_stage,
                )
                if failed_id:
                    failed_files_ids.append(failed_id)

                failure_details.append(
                    {
                        "filename": filename,
                        "file_id": failed_id,
                        "error": error_message,
                        "stage": failure_stage,
                    }
                )
                asyncio.create_task(
                    send_log(f"❌ {filename} failed to process.", user_room=room)
                )
                continue

            parsed = result.get("parsed_content")
            if isinstance(parsed, dict) and "error" in parsed:
                failed += 1
                error_message = str(
                    parsed.get("error") or "Unknown error"
                ).strip()
                failure_stage = str(
                    parsed.get("stage") or "parsing"
                ).strip()
                failed_id = await _record_failure(
                    filename,
                    error_message,
                    failure_stage,
                )
                if failed_id:
                    failed_files_ids.append(failed_id)

                failure_details.append(
                    {
                        "filename": filename,
                        "file_id": failed_id,
                        "error": error_message,
                        "stage": failure_stage,
                    }
                )
                asyncio.create_task(
                    send_log(
                        f"❌ {filename} failed: {error_message}. "
                        "Please submit a valid invoice, bill, or receipt.",
                        user_room=room,
                    )
                )
                continue

            sanitized_filename = result.get("sanitized_filename") or filename

            try:
                compression_stream = io.BytesIO(raw)
                compressed_file = await asyncio.to_thread(
                    compress_file_blob,
                    compression_stream,
                    filename,
                )
                compressed_file.seek(0)
                encoded_content = base64.b64encode(compressed_file.read()).decode(
                    "utf-8"
                )
                files_metadata.append(
                    {"filename": sanitized_filename, "content": encoded_content}
                )
            except Exception as exc:
                logger.warning("Compression failed for %s: %s", filename, exc)

            asyncio.create_task(
                send_log(f"✅ {filename} processed successfully!", user_room=room)
            )

        for upload in chunk_files:
            release = getattr(upload, "release", None)
            if callable(release):
                release()

        del file_buffers

    firestore_client = firestore.client()
    group_doc_ref = (
        firestore_client.collection("users")
        .document(normalized_user_id)
        .collection("groups")
        .document(group_id)
    )

    if failed >= total_files:
        fail_meta = {
            "status": "failed",
            "upload_time": datetime.now().isoformat(),
            "file_count": 0,
            "group_name": group_id,
            "isViewed": False,
            "failed_count": failed,
            "failed_files": failed_files_ids,
        }
        if persist_failure_artifacts:
            await document_set(
                group_doc_ref,
                {"metadata": fail_meta},
                merge=True,
            )

        await sio.emit(
            EVENT_PROGRESS,
            {"progress": 100, "file_id": None},
            room=room,
        )
        return {
            "error": "❌ Files Processing Failed",
            "group_id": group_id,
            "metadata": fail_meta,
            "failures": failure_details,
        }

    metadata: dict[str, Any] = {
        "upload_time": datetime.now().isoformat(),
        "file_count": total_files - failed,
        "group_name": group_id,
        "isViewed": False,
    }
    await document_set(group_doc_ref, {"metadata": metadata})

    if failed > 0:
        metadata["failed_count"] = failed
        metadata["failed_files"] = failed_files_ids
        await document_set(group_doc_ref, {"metadata": metadata}, merge=True)

    if files_metadata:
        metadata["files"] = files_metadata
        await document_set(group_doc_ref, {"metadata": metadata}, merge=True)

    group_obj: dict[str, Any] = {}
    for item in results.values():
        group_obj[item["sanitized_filename"]] = item["parsed_content"]
    group_obj["group_id"] = group_id
    group_obj["metadata"] = metadata
    if failure_details:
        group_obj["failures"] = failure_details

    await sio.emit(
        EVENT_PROGRESS,
        {"progress": 100, "file_id": None},
        room=room,
    )
    return group_obj
