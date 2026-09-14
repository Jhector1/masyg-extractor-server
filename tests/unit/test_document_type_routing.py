from masyg_extractor.documents.document_types import (
    DocumentType,
    canonical_document_type,
    normalize_extracted_document,
)
from masyg_extractor.integrations.accounting.core.document_intent import (
    AccountingIntent,
    default_accounting_intent,
)


def test_legacy_document_types_normalize_to_canonical_values():
    assert canonical_document_type("bill") is DocumentType.VENDOR_BILL
    assert canonical_document_type("invoice") is DocumentType.VENDOR_BILL
    assert canonical_document_type("receipt") is DocumentType.PURCHASE_RECEIPT
    assert canonical_document_type("quote") is DocumentType.QUOTE_ESTIMATE
    assert canonical_document_type("statement") is DocumentType.BANK_STATEMENT


def test_extractor_metadata_is_provider_neutral_and_confidence_is_clamped():
    normalized = normalize_extracted_document(
        {
            "documentType": "purchase-order",
            "documentTypeConfidence": 1.4,
            "vendor_name": "Example",
            "line_items": [],
        }
    )

    assert normalized["documentType"] == "purchase_order"
    assert normalized["documentTypeConfidence"] == 1.0
    assert normalized["documentTypeSource"] == "extractor"
    assert normalized["vendor_name"] == "Example"


def test_unknown_document_type_requires_review_instead_of_guessing():
    normalized = normalize_extracted_document({"documentType": "mystery"})
    assert normalized["documentType"] == "other"
    assert default_accounting_intent("other") is AccountingIntent.REVIEW_REQUIRED


def test_accounting_intent_is_separate_from_document_classification():
    assert default_accounting_intent("vendor_bill") is AccountingIntent.CREATE_AP_BILL
    assert default_accounting_intent("sales_invoice") is AccountingIntent.CREATE_AR_INVOICE
    assert default_accounting_intent("bank_statement") is AccountingIntent.RECONCILE_ONLY
