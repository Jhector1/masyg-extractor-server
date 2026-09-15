import pytest

from masyg_extractor.integrations.accounting.shared.provider_batching import (
    QUICKBOOKS_DOCUMENT_BATCH_POLICY,
    XERO_DOCUMENT_BATCH_POLICY,
    accounting_provider_batch_policy,
    chunk_accounting_provider_documents,
)


def document(index: int) -> dict:
    return {
        "id": index,
        "payload": f"document-{index}",
    }


def flatten(chunks):
    return [
        item
        for chunk in chunks
        for item in chunk
    ]


def test_quickbooks_policy_is_capped_at_30_operations():
    policy = (
        accounting_provider_batch_policy(
            "quickbooks"
        )
    )

    assert policy == (
        QUICKBOOKS_DOCUMENT_BATCH_POLICY
    )

    assert policy.envelope_key == (
        "BatchItemRequest"
    )

    assert policy.max_items == 30
    assert policy.max_payload_bytes is None


def test_quickbooks_30_documents_stay_in_one_batch():
    documents = [
        document(index)
        for index in range(30)
    ]

    chunks = (
        chunk_accounting_provider_documents(
            "quickbooks",
            documents,
        )
    )

    assert [
        len(chunk)
        for chunk in chunks
    ] == [30]

    assert flatten(chunks) == documents


def test_quickbooks_31_documents_split_30_plus_1_without_reordering():
    documents = [
        document(index)
        for index in range(31)
    ]

    chunks = (
        chunk_accounting_provider_documents(
            "quickbooks",
            documents,
        )
    )

    assert [
        len(chunk)
        for chunk in chunks
    ] == [30, 1]

    assert flatten(chunks) == documents


def test_xero_policy_uses_practical_50_node_ceiling_and_payload_margin():
    policy = (
        accounting_provider_batch_policy(
            "xero"
        )
    )

    assert policy == (
        XERO_DOCUMENT_BATCH_POLICY
    )

    assert policy.envelope_key == (
        "Invoices"
    )

    assert policy.max_items == 50

    assert (
        policy.max_payload_bytes
        == 3_000_000
    )


def test_xero_51_small_documents_split_50_plus_1_without_reordering():
    documents = [
        document(index)
        for index in range(51)
    ]

    chunks = (
        chunk_accounting_provider_documents(
            "xero",
            documents,
        )
    )

    assert [
        len(chunk)
        for chunk in chunks
    ] == [50, 1]

    assert flatten(chunks) == documents


def test_xero_splits_before_safe_payload_byte_ceiling():
    documents = [
        {
            "id": 1,
            "memo": "a" * 1_700_000,
        },
        {
            "id": 2,
            "memo": "b" * 1_700_000,
        },
    ]

    chunks = (
        chunk_accounting_provider_documents(
            "xero",
            documents,
        )
    )

    assert [
        len(chunk)
        for chunk in chunks
    ] == [1, 1]

    assert flatten(chunks) == documents


def test_xero_rejects_one_document_that_exceeds_safe_payload_ceiling():
    oversized = {
        "id": 1,
        "memo": "x" * 3_100_000,
    }

    with pytest.raises(
        ValueError,
        match=(
            "single xero document exceeds"
        ),
    ):
        (
            chunk_accounting_provider_documents(
                "xero",
                [oversized],
            )
        )


def test_empty_document_list_requires_no_provider_request():
    assert (
        chunk_accounting_provider_documents(
            "quickbooks",
            [],
        )
        == []
    )

    assert (
        chunk_accounting_provider_documents(
            "xero",
            [],
        )
        == []
    )


def test_unknown_provider_is_rejected():
    with pytest.raises(
        ValueError,
        match="Unsupported accounting provider",
    ):
        (
            chunk_accounting_provider_documents(
                "invented",
                [document(1)],
            )
        )


def test_non_object_provider_payload_is_rejected():
    with pytest.raises(
        ValueError,
        match="must be an object",
    ):
        (
            chunk_accounting_provider_documents(
                "quickbooks",
                [
                    document(1),
                    "invalid",
                ],
            )
        )
