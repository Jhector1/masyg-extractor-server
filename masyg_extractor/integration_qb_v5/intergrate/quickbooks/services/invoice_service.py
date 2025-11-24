from typing import List, Dict, Any

from masyg_extractor.integration_qb_v5.core.integration_context import IntegrationContext
from masyg_extractor.integration_qb_v5.domain.models import Invoice
from masyg_extractor.integration_qb_v5.intergrate.baseAdapter import IntegrationClientAdapter
from masyg_extractor.integration_qb_v5.intergrate.quickbooks.services.audit_log_service import audit_op
from masyg_extractor.integration_qb_v5.intergrate.quickbooks.services.document_service import DocumentService
from masyg_extractor.integration_qb_v5.repository.firestore_repository import QuickBooksFirestoreService


class InvoiceService(DocumentService):
    def __init__(
        self,
        context: IntegrationContext,
        repo: QuickBooksFirestoreService,
        client: IntegrationClientAdapter,
    ):
        super().__init__("INV", "Invoice", context, repo, client)

    @audit_op(doc_type="Invoice", entity_type="Invoice", operation="submit")
    async def send_invoice(self, invoice: Invoice, share_progress: float) -> Dict[str, Any]:
        return await super().send_document(invoice, share_progress)

    async def send_invoice_in_bulk(self, invoices: List[Invoice], share_progress: float) -> Dict[str, Any]:
        return await super().send_document_in_bulk(invoices, share_progress)
