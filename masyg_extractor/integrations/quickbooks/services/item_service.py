from typing import Dict, Any, Optional
from fastapi import Request

from masyg_extractor.services.file_extractor_service import remove_non_alphanumeric
from masyg_extractor.services.my_log import logger
from masyg_extractor.integrations.quickbooks.quickbooks_client import quickbooks_request


class ItemService:
    @staticmethod
    async def check_item_exists(
        request: Request,
        item_name: str,
        item_id: Optional[str] = None,
        client_id: str = ""
    ) -> bool:
        # Sanitize the item name
        item_name = remove_non_alphanumeric(item_name)
        if not item_name and not item_id:
            logger.warning("check_item_exists called without item_name or item_id.")
            return False

        # Validate item_id if provided, then convert to a string for the query.
        item_id_str = None
        if item_id is not None and str(item_id).strip():
            try:
                # Validate by converting to int, then convert back to string
                validated_id = int(item_id)
                item_id_str = str(validated_id)
            except (ValueError, TypeError):
                logger.error(f"Invalid item_id provided: {item_id}. It must be an integer.")
                return False

        # If item_id is provided, first check using only the id.
        if item_id_str:
            # Enclose the id in quotes since QBO expects IDs as strings.
            query_by_id = f"SELECT * FROM Item WHERE Id = '{item_id_str}'"
            try:
                response = await quickbooks_request(
                    request,
                    "query",
                    method="GET",
                    params={"query": query_by_id},
                    client_id=client_id
                )
                items = response.get("QueryResponse", {}).get("Item", [])
                if items:

                    return True
            except Exception as e:
                logger.error(f"Error checking item existence by id: {e} | Query: {query_by_id}")

        # If no item was found by id, or if only item_name is provided, check by name.
        if item_name:
            query_by_name = f"SELECT * FROM Item WHERE Name = '{item_name}'"
            try:
                response = await quickbooks_request(
                    request,
                    "query",
                    method="GET",
                    params={"query": query_by_name},
                    client_id=client_id
                )
                items = response.get("QueryResponse", {}).get("Item", [])
                if items:

                    return True
            except Exception as e:
                logger.error(f"Error checking item existence by name: {e} | Query: {query_by_name}")
                return False


        return False

    @staticmethod
    async def get_default_service_accounts(request: Request, client_id: str = "") -> tuple[str, str]:
        try:
            query = "SELECT * FROM Account WHERE Active = true"
            params = {"query": query}
            response = await quickbooks_request(request, "query", method="GET", params=params, client_id=client_id)
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

    @staticmethod
    async def create_item(request: Request, item_data: Dict[str, Any], client_id: str = "") -> str:
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

        # Ensure that the item name is valid after sanitization.
        item_name = remove_non_alphanumeric(item_data.get("item_name", "Unnamed Item")[:100])
        if not item_name:
            item_name = "Unnamed Item"

        payload = {
            "TrackQtyOnHand":item_data.get("type")=="Inventory",

            "QtyOnHand": int(item_data.get("QtyOnHand", 10)),
            "Name": item_name,
            "Sku": item_data.get("sku", ""),
            "UnitPrice":  float(item_data.get("unit_price", 0)),
            "Description": item_data.get("description", ""),
            "Type": item_data.get("type","Service"),
            # "SalesTaxCodeRef": {
            #     "value": item_data.get("sales_tax_value", 0),
            #     "name": item_data.get("sales_tax_code", ""),
            # },

            "Active": True,
            "IncomeAccountRef": {
                "value": income_account_id,
                "name": item_data.get("income_account", "Service Fee Income")
            },
            "ExpenseAccountRef": {
                "value": expense_account_id,
                "name": item_data.get("expense_account", "Cost of Goods Sold")
            }
        }

        if item_data.get("type") == "Inventory":
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


        try:
            response = await quickbooks_request(request, "item", payload=payload, method="POST", client_id=client_id)
            logger.info("create_item response received.")
            if "Item" in response and "Id" in response["Item"]:
                item_id = response["Item"]["Id"]

                return item_id
            else:
                logger.error(f"Failed to create item. Response: {response}")
                raise Exception("Failed to create item.")
        except Exception as e:
            logger.error(f"Exception in create_item: {e}")
            raise


# Helper functions to directly call the service methods.

async def check_item_exists(item_name: str, item_id: Optional[str] = None, client_id: str = "", request: Request = None) -> bool:
    item_name = remove_non_alphanumeric(item_name)
    return await ItemService.check_item_exists(request, item_name, item_id, client_id=client_id)


async def create_item(item_data: Dict[str, Any], client_id: str = "", request: Request = None) -> str:
    return await ItemService.create_item(request, item_data, client_id=client_id)
