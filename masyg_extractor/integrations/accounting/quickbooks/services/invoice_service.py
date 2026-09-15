from typing import List, Dict, Any

from masyg_extractor.integrations.accounting.core.integration_context import IntegrationContext
from masyg_extractor.integrations.accounting.core.models import Invoice
from masyg_extractor.integrations.accounting.quickbooks.base_adapter import IntegrationClientAdapter
from masyg_extractor.integrations.accounting.quickbooks.services.audit_log_service import audit_op
from masyg_extractor.integrations.accounting.quickbooks.services.document_service import DocumentService
from masyg_extractor.integrations.accounting.shared.firestore_repository import QuickBooksFirestoreService
from masyg_extractor.integrations.accounting.shared.single_send_compat import (
    run_single_via_bulk,
)


class InvoiceService(DocumentService):
    def __init__(
        self,
        context: IntegrationContext,
        repo: QuickBooksFirestoreService,
        client: IntegrationClientAdapter,
    ):
        super().__init__("INV", "Invoice", context, repo, client)

    @audit_op(doc_type="Invoice", entity_type="Invoice", operation="submit")
    async def send_invoice(
        self,
        invoice: Invoice,
        share_progress: float,
    ) -> Dict[str, Any] | str:
        return await run_single_via_bulk(
            invoice,
            share_progress,
            send_bulk=super().send_document_in_bulk,
            repo=self.repo,
            record_type="invoices",
        )

    async def send_invoice_in_bulk(self, invoices: List[Invoice], share_progress: float) -> Dict[str, Any]:
        return await super().send_document_in_bulk(invoices, share_progress)
