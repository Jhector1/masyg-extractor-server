import base64
import json

import pytest

from masyg_extractor.integrations.document_sources.gmail.pubsub import (
    GmailPubSubPayloadError,
    decode_gmail_notification,
)


def encoded(value: dict) -> str:
    raw = json.dumps(
        value,
        separators=(",", ":"),
    ).encode("utf-8")
    return (
        base64.urlsafe_b64encode(raw)
        .decode("ascii")
        .rstrip("=")
    )


def test_decodes_gmail_pubsub_payload():
    result = decode_gmail_notification(
        {
            "message": {
                "data": encoded(
                    {
                        "emailAddress":
                            "USER@example.com",
                        "historyId": "12345",
                    }
                ),
                "messageId": "pubsub-1",
                "publishTime":
                    "2026-09-20T22:00:00Z",
            }
        }
    )

    assert result == {
        "email_address": "user@example.com",
        "history_id": "12345",
        "message_id": "pubsub-1",
        "publish_time":
            "2026-09-20T22:00:00Z",
    }


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"message": {}},
        {
            "message": {
                "data": "not-valid-json",
                "messageId": "1",
            }
        },
        {
            "message": {
                "data": encoded(
                    {
                        "emailAddress":
                            "a@example.com",
                        "historyId": "abc",
                    }
                ),
                "messageId": "1",
            }
        },
    ],
)
def test_rejects_invalid_pubsub_payload(
    payload,
):
    with pytest.raises(
        GmailPubSubPayloadError
    ):
        decode_gmail_notification(
            payload
        )
