from __future__ import annotations

import asyncio
import hashlib
import io
from typing import Iterator

from masyg_extractor.integrations.document_sources.gmail.client import (
    GmailHistoryExpiredError,
    decode_gmail_body_data,
    get_gmail_attachment,
    get_gmail_message,
    list_gmail_history,
)
from masyg_extractor.integrations.document_sources.gmail.repository import (
    GmailCredentialRepository,
)
from masyg_extractor.integrations.document_sources.gmail.service import (
    refresh_gmail_authorization,
)
from masyg_extractor.services.document_ingestion import ingest_documents
from masyg_extractor.services.my_log import logger
from masyg_extractor.services.progress_log import ExtractorProgressLog
from masyg_extractor.services.subscription_access import (
    user_has_active_subscription,
)

SUPPORTED_GMAIL_DOCUMENT_EXTENSIONS = (
    ".pdf",
    ".png",
    ".jpg",
    ".jpeg",
    ".bmp",
    ".tiff",
)


class GmailAttachmentUpload:
    def __init__(
        self,
        *,
        filename: str,
        content: bytes,
        content_type: str,
    ):
        self.filename = str(filename or "").strip()
        self.content_type = str(content_type or "").strip()
        self._stream = io.BytesIO(content)

    async def read(self) -> bytes:
        return self._stream.read()

    async def seek(self, offset: int) -> int:
        return self._stream.seek(offset)

    def release(self) -> None:
        if not self._stream.closed:
            self._stream.close()


def _iter_parts(payload: dict) -> Iterator[dict]:
    if not isinstance(payload, dict):
        return
    yield payload
    for part in payload.get("parts") or []:
        if isinstance(part, dict):
            yield from _iter_parts(part)


def _supported_attachment(part: dict) -> bool:
    filename = str(part.get("filename") or "").strip().lower()
    return bool(
        filename
        and filename.endswith(SUPPORTED_GMAIL_DOCUMENT_EXTENSIONS)
    )


def _part_key(part: dict) -> str:
    body = part.get("body") or {}
    attachment_id = str(body.get("attachmentId") or "").strip()
    if attachment_id:
        return f"attachment:{attachment_id}"

    part_id = str(part.get("partId") or "").strip()
    if part_id:
        return f"part:{part_id}"

    fingerprint = (
        str(part.get("filename") or "")
        + "\0"
        + str(body.get("data") or "")
    )
    return "inline:" + hashlib.sha256(
        fingerprint.encode("utf-8")
    ).hexdigest()


def _gmail_group_id(
    message_id: str,
    part_key: str,
) -> str:
    raw = (
        str(message_id or "").strip()
        + "\0"
        + str(part_key or "").strip()
    )
    digest = hashlib.sha256(
        raw.encode("utf-8")
    ).hexdigest()
    return f"gmail-{digest[:40]}"


async def _part_bytes(
    access_token: str,
    *,
    message_id: str,
    part: dict,
) -> bytes:
    body = part.get("body") or {}
    attachment_id = str(body.get("attachmentId") or "").strip()
    if attachment_id:
        return await get_gmail_attachment(
            access_token,
            message_id=message_id,
            attachment_id=attachment_id,
        )
    return decode_gmail_body_data(str(body.get("data") or ""))


async def process_gmail_notifications_for_user(user_id: str) -> dict:
    normalized_user = str(user_id or "").strip()
    if not normalized_user:
        return {"status": "invalid_user"}

    repository = GmailCredentialRepository(normalized_user)

    if not await asyncio.to_thread(repository.is_connected):
        return {"status": "disconnected"}

    if not await user_has_active_subscription(normalized_user):
        return {"status": "subscription_required"}

    lease_token = await asyncio.to_thread(
        repository.claim_processing_lease
    )
    if not lease_token:
        return {"status": "busy"}

    try:
        processed_history_id = await asyncio.to_thread(
            repository.history_id
        )
        latest_history_id = await asyncio.to_thread(
            repository.latest_notification_history_id
        )

        if (
            not processed_history_id.isdigit()
            or not latest_history_id.isdigit()
        ):
            return {"status": "no_cursor"}

        if int(latest_history_id) <= int(processed_history_id):
            return {
                "status": "caught_up",
                "history_id": processed_history_id,
            }

        access_token = await refresh_gmail_authorization(
            normalized_user
        )

        message_ids: list[str] = []
        seen_message_ids: set[str] = set()
        page_token = ""
        final_history_id = processed_history_id

        try:
            while True:
                page = await list_gmail_history(
                    access_token,
                    start_history_id=processed_history_id,
                    page_token=page_token or None,
                )
                final_history_id = str(
                    page.get("history_id") or final_history_id
                ).strip()

                for message_id in page.get("message_ids") or []:
                    message = str(message_id or "").strip()
                    if message and message not in seen_message_ids:
                        seen_message_ids.add(message)
                        message_ids.append(message)

                page_token = str(
                    page.get("next_page_token") or ""
                ).strip()
                if not page_token:
                    break
        except GmailHistoryExpiredError:
            await asyncio.to_thread(
                repository.mark_sync_recovery_required,
                processed_history_id=processed_history_id,
                latest_history_id=latest_history_id,
            )
            logger.warning(
                "Gmail automatic import requires cursor recovery "
                "user_id=%s",
                normalized_user,
            )
            return {"status": "recovery_required"}

        imported = 0
        supported = 0
        failures = 0

        for message_id in message_ids:
            try:
                message = await get_gmail_message(
                    access_token,
                    message_id,
                )
            except Exception as exc:
                failures += 1
                logger.warning(
                    "Gmail message fetch failed "
                    "user_id=%s error_type=%s",
                    normalized_user,
                    type(exc).__name__,
                )
                continue

            payload = message.get("payload") or {}
            for part in _iter_parts(payload):
                if not _supported_attachment(part):
                    continue

                supported += 1
                filename = str(part.get("filename") or "").strip()
                part_key = _part_key(part)
                group_id = _gmail_group_id(
                    message_id,
                    part_key,
                )

                claim = await asyncio.to_thread(
                    repository.claim_attachment,
                    message_id=message_id,
                    part_key=part_key,
                    filename=filename,
                )
                if claim == "processed":
                    continue
                if claim != "claimed":
                    failures += 1
                    continue

                try:
                    if await asyncio.to_thread(
                        repository.group_ingestion_succeeded,
                        group_id,
                    ):
                        await asyncio.to_thread(
                            repository.mark_attachment_processed,
                            message_id=message_id,
                            part_key=part_key,
                            group_id=group_id,
                        )
                        continue

                    content = await _part_bytes(
                        access_token,
                        message_id=message_id,
                        part=part,
                    )
                    if not content:
                        raise ValueError(
                            "Gmail attachment content is empty"
                        )

                    upload = GmailAttachmentUpload(
                        filename=filename,
                        content=content,
                        content_type=str(
                            part.get("mimeType") or ""
                        ).strip(),
                    )
                    progress_logger = ExtractorProgressLog(
                        client_id=f"gmail:{normalized_user}"
                    )

                    result = await ingest_documents(
                        files=[upload],
                        user_id=normalized_user,
                        client_id=f"gmail:{normalized_user}",
                        progress_logger=progress_logger,
                        group_id=group_id,
                    )
                    if result.get("error"):
                        raise RuntimeError(
                            "Canonical document ingestion failed"
                        )

                    await asyncio.to_thread(
                        repository.mark_attachment_processed,
                        message_id=message_id,
                        part_key=part_key,
                        group_id=group_id,
                    )
                    imported += 1
                except Exception as exc:
                    failures += 1
                    await asyncio.to_thread(
                        repository.mark_attachment_failed,
                        message_id=message_id,
                        part_key=part_key,
                        error_type=type(exc).__name__,
                    )
                    logger.warning(
                        "Gmail attachment import failed "
                        "user_id=%s error_type=%s",
                        normalized_user,
                        type(exc).__name__,
                    )

        if failures:
            logger.warning(
                "Gmail automatic import incomplete "
                "user_id=%s imported=%s failures=%s",
                normalized_user,
                imported,
                failures,
            )
            return {
                "status": "partial",
                "imported": imported,
                "failures": failures,
                "supported": supported,
            }

        if not final_history_id or not final_history_id.isdigit():
            return {
                "status": "invalid_result_cursor",
                "imported": imported,
            }

        await asyncio.to_thread(
            repository.advance_history_id,
            final_history_id,
        )
        await asyncio.to_thread(
            repository.mark_sync_healthy,
            processed_history_id=final_history_id,
        )

        logger.info(
            "Gmail automatic import complete "
            "user_id=%s imported=%s history_id=%s",
            normalized_user,
            imported,
            final_history_id,
        )
        return {
            "status": "processed",
            "imported": imported,
            "supported": supported,
            "history_id": final_history_id,
        }
    finally:
        await asyncio.to_thread(
            repository.release_processing_lease,
            lease_token,
        )


async def process_pending_gmail_notifications() -> None:
    user_ids = await asyncio.to_thread(
        GmailCredentialRepository.registered_user_ids
    )
    for user_id in user_ids:
        try:
            await process_gmail_notifications_for_user(user_id)
        except Exception as exc:
            logger.warning(
                "Gmail notification processor failed "
                "user_id=%s error_type=%s",
                user_id,
                type(exc).__name__,
            )
