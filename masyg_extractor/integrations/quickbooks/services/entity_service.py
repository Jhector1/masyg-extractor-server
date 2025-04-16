# import asyncio
# from typing import Dict, Any, Optional
# from fastapi import Request
#
# from masyg_extractor.services.file_extractor_service import remove_non_alphanumeric
# from masyg_extractor.services.my_log import logger
# from masyg_extractor.integrations.quickbooks.quickbooks_client import quickbooks_request
# from masyg_extractor.services.progress_log import IntegrationsProgressLog
#
#
# class EntityService:
#     def __init__(self, client_id):
#         self.client_id = client_id
#
#     async def check_entity_exists(self,
#                                   request: Request,
#                                   entity: str,
#                                   identifier_field: str,
#                                   identifier_value: str,
#                                   user_id: str,
#
#                                   ) -> bool:
#         """
#         Asynchronously checks if an entity (Customer, Vendor, etc.) exists in QuickBooks using a given identifier field.
#         """
#         query = f"SELECT * FROM {entity} WHERE {identifier_field} = '{identifier_value}'"
#
#         response = await quickbooks_request(
#             request,
#             "query",
#             method="GET",
#             params={"query": query},
#             client_id=self.client_id, user_id=user_id)
#
#         exists = bool(response.get("QueryResponse", {}).get(entity))
#
#         logger.info(f"{entity} exists check for {identifier_field}='{identifier_value}': {exists}")
#         return exists
#
#     @staticmethod
#     async def get_default_service_accounts(request: Request, client_id: str = "") -> tuple[str, str]:
#         try:
#             query = "SELECT * FROM Account WHERE Active = true"
#             params = {"query": query}
#             response = await quickbooks_request(request, "query", method="GET", params=params, client_id=client_id)
#             if "QueryResponse" not in response or "Account" not in response["QueryResponse"]:
#                 raise Exception("No accounts returned from QuickBooks")
#             accounts = response["QueryResponse"]["Account"]
#
#             income_account_id = None
#             expense_account_id = None
#
#             # First pass: try to find accounts using your preferred criteria.
#             for acc in accounts:
#                 if acc.get("AccountType") == "Income" and acc.get("AccountSubType") in ["SalesOfProductIncome",
#                                                                                         "ServiceFeeIncome"]:
#                     income_account_id = acc["Id"]
#                 if acc.get("AccountType") == "Cost of Goods Sold":
#                     expense_account_id = acc["Id"]
#                 if income_account_id and expense_account_id:
#                     break
#
#             # Second pass: if not found, fallback to Uncategorized accounts.
#             if not income_account_id:
#                 for acc in accounts:
#                     if acc.get("AccountType") == "Income" and acc.get("AccountSubType") == "UncategorizedIncome":
#                         income_account_id = acc["Id"]
#                         break
#
#             if not expense_account_id:
#                 for acc in accounts:
#                     if acc.get("AccountType") == "Cost of Goods Sold" and acc.get(
#                             "AccountSubType") == "UncategorizedExpense":
#                         expense_account_id = acc["Id"]
#                         break
#
#             if not income_account_id or not expense_account_id:
#                 raise Exception("Could not find suitable Income/Expense accounts in QuickBooks.")
#
#             return income_account_id, expense_account_id
#         except Exception as e:
#             logger.error(f"Error fetching default service accounts: {e}")
#             raise
#
#     @staticmethod
#     async def create_item(request: Request, item_data: Dict[str, Any], progress_logger: IntegrationsProgressLog,
#                           progress: Dict[str, float], client_id: str = "") -> str:
#         income_account_id = item_data.get("income_account_id")
#         expense_account_id = item_data.get("expense_account_id")
#         steps = 5
#
#         for step in range(steps):
#             await asyncio.sleep(0.3)
#             progress["creating_item"] = ((step + 1) / steps) * IntegrationsProgressLog.CREATING_ITEM_WEIGHT
#             await progress_logger.safe_emit_progress(progress_logger.calculate_overall_progress(progress))
#
#         if not income_account_id or not expense_account_id:
#             try:
#                 default_income, default_expense = await ItemService.get_default_service_accounts(request,
#                                                                                                  client_id=client_id)
#                 if not income_account_id:
#                     income_account_id = default_income
#                 if not expense_account_id:
#                     expense_account_id = default_expense
#             except Exception as e:
#                 logger.error(f"Could not fetch default accounts: {e}")
#                 raise
#
#         # Ensure that the item name is valid after sanitization.
#         item_name = remove_non_alphanumeric(item_data.get("item_name", "Unnamed Item")[:100])
#         if not item_name:
#             item_name = "Unnamed Item"
#
#         payload = {
#             "TrackQtyOnHand": item_data.get("type") == "Inventory",
#
#             "QtyOnHand": int(item_data.get("QtyOnHand", 10)),
#             "Name": item_name,
#             "Sku": item_data.get("sku", ""),
#             "UnitPrice": float(item_data.get("unit_price", 0)),
#             "Description": item_data.get("description", ""),
#             "Type": item_data.get("type", "Service"),
#             # "SalesTaxCodeRef": {
#             #     "value": item_data.get("sales_tax_value", 0),
#             #     "name": item_data.get("sales_tax_code", ""),
#             # },
#
#             "Active": True,
#             "IncomeAccountRef": {
#                 "value": income_account_id,
#                 "name": item_data.get("income_account", "Service Fee Income")
#             },
#             "ExpenseAccountRef": {
#                 "value": expense_account_id,
#                 "name": item_data.get("expense_account", "Cost of Goods Sold")
#             }
#         }
#
#         if item_data.get("type") == "Inventory":
#             payload["AssetAccountRef"] = {
#                 "name": "Inventory Asset",
#                 "value": "81"
#             }
#             from datetime import date
#
#             # Get today's date
#             today = date.today()
#
#             payload["InvStartDate"] = today.strftime("%Y-%m-%d")
#             payload["PurchaseCost"] = 602.68  # Added required field for inventory cost
#             payload["ExpenseAccountRef"] = {
#                 "value": "80",
#                 "name": "Cost of Goods Sold"
#             }
#             payload["IncomeAccountRef"] = {
#                 "name": "Sales of Product Income",
#                 "value": "79"
#             }
#
#         progress["creating_item"] = IntegrationsProgressLog.CREATING_ITEM_WEIGHT
#         await progress_logger.safe_emit_progress(progress_logger.calculate_overall_progress(progress))
#         try:
#             response = await quickbooks_request(request, "item", payload=payload, method="POST", client_id=client_id)
#             logger.info("create_item response received.")
#             if "Item" in response and "Id" in response["Item"]:
#                 item_id = response["Item"]["Id"]
#
#                 return item_id
#             else:
#                 logger.error(f"Failed to create item. Response: {response}")
#                 raise Exception("Failed to create item.")
#         except Exception as e:
#             logger.error(f"Exception in create_item: {e}")
#             raise
#
#
# # Helper functions to directly call the service methods.
#
# async def check_item_exists(item_name: str, item_id: Optional[str] = None, client_id: str = "",
#                             request: Request = None) -> bool:
#     item_name = remove_non_alphanumeric(item_name)
#     return await ItemService.check_item_exists(request, item_name, item_id, client_id=client_id)
#
#
# async def create_item(item_data: Dict[str, Any], progress_logger: IntegrationsProgressLog, progress: Dict[str, float],
#                       client_id: str = "", request: Request = None) -> str:
#     return await ItemService.create_item(request, item_data, progress_logger, progress, client_id=client_id)
