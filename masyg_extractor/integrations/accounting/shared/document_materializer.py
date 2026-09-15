from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from masyg_extractor.integrations.accounting.core.models import (
    Document,
)


AccountingDocumentFactory = Callable[
    [
        str,
        str,
        dict[str, Any],
    ],
    Document,
]


def materialize_accounting_document(
    document: Mapping[str, Any],
    *,
    group_id: str,
    file_id: str,
    factory: AccountingDocumentFactory,
) -> Document:
    """
    Materialize one persisted accounting document while preserving
    its canonical Firestore identity exactly.

    Field interpretation remains owned by the existing provider
    document factory. This shared boundary owns identity only.

    In particular this function must never:
      - reconstruct a transaction ID from a filename/key
      - append legacy suffixes such as "-0"
      - call legacy normalize_payload()
      - infer provider accounting meaning
      - perform Firestore or provider writes
    """

    group_id = str(
        group_id or ""
    ).strip()

    file_id = str(
        file_id or ""
    ).strip()

    if not group_id or not file_id:
        raise ValueError(
            "Canonical group_id and file_id are required."
        )

    if not isinstance(document, Mapping):
        raise TypeError(
            "Persisted accounting document must be a mapping."
        )

    if not callable(factory):
        raise TypeError(
            "Accounting document factory must be callable."
        )

    # Give the provider factory a copy of the persisted source shape.
    # It may interpret provider-facing fields, but it does not own
    # canonical identity construction.
    details = dict(document)

    materialized = factory(
        file_id,
        group_id,
        details,
    )

    if not isinstance(materialized, Document):
        raise TypeError(
            "Accounting document factory must return Document."
        )

    if (
        materialized.transaction_id
        != file_id
    ):
        raise ValueError(
            "Accounting document factory changed canonical file_id."
        )

    if (
        materialized.group_id
        != group_id
    ):
        raise ValueError(
            "Accounting document factory changed canonical group_id."
        )

    return materialized
