
from typing import List, Dict, Any, Optional

from masyg_extractor.integration_qb_v5.core.integration_context import IntegrationContext
from masyg_extractor.integration_qb_v5.domain.models import Item, Customer, Invoice, SalesReceipt, Document
from masyg_extractor.integration_qb_v5.entity_helper import EntityHelper
from masyg_extractor.integration_qb_v5.intergrate.baseAdapter import IntegrationClientAdapter
from masyg_extractor.integration_qb_v5.intergrate.quickbooks.services.audit_log_service import audit_op
from masyg_extractor.integration_qb_v5.intergrate.quickbooks.services.document_service import DocumentService
from masyg_extractor.integration_qb_v5.intergrate.quickbooks.services.item_service import ItemService
from masyg_extractor.integration_qb_v5.repository.firestore_repository import QuickBooksFirestoreService

from masyg_extractor.integration_qb_v5.intergrate.quickbooks.services.customer_service import CustomerService

# -----------------------------
# Subclass usage (decorate here)
# -----------------------------
from masyg_extractor.integration_qb_v5.intergrate.quickbooks.services.customer_service import CustomerService


class SalesReceiptService(DocumentService):
    def __init__(
        self,
        context: IntegrationContext,
        repo: QuickBooksFirestoreService,
        client: IntegrationClientAdapter,
    ):
        super().__init__("SR", "SalesReceipt", context, repo, client)

    @audit_op(doc_type="SalesReceipt", entity_type="SalesReceipt", operation="submit")
    async def send_receipt(self, sales_receipt: Document, share_progress: float) -> Dict[str, Any]:
        return await super().send_document(sales_receipt, share_progress)
