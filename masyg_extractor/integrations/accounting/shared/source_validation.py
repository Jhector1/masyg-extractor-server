from __future__ import annotations

from typing import Any, Mapping


CUSTOMER_NAME_REQUIRED_INTENTS = frozenset(
    {
        "create_ar_invoice",
        "create_ap_bill",
        "create_sales_receipt",
    }
)


def require_accounting_customer_name(
    document: Mapping[str, Any],
) -> str:
    """
    Return the required accounting customer/counterparty name.

    Do not synthesize or silently default this value. Provider document
    factories require a real name before a document may be materialized.
    """

    if not isinstance(document, Mapping):
        raise TypeError(
            "Accounting source document must be a mapping."
        )

    value = document.get("customer_name")

    if (
        not isinstance(value, str)
        or not value.strip()
    ):
        raise ValueError(
            "Customer name is required for accounting execution."
        )

    return value.strip()


def validate_accounting_source_document(
    document: Mapping[str, Any],
    *,
    accounting_intent: str,
) -> None:
    """
    Validate source fields required before provider materialization.

    Keep this boundary provider-HTTP-free. It validates only fields whose
    absence is already known to make the canonical provider factories
    unable to materialize the document.
    """

    if not isinstance(document, Mapping):
        raise TypeError(
            "Accounting source document must be a mapping."
        )

    intent = str(
        accounting_intent or ""
    ).strip()

    if not intent:
        raise ValueError(
            "Accounting intent is required for source validation."
        )

    if intent in CUSTOMER_NAME_REQUIRED_INTENTS:
        require_accounting_customer_name(
            document
        )
