from pathlib import Path

from masyg_extractor.integrations.document_sources.gmail.identity import (
    gmail_group_id,
    gmail_part_key,
    iter_gmail_parts,
)


ROOT = Path(__file__).resolve().parents[2]


def test_attachment_id_is_not_part_of_durable_part_identity():
    first = {
        "partId": "2",
        "filename": "invoice.pdf",
        "mimeType": "application/pdf",
        "body": {
            "attachmentId": "opaque-token-a",
        },
    }
    second = {
        **first,
        "body": {
            "attachmentId": "opaque-token-b",
        },
    }

    first_key = gmail_part_key(
        first,
        mime_path="0.1",
    )
    second_key = gmail_part_key(
        second,
        mime_path="0.1",
    )

    assert first_key == "part:2"
    assert second_key == first_key
    assert (
        gmail_group_id(
            "message-1",
            first_key,
        )
        == gmail_group_id(
            "message-1",
            second_key,
        )
    )


def test_mime_path_fallback_is_stable_when_attachment_id_changes():
    first = {
        "filename": "receipt.jpg",
        "mimeType": "image/jpeg",
        "body": {
            "attachmentId": "opaque-a",
        },
    }
    second = {
        **first,
        "body": {
            "attachmentId": "opaque-b",
        },
    }

    assert gmail_part_key(
        first,
        mime_path="0.3",
    ) == gmail_part_key(
        second,
        mime_path="0.3",
    )


def test_same_filename_in_different_mime_positions_has_distinct_identity():
    part = {
        "filename": "invoice.pdf",
        "mimeType": "application/pdf",
        "body": {
            "attachmentId": "opaque",
        },
    }

    assert gmail_part_key(
        part,
        mime_path="0.1",
    ) != gmail_part_key(
        part,
        mime_path="0.2",
    )


def test_recursive_mime_walk_has_deterministic_paths():
    payload = {
        "parts": [
            {
                "filename": "one.pdf",
            },
            {
                "parts": [
                    {
                        "filename": "two.pdf",
                    },
                ],
            },
        ],
    }

    paths = [
        path
        for _, path in iter_gmail_parts(
            payload,
        )
    ]

    assert paths == [
        "0",
        "0.0",
        "0.1",
        "0.1.0",
    ]


def test_retrieval_still_uses_current_attachment_id():
    processor = (
        ROOT
        / "masyg_extractor/integrations/document_sources/gmail/processor.py"
    ).read_text()
    identity = (
        ROOT
        / "masyg_extractor/integrations/document_sources/gmail/identity.py"
    ).read_text()

    assert 'body.get("attachmentId")' not in identity
    assert 'body.get("attachmentId")' in processor
    assert "get_gmail_attachment(" in processor

    assert "gmail_part_key(" in processor
    assert "gmail_group_id(" in processor
    assert "iter_gmail_parts(" in processor


def test_legacy_claim_reuse_is_unambiguous_and_success_checked():
    processor = (
        ROOT
        / "masyg_extractor/integrations/document_sources/gmail/processor.py"
    ).read_text()
    repository = (
        ROOT
        / "masyg_extractor/integrations/document_sources/gmail/repository.py"
    ).read_text()

    assert (
        "def legacy_processed_attachment_group("
        in repository
    )
    assert (
        'part_key.startswith("attachment:")'
        in repository
    )
    assert (
        'current.get("status") != "processed"'
        in repository
    )
    assert "if len(group_ids) != 1:" in repository

    assert "filename_counts" in processor
    assert (
        "repository.legacy_processed_attachment_group"
        in processor
    )

    legacy_lookup = processor.index(
        "repository.legacy_processed_attachment_group"
    )
    legacy_success = processor.index(
        "repository.group_ingestion_succeeded",
        legacy_lookup,
    )
    migrated_mark = processor.index(
        "repository.mark_attachment_processed",
        legacy_success,
    )

    assert legacy_lookup < legacy_success < migrated_mark
