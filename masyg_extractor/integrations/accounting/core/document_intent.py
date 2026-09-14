from __future__ import annotations

from enum import Enum

from masyg_extractor.documents.document_types import (
    DocumentType,
    canonical_document_type,
)


class AccountingIntent(str, Enum):
    CREATE_AP_BILL = "create_ap_bill"
    CREATE_AR_INVOICE = "create_ar_invoice"
    RECONCILE_EXPENSE = "reconcile_expense"
    CREATE_SALES_RECEIPT = "create_sales_receipt"
    CREATE_VENDOR_CREDIT = "create_vendor_credit"
    CREATE_CUSTOMER_CREDIT = "create_customer_credit"
    CREATE_PURCHASE_ORDER = "create_purchase_order"
    CREATE_ESTIMATE = "create_estimate"
    RECONCILE_ONLY = "reconcile_only"
    REVIEW_REQUIRED = "review_required"


DEFAULT_ACCOUNTING_INTENT_BY_DOCUMENT_TYPE = {
    DocumentType.VENDOR_BILL: AccountingIntent.CREATE_AP_BILL,
    DocumentType.SALES_INVOICE: AccountingIntent.CREATE_AR_INVOICE,
    DocumentType.PURCHASE_RECEIPT: AccountingIntent.RECONCILE_EXPENSE,
    DocumentType.SALES_RECEIPT: AccountingIntent.CREATE_SALES_RECEIPT,
    DocumentType.VENDOR_CREDIT: AccountingIntent.CREATE_VENDOR_CREDIT,
    DocumentType.CUSTOMER_CREDIT: AccountingIntent.CREATE_CUSTOMER_CREDIT,
    DocumentType.PURCHASE_ORDER: AccountingIntent.CREATE_PURCHASE_ORDER,
    DocumentType.QUOTE_ESTIMATE: AccountingIntent.CREATE_ESTIMATE,
    DocumentType.BANK_STATEMENT: AccountingIntent.RECONCILE_ONLY,
    DocumentType.CREDIT_CARD_STATEMENT: AccountingIntent.RECONCILE_ONLY,
    DocumentType.OTHER: AccountingIntent.REVIEW_REQUIRED,
}


def default_accounting_intent(document_type: DocumentType | str) -> AccountingIntent:
    canonical = (
        document_type
        if isinstance(document_type, DocumentType)
        else canonical_document_type(document_type)
    )
    return DEFAULT_ACCOUNTING_INTENT_BY_DOCUMENT_TYPE[canonical]
