from typing import Dict, Any, Optional
from fastapi import Request

from masyg_extractor.integration_qb_v5.core.integration_context import IntegrationContext
from masyg_extractor.integration_qb_v5.domain.models import Account
from masyg_extractor.integration_qb_v5.entity_helper import EntityHelper
from masyg_extractor.integration_qb_v5.intergrate.baseAdapter import IntegrationClientAdapter
from masyg_extractor.integration_qb_v5.repository.firestore_repository import QuickBooksFirestoreService
from masyg_extractor.services.file_extractor_service import remove_non_alphanumeric
from masyg_extractor.services.my_log import logger
from masyg_extractor.integrations.quickbooks.quickbooks_client import quickbooks_request


class AccountService:
    def __init__(self, context: IntegrationContext, repo: QuickBooksFirestoreService, client: IntegrationClientAdapter):
        self.entity_helper = EntityHelper(context, repo, client)

    async def get_all_accounts(self):
        # where_clause = "Type == 'REVENUE'"
        return await self.entity_helper.get_all_entities(
            "Account",
            id_field="Id",
            # where_clause=where_clause
        )

    async def check_account_exists(self,
        account: Account,

    ) -> bool:

        if account.id:

            return await self.entity_helper.check_entity_exists("Account", "Id", account.id)
        if account.name:
            return await self.entity_helper.check_entity_exists( "Account", "Name", account.name)
        return False


    async def create_account(
        self,
            account: Account,

    ) -> str:
        return await self.entity_helper.create_entity("Account", account.name)
