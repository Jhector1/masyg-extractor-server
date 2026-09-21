from __future__ import annotations

import base64
import json
import os

from google.auth.transport import requests as google_requests
from google.oauth2 import id_token


class GmailPubSubConfigurationError(RuntimeError):
    pass


class GmailPubSubAuthenticationError(RuntimeError):
    pass


class GmailPubSubPayloadError(ValueError):
    pass


def push_auth_config() -> tuple[str, str]:
    audience = (
        os.getenv("GMAIL_PUBSUB_PUSH_AUDIENCE")
        or ""
    ).strip()
    service_account = (
        os.getenv("GMAIL_PUBSUB_PUSH_SERVICE_ACCOUNT")
        or ""
    ).strip().lower()

    missing = []
    if not audience:
        missing.append("GMAIL_PUBSUB_PUSH_AUDIENCE")
    if not service_account:
        missing.append(
            "GMAIL_PUBSUB_PUSH_SERVICE_ACCOUNT"
        )
    if missing:
        raise GmailPubSubConfigurationError(
            "Missing Gmail Pub/Sub push configuration: "
            + ", ".join(missing)
        )

    return audience, service_account


def verify_push_authorization(
    authorization_header: str | None,
) -> dict:
    value = str(
        authorization_header or ""
    ).strip()
    if not value.startswith("Bearer "):
        raise GmailPubSubAuthenticationError(
            "Pub/Sub bearer token is required"
        )

    token = value[7:].strip()
    if not token:
        raise GmailPubSubAuthenticationError(
            "Pub/Sub bearer token is required"
        )

    audience, expected_email = push_auth_config()

    try:
        claims = id_token.verify_oauth2_token(
            token,
            google_requests.Request(),
            audience=audience,
        )
    except Exception as exc:
        raise GmailPubSubAuthenticationError(
            "Pub/Sub identity token is invalid"
        ) from exc

    email = str(
        claims.get("email") or ""
    ).strip().lower()

    if email != expected_email:
        raise GmailPubSubAuthenticationError(
            "Pub/Sub service account is invalid"
        )
    if claims.get("email_verified") is not True:
        raise GmailPubSubAuthenticationError(
            "Pub/Sub service account email is not verified"
        )

    return claims


def decode_gmail_notification(
    envelope: dict,
) -> dict:
    message = envelope.get("message")
    if not isinstance(message, dict):
        raise GmailPubSubPayloadError(
            "Pub/Sub message is missing"
        )

    encoded = str(message.get("data") or "").strip()
    message_id = str(
        message.get("messageId") or ""
    ).strip()
    publish_time = (
        str(message.get("publishTime") or "").strip()
        or None
    )

    if not encoded or not message_id:
        raise GmailPubSubPayloadError(
            "Pub/Sub message data is incomplete"
        )

    try:
        padded = encoded + (
            "=" * (-len(encoded) % 4)
        )
        decoded = (
            base64.urlsafe_b64decode(
                padded.encode("ascii")
            )
            .decode("utf-8")
        )
        payload = json.loads(decoded)
    except (
        ValueError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as exc:
        raise GmailPubSubPayloadError(
            "Gmail notification data is invalid"
        ) from exc

    email_address = str(
        payload.get("emailAddress") or ""
    ).strip().lower()
    history_id = str(
        payload.get("historyId") or ""
    ).strip()

    if (
        not email_address
        or not history_id
        or not history_id.isdigit()
    ):
        raise GmailPubSubPayloadError(
            "Gmail notification payload is incomplete"
        )

    return {
        "email_address": email_address,
        "history_id": history_id,
        "message_id": message_id,
        "publish_time": publish_time,
    }
