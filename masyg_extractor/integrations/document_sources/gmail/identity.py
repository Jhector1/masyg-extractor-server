from __future__ import annotations

import hashlib
from typing import Iterator


def iter_gmail_parts(
    payload: dict,
    mime_path: str = "0",
) -> Iterator[tuple[dict, str]]:
    if not isinstance(payload, dict):
        return

    yield payload, mime_path

    for index, part in enumerate(
        payload.get("parts") or []
    ):
        if isinstance(part, dict):
            yield from iter_gmail_parts(
                part,
                f"{mime_path}.{index}",
            )


def gmail_part_key(
    part: dict,
    *,
    mime_path: str,
) -> str:
    # attachmentId is deliberately excluded from durable identity.
    part_id = str(
        part.get("partId") or ""
    ).strip()

    if part_id:
        return f"part:{part_id}"

    fingerprint = "\0".join(
        (
            str(
                mime_path or "0"
            ).strip() or "0",
            str(
                part.get("filename") or ""
            ).strip().casefold(),
            str(
                part.get("mimeType") or ""
            ).strip().casefold(),
        )
    )

    return "mime:" + hashlib.sha256(
        fingerprint.encode("utf-8")
    ).hexdigest()


def gmail_group_id(
    message_id: str,
    part_key: str,
) -> str:
    raw = (
        str(
            message_id or ""
        ).strip()
        + "\0"
        + str(
            part_key or ""
        ).strip()
    )

    digest = hashlib.sha256(
        raw.encode("utf-8")
    ).hexdigest()

    return f"gmail-{digest[:40]}"
