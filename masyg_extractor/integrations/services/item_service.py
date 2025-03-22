from typing import Dict, Any, Optional
from fastapi import Request
from masyg_extractor.services.my_log import logger
from masyg_extractor.integrations.quickbooks_client import quickbooks_request

class ItemService:
    @staticmethod
    async def check_item_exists(
        request: Request,
        item_name: str,
        item_id: Optional[str] = None,
        client_id: str = ""
    ) -> bool:
        if not item_name and not item_id:
            logger.warning("check_item_exists called without item_name or item_id.")
            return False

        if item_id and item_name:
            query = f"SELECT * FROM Item WHERE Id = '{item_id}' OR Name = '{item_name}'"
        elif item_id:
            query = f"SELECT * FROM Item WHERE Id = '{item_id}'"
        else:
            query = f"SELECT * FROM Item WHERE Name = '{item_name}'"

        try:
            response = await quickbooks_request(request, "query", method="GET", params={"query": query}, client_id=client_id)
            items = response.get("QueryResponse", {}).get("Item", [])
            found = len(items) > 0
            logger.info(f"Item found? {found} => {item_name} (ID: {item_id})")
            return found
        except Exception as e:
            logger.error(f"Error checking item existence: {e} | Query: {query}")
            return False

    @staticmethod
    async def  get_default_service_accounts(request: Request, client_id: str = "") -> tuple[str, str]:
        try:
            query = "SELECT * FROM Account WHERE Active = true"
            params = {"query": query}
            response = await quickbooks_request(request, "query", method="GET", params=params, client_id=client_id)
            if "QueryResponse" not in response or "Account" not in response["QueryResponse"]:
                raise Exception("No accounts returned from QuickBooks")
            accounts = response["QueryResponse"]["Account"]
            income_account_id = None
            expense_account_id = None
            for acc in accounts:
                if acc.get("AccountType") == "Income" and acc.get("AccountSubType") in ["SalesOfProductIncome", "ServiceFeeIncome"]:
                    income_account_id = acc["Id"]
                if acc.get("AccountType") == "Cost of Goods Sold":
                    expense_account_id = acc["Id"]
                if income_account_id and expense_account_id:
                    break
            if not income_account_id or not expense_account_id:
                raise Exception("Could not find suitable Income/Expense accounts in QuickBooks.")
            return income_account_id, expense_account_id
        except Exception as e:
            logger.error(f"Error fetching default service accounts: {e}")
            raise

    @staticmethod
    async def  create_item(request: Request, item_data: Dict[str, Any], client_id: str = "") -> str:
        income_account_id = item_data.get("income_account_id")
        expense_account_id = item_data.get("expense_account_id")
        if not income_account_id or not expense_account_id:
            try:
                default_income, default_expense = await ItemService.get_default_service_accounts(request, client_id=client_id)
                if not income_account_id:
                    income_account_id = default_income
                if not expense_account_id:
                    expense_account_id = default_expense
            except Exception as e:
                logger.error(f"Could not fetch default accounts: {e}")
                raise
        payload = {
            "Name": item_data.get("item_name", "Unnamed Item")[:100],
            "Description": item_data.get("description", ""),
            "Type": "Service",
            "Active": True,
            "IncomeAccountRef": {
                "value": income_account_id,
                "name": item_data.get("income_account_name", "Service Fee Income")
            },
            "ExpenseAccountRef": {
                "value": expense_account_id,
                "name": item_data.get("expense_account_name", "Cost of Goods Sold")
            }
        }
        logger.info(f"Creating item with payload (sanitized): {{'Name': {payload.get('Name')}}}")
        try:
            response = await quickbooks_request(request, "item", payload=payload, method="POST", client_id=client_id)
            logger.info("create_item response received.")
            if "Item" in response and "Id" in response["Item"]:
                item_id = response["Item"]["Id"]
                logger.info(f"Item created with ID: {item_id}")
                return item_id
            else:
                logger.error(f"Failed to create item. Response: {response}")
                raise Exception("Failed to create item.")
        except Exception as e:
            logger.error(f"Exception in create_item: {e}")
            raise

async def check_item_exists(item_name: str, item_id: Optional[str] = None, client_id: str = "", request: Request = None) -> bool:
    return await ItemService.check_item_exists(request, item_name, item_id, client_id=client_id)

async def create_item(item_data: Dict[str, Any], client_id: str = "", request: Request = None) -> str:
    return await ItemService.create_item(request, item_data, client_id=client_id)
