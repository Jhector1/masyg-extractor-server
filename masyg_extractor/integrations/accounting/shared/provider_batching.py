from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class AccountingProviderBatchPolicy:
    provider: str
    envelope_key: str
    max_items: int
    max_payload_bytes: int | None = None


# QuickBooks Online Batch API:
# maximum 30 operations per BatchItemRequest.
QUICKBOOKS_DOCUMENT_BATCH_POLICY = (
    AccountingProviderBatchPolicy(
        provider="quickbooks",
        envelope_key="BatchItemRequest",
        max_items=30,
    )
)


# Xero supports multiple invoices in one request and recommends
# a practical ceiling of about 50 nodes while staying under its
# 3.5 MB request-size ceiling.
#
# Masyg uses a lower 3.0 MB serialized-payload ceiling so the
# provider wrapper and transport overhead retain safety margin.
XERO_DOCUMENT_BATCH_POLICY = (
    AccountingProviderBatchPolicy(
        provider="xero",
        envelope_key="Invoices",
        max_items=50,
        max_payload_bytes=3_000_000,
    )
)


_ACCOUNTING_PROVIDER_BATCH_POLICIES = {
    "quickbooks":
        QUICKBOOKS_DOCUMENT_BATCH_POLICY,
    "xero":
        XERO_DOCUMENT_BATCH_POLICY,
}


def accounting_provider_batch_policy(
    provider: str,
) -> AccountingProviderBatchPolicy:
    provider = str(
        provider or ""
    ).strip().lower()

    policy = (
        _ACCOUNTING_PROVIDER_BATCH_POLICIES
        .get(provider)
    )

    if policy is None:
        raise ValueError(
            "Unsupported accounting provider "
            "batch policy."
        )

    return policy


def _serialized_envelope_size(
    envelope_key: str,
    items: list[dict[str, Any]],
) -> int:
    payload = {
        envelope_key: items,
    }

    rendered = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")

    return len(rendered)


def chunk_accounting_provider_documents(
    provider: str,
    documents: Iterable[dict[str, Any]],
) -> list[list[dict[str, Any]]]:
    """
    Split prepared provider document payloads into safe request chunks.

    This owner is deliberately transport-free:
      - no provider client
      - no Firestore
      - no mutation
      - no send/create call

    The caller remains responsible for durable claims and provider
    result settlement.
    """

    policy = accounting_provider_batch_policy(
        provider
    )

    rendered_documents = list(
        documents
    )

    if not rendered_documents:
        return []

    chunks: list[
        list[dict[str, Any]]
    ] = []

    current: list[
        dict[str, Any]
    ] = []

    for document in rendered_documents:
        if not isinstance(document, dict):
            raise ValueError(
                "Provider document payload must "
                "be an object."
            )

        candidate = [
            *current,
            document,
        ]

        exceeds_count = (
            len(candidate) >
            policy.max_items
        )

        exceeds_bytes = False

        if (
            policy.max_payload_bytes
            is not None
        ):
            exceeds_bytes = (
                _serialized_envelope_size(
                    policy.envelope_key,
                    candidate,
                )
                >
                policy.max_payload_bytes
            )

        if (
            not exceeds_count
            and not exceeds_bytes
        ):
            current = candidate
            continue

        if not current:
            raise ValueError(
                f"A single {policy.provider} "
                "document exceeds the safe "
                "provider request size."
            )

        chunks.append(current)

        current = [document]

        if (
            policy.max_payload_bytes
            is not None
            and
            _serialized_envelope_size(
                policy.envelope_key,
                current,
            )
            >
            policy.max_payload_bytes
        ):
            raise ValueError(
                f"A single {policy.provider} "
                "document exceeds the safe "
                "provider request size."
            )

    if current:
        chunks.append(current)

    return chunks
