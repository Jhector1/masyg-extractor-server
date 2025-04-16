
from typing import List, Dict, Any, Optional

from masyg_extractor.integration_v2.core.integration_context import IntegrationContext
from masyg_extractor.integration_v2.domain.models import Item, Customer, Invoice, SalesReceipt
from masyg_extractor.integration_v2.entity_helper import EntityHelper
from masyg_extractor.integration_v2.intergrate.baseAdapter import IntegrationClientAdapter
from masyg_extractor.integration_v2.intergrate.quickbooks.services.document_service import DocumentService
from masyg_extractor.integration_v2.intergrate.quickbooks.services.item_service import ItemService
from masyg_extractor.integration_v2.repository.firestore_repository import QuickBooksFirestoreService

from masyg_extractor.integration_v2.intergrate.quickbooks.services.customer_service import CustomerService

class SalesReceiptService(DocumentService):
    def __init__(self, context: IntegrationContext, repo: QuickBooksFirestoreService, client: IntegrationClientAdapter):
        super().__init__("SR", "SalesReceipt", context, repo, client)
        self.context = context
        self.repo = repo
        self.client = client
        self.item_service = ItemService(context, repo, client)

        self.customer_service = CustomerService(context, repo, client)
        self.entity_helper = EntityHelper(context, repo, client)

    async def send_receipt(
            self,

            salesReceipt: SalesReceipt,
            # items: List[Item],
            # transaction_id: str,

            # customer: Customer,
            share_progress: float

    ) -> Dict[str, Any]:
        """
        Asynchronously creates an invoice in QuickBooks and stores key invoice info in Firestore.
        """
        return await super().send_document(salesReceipt, share_progress)
