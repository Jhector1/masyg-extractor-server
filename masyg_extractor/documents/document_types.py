from __future__ import annotations

from enum import Enum
from typing import Any


class DocumentType(str, Enum):
    VENDOR_BILL = "vendor_bill"
    SALES_INVOICE = "sales_invoice"
    PURCHASE_RECEIPT = "purchase_receipt"
    SALES_RECEIPT = "sales_receipt"
    VENDOR_CREDIT = "vendor_credit"
    CUSTOMER_CREDIT = "customer_credit"
    PURCHASE_ORDER = "purchase_order"
    QUOTE_ESTIMATE = "quote_estimate"
    BANK_STATEMENT = "bank_statement"
    CREDIT_CARD_STATEMENT = "credit_card_statement"
    OTHER = "other"


DOCUMENT_TYPE_ALIASES: dict[str, DocumentType] = {
    "vendor_bill": DocumentType.VENDOR_BILL,
    "bill": DocumentType.VENDOR_BILL,
    "supplier_invoice": DocumentType.VENDOR_BILL,
    "vendor_invoice": DocumentType.VENDOR_BILL,
    # Historical extractor output used "invoice" for purchasing documents.
    "invoice": DocumentType.VENDOR_BILL,
    "sales_invoice": DocumentType.SALES_INVOICE,
    "customer_invoice": DocumentType.SALES_INVOICE,
    "purchase_receipt": DocumentType.PURCHASE_RECEIPT,
    "receipt": DocumentType.PURCHASE_RECEIPT,
    "sales_receipt": DocumentType.SALES_RECEIPT,
    "vendor_credit": DocumentType.VENDOR_CREDIT,
    "supplier_credit": DocumentType.VENDOR_CREDIT,
    "customer_credit": DocumentType.CUSTOMER_CREDIT,
    "credit_memo": DocumentType.CUSTOMER_CREDIT,
    "purchase_order": DocumentType.PURCHASE_ORDER,
    "po": DocumentType.PURCHASE_ORDER,
    "quote_estimate": DocumentType.QUOTE_ESTIMATE,
    "quote": DocumentType.QUOTE_ESTIMATE,
    "bid": DocumentType.QUOTE_ESTIMATE,
    "estimate": DocumentType.QUOTE_ESTIMATE,
    "bank_statement": DocumentType.BANK_STATEMENT,
    "statement": DocumentType.BANK_STATEMENT,
    "credit_card_statement": DocumentType.CREDIT_CARD_STATEMENT,
    "card_statement": DocumentType.CREDIT_CARD_STATEMENT,
    "other": DocumentType.OTHER,
}


def _token(value: Any) -> str:
    return (
        str(value or "")
        .strip()
        .lower()
        .replace("-", "_")
        .replace(" ", "_")
    )


def canonical_document_type(value: Any) -> DocumentType:
    return DOCUMENT_TYPE_ALIASES.get(_token(value), DocumentType.OTHER)


def normalize_extracted_document(payload: Any) -> Any:
    """Normalize extractor output without coupling it to an accounting provider."""
    if not isinstance(payload, dict):
        return payload

    normalized = dict(payload)
    raw_type = normalized.get("documentType", normalized.get("document_type"))
    normalized["documentType"] = canonical_document_type(raw_type).value
    normalized["documentTypeSource"] = str(
        normalized.get("documentTypeSource")
        or normalized.get("document_type_source")
        or "extractor"
    )

    raw_confidence = normalized.get(
        "documentTypeConfidence",
        normalized.get("document_type_confidence"),
    )
    if raw_confidence not in (None, ""):
        try:
            confidence = float(raw_confidence)
        except (TypeError, ValueError):
            confidence = None
        if confidence is not None:
            normalized["documentTypeConfidence"] = max(0.0, min(1.0, confidence))

    normalized.pop("document_type", None)
    normalized.pop("document_type_source", None)
    normalized.pop("document_type_confidence", None)
    return normalized
