import asyncio
from typing import Dict, Any, Optional
from fastapi import Request

from masyg_extractor.integration_v2.core.integration_context import IntegrationContext
from masyg_extractor.integration_v2.domain.models import Item
from masyg_extractor.integration_v2.entity_helper import EntityHelper
from masyg_extractor.integration_v2.intergrate.baseAdapter import IntegrationClientAdapter
from masyg_extractor.integration_v2.repository.firestore_repository import QuickBooksFirestoreService
from masyg_extractor.services.file_extractor_service import remove_non_alphanumeric
from masyg_extractor.services.my_log import logger
from masyg_extractor.integrations.quickbooks.quickbooks_client import quickbooks_request
from masyg_extractor.services.progress_log import IntegrationsProgressLog


class ItemService:
    def __init__(self, context: IntegrationContext, repo: QuickBooksFirestoreService, client: IntegrationClientAdapter):
        self.context = context
        self.repo = repo
        self.client = client
        self.entity_helper = EntityHelper(context, repo, client)



    async def check_item_exists(self,
        item: Item
    ) -> bool:

        if item.id:

            return await self.entity_helper.check_entity_exists("Item", "Id", item.id)
        if item.name:
            return await self.entity_helper.check_entity_exists( "Item", "Name", item.name)
        return False



    async def get_default_service_accounts(self,) -> tuple[str, str]:
        try:
            query = "SELECT * FROM Account WHERE Active = true"
            params = {"query": query}
            response = await self.client.request(self.repo.get_integration_token(),"query",  method="GET", params=params)

            if "QueryResponse" not in response or "Account" not in response["QueryResponse"]:
                raise Exception("No accounts returned from QuickBooks")
            accounts = response["QueryResponse"]["Account"]

            income_account_id = None
            expense_account_id = None

            # First pass: try to find accounts using your preferred criteria.
            for acc in accounts:
                if acc.get("AccountType") == "Income" and acc.get("AccountSubType") in ["SalesOfProductIncome",
                                                                                        "ServiceFeeIncome"]:
                    income_account_id = acc["Id"]
                if acc.get("AccountType") == "Cost of Goods Sold":
                    expense_account_id = acc["Id"]
                if income_account_id and expense_account_id:
                    break

            # Second pass: if not found, fallback to Uncategorized accounts.
            if not income_account_id:
                for acc in accounts:
                    if acc.get("AccountType") == "Income" and acc.get("AccountSubType") == "UncategorizedIncome":
                        income_account_id = acc["Id"]
                        break

            if not expense_account_id:
                for acc in accounts:
                    if acc.get("AccountType") == "Cost of Goods Sold" and acc.get(
                            "AccountSubType") == "UncategorizedExpense":
                        expense_account_id = acc["Id"]
                        break

            if not income_account_id or not expense_account_id:
                raise Exception("Could not find suitable Income/Expense accounts in QuickBooks.")

            return income_account_id, expense_account_id
        except Exception as e:
            logger.error(f"Error fetching default service accounts: {e}")
            raise

    async def create_item(self, item:Item) -> str:

        income_account_id = item.income_account.id
        expense_account_id = item.expense_account.id


        if not item.income_account.id or not item.expense_account.id:
            try:
                default_income, default_expense = await self.get_default_service_accounts()
                if not income_account_id:
                    income_account_id = default_income
                if not expense_account_id:
                    expense_account_id = default_expense
            except Exception as e:
                logger.error(f"Could not fetch default accounts: {e}")
                raise

        # Ensure that the item name is valid after sanitization.
        item_name = remove_non_alphanumeric(item.name[:100])
        if not item.name:
            item_name = "Unnamed Item"

        payload = {
            "TrackQtyOnHand":item.type=="Inventory",

            "QtyOnHand": int(item.QtyOnHand) if item.QtyOnHand is not None else 10,
            "Name":remove_non_alphanumeric(item.name) if item.name else "Unnamed Item",
            "Sku": item.sku if item.sku  is not None else "",
            "UnitPrice":  float(item.unit_price)  if item.unit_price is not None else 0,
            "Description": item.description if item.description  is not None else "",
            "Type": item.type if item.type is not None else "Service",
            # "SalesTaxCodeRef": {
            #     "value": item_data.get("sales_tax_value", 0),
            #     "name": item_data.get("sales_tax_code", ""),
            # },

            "Active": True,
            "IncomeAccountRef": {
                "value": income_account_id,
                "name": item.income_account.name if item.income_account.name is not  None  else "Service Fee Income"
            },
            "ExpenseAccountRef": {
                "value": expense_account_id,
                "name":item.expense_account.name if item.expense_account.name is not  None  else "Cost of Goods Sold"
            }
        }

        if item.type == "Inventory":
            payload["AssetAccountRef"] = {
                "name": "Inventory Asset",
                "value": "81"
            }
            from datetime import date

            # Get today's date
            today = date.today()

            payload["InvStartDate"] = today.strftime("%Y-%m-%d")
            payload["PurchaseCost"]= 602.68  # Added required field for inventory cost
            payload["ExpenseAccountRef"]= {
                "value": "80",
                "name":  "Cost of Goods Sold"
            }
            payload["IncomeAccountRef"]= {
                "name": "Sales of Product Income",
                "value": "79"
            }

        return await self.entity_helper.create_entity("Item",  payload)


# Helper functions to directly call the service methods.

# async def check_item_exists(item_name: str, item_id: Optional[str] = None, client_id: str = "", request: Request = None) -> bool:
#     item_name = remove_non_alphanumeric(item_name)
#     return await sef.check_item_exists(request, item_name, item_id, client_id=client_id)
#
#
# async def create_item(item_data: Dict[str, Any],progress_logger: IntegrationsProgressLog, progress: Dict[str, float], client_id: str = "", request: Request = None) -> str:
#     return await ItemService.create_item(request, item_data,progress_logger,progress, client_id=client_id)
