import asyncio
from itertools import chain
from typing import Dict, List, Optional, cast

from fastapi import Request

from masyg_extractor.integration_v4.core.integration_context import IntegrationContext
from masyg_extractor.integration_v4.domain.models import Item
from masyg_extractor.integration_v4.entity_helper import EntityHelper
from masyg_extractor.integration_v4.intergrate.baseAdapter import IntegrationClientAdapter
from masyg_extractor.integration_v4.repository.firestore_repository import QuickBooksFirestoreService
from masyg_extractor.integration_v4.utils import extract_uuid
from masyg_extractor.integrations.xero.services.item_services import generate_sku
from masyg_extractor.services.file_extractor_service import remove_non_alphanumeric
from masyg_extractor.services.my_log import logger
from masyg_extractor.integrations.quickbooks.quickbooks_client import quickbooks_request
from masyg_extractor.services.progress_log import IntegrationsProgressLog


class ItemService:
    def __init__(
            self,
            context: IntegrationContext,
            repo: QuickBooksFirestoreService,
            client: IntegrationClientAdapter,
    ):
        self.context = context
        self.repo = repo
        self.client = client
        self.entity_helper = EntityHelper(context, repo, client)

    @staticmethod
    def get_sanitized_name(name: Optional[str], max_length: int = 50) -> str:
        """
        Sanitize and truncate the item name.
        Returns "Unnamed Item" if the result is empty.
        """
        try:
            raw_name = (name or "Unnamed Item")[:max_length]
            sanitized = remove_non_alphanumeric(raw_name)
            return sanitized or "Unnamed Item"
        except Exception as e:
            logger.error(f"Error sanitizing name '{name}': {str(e)}")
            return "Unnamed Item"

    @staticmethod
    def create_single_item_payload(item: Item) -> dict:
        """
        Generate the payload for creating a single item.
        Uses a cleaned name and either the given SKU or one generated from the name.
        """
        try:
            name = ItemService.get_sanitized_name(item.name)
            sku = item.sku if item.sku else generate_sku(name)
            return {
                "Code": sku,
                "Name": name,
                "Description": item.description,
            }
        except Exception as e:
            logger.error(f"Error creating single item payload for item '{item.name}': {str(e)}")
            return {}

    @staticmethod
    def create_bulk_item_payload(item: Item) -> dict:
        """
        Generate payload for creating an item in bulk.
        Combines a truncated UUID (from transaction_id) with a short SKU segment.
        """
        try:
            name = ItemService.get_sanitized_name(item.name)
            if not item.sku:
                item.sku = generate_sku(name)
            # Create a short code: first part from UUID and second from SKU (max 5 characters)
            code_prefix = extract_uuid(item.transaction_id)[:20]
            code_suffix = item.sku[:5] if item.sku else generate_sku(name)[:5]
            # code_suffix =  generate_sku(name)[:5]

            return {
                "Code": f"{code_prefix}_{code_suffix}",
                "Name": name,
                "Description": item.description
            }
        except Exception as e:
            logger.error(f"Error creating bulk item payload for item '{item.name}': {str(e)}")
            return {}

    async def check_item_exists(self, item: Item) -> bool:
        """
        Check if an item exists based on its ID or Name.
        """
        try:
            if item.id:
                return await self.entity_helper.check_entity_exists("Item", "Id", item.id)
            if item.name:
                return await self.entity_helper.check_entity_exists("Item", "Name", item.name)
            return False
        except Exception as e:
            logger.error(f"Error checking existence for item '{item.name}': {str(e)}")
            return False

    async def get_default_service_accounts(self) -> tuple[str, str]:
        """
        Retrieve default income and expense accounts from QuickBooks.
        Uses primary criteria for account selection with fallback to Uncategorized types.
        """
        query = "SELECT * FROM Account WHERE Active = true"
        params = {"query": query}

        try:
            response = await self.client.request(
                self.repo.get_integration_token(),
                "query",
                method="GET",
                params=params,
            )

            accounts = response.get("QueryResponse", {}).get("Account")
            if not accounts:
                raise Exception("No accounts returned from QuickBooks")

            income_account_id = None
            expense_account_id = None

            # First pass: preferred criteria.
            for acc in accounts:
                if (
                        not income_account_id
                        and acc.get("AccountType") == "Income"
                        and acc.get("AccountSubType") in ["SalesOfProductIncome", "ServiceFeeIncome"]
                ):
                    income_account_id = acc["Id"]
                if (
                        not expense_account_id
                        and acc.get("AccountType") == "Cost of Goods Sold"
                ):
                    expense_account_id = acc["Id"]
                if income_account_id and expense_account_id:
                    break

            # Second pass: fall back to Uncategorized accounts.
            if not income_account_id:
                for acc in accounts:
                    if (
                            acc.get("AccountType") == "Income"
                            and acc.get("AccountSubType") == "UncategorizedIncome"
                    ):
                        income_account_id = acc["Id"]
                        break

            if not expense_account_id:
                for acc in accounts:
                    if (
                            acc.get("AccountType") == "Cost of Goods Sold"
                            and acc.get("AccountSubType") == "UncategorizedExpense"
                    ):
                        expense_account_id = acc["Id"]
                        break

            if not income_account_id or not expense_account_id:
                raise Exception("Could not find suitable Income/Expense accounts in QuickBooks.")

            return income_account_id, expense_account_id

        except Exception as e:
            logger.error(f"Error fetching default service accounts: {e}")
            # Return empty strings as a graceful fallback.
            return "", ""

    async def create_item(self, item: Item) -> str:
        """
        Create a single item in the target system.
        Prepares a sanitized payload and delegates the creation to the entity helper.
        """
        try:
            payload = {"Items": [self.create_single_item_payload(item)]}
            logger.info(f"Creating item with payload: {payload}")
            return await self.entity_helper.create_entity("Item", payload)
        except Exception as e:
            logger.error(f"Error creating item '{item.name}': {str(e)}")
            return ""

    async def create_item_in_bulk(self, local_items: Dict[str, List[Item]]) -> Dict[str, List[Item]]:
        """
        Create items in bulk in the target system.

        1. Flattens the provided dictionary of items.
        2. Filters out any items that already exist.
        3. Generates payloads for new items.
        4. Merges the newly created items with the current items.
        """
        try:
            name_key = "Name"
            id_key = "Code" #using instead of real_ids
            entity = "Items"

            # Flatten the list of items from dictionary values.
            flat_items: List[Item] = [item for sublist in local_items.values() for item in sublist]

            # Filter out existing items based on name or identifier.
            non_existing_items = cast(
                List[Item],
                await self.entity_helper.get_non_existing_entities(flat_items, entity, name_key, id_key),
            )

            # Prepare payload for each new item.
            payload_items = [self.create_bulk_item_payload(item) for item in non_existing_items]
            payload = {"Items": payload_items}

            logger.info(f"Creating bulk items with payload: {payload}")
            all_current_items = cast(
                Dict[str, List[Item]],
                await self.entity_helper.create_entity_in_bulk_and_merge_with_current(
                    local_items, entity, payload, name_key, id_key
                ),
            )

            return all_current_items
        except Exception as e:
            logger.error(f"Error creating items in bulk: {str(e)}")
            return {}
