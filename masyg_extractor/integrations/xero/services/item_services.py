from typing import Dict, Any, Optional
from fastapi import Request
from masyg_extractor.integrations.utils import format_date
from masyg_extractor.services.file_extractor_service import remove_non_alphanumeric
from masyg_extractor.services.my_log import logger
from masyg_extractor.integrations.xero.xero_client import xero_request  # Similar to quickbooks_request

class ItemService:
    @staticmethod
    async def check_item_exists(
        request: Request,
        item_name: str,
        item_id: Optional[str] = None,
        client_id: str = ""
    ) -> bool:
        # Sanitize the item name.
        item_name = item_name
        if not item_name and not item_id:
            logger.warning("check_item_exists called without item_name or item_id.")
            return False

        # If an item_id is provided, try to query by it.
        if item_id:
            # Xero supports filtering via the "where" parameter.
            query_by_id = f"where=ItemID==guid'{item_id}'"
            try:
                response = await xero_request(
                    request,
                    "Items",
                    method="GET",
                    params={"where": query_by_id},
                    client_id=client_id
                )
                items = response.get("Items", [])
                if items:
                    logger.info(f"Item found by id: {item_id}")
                    return True
            except Exception as e:
                logger.error(f"Error checking item existence by id: {e} | Query: {query_by_id}")

        # Otherwise, check by name.
        if item_name:
            query_by_name = f'where=Name=="{item_name}"'
            try:
                response = await xero_request(
                    request,
                    "Items",
                    method="GET",
                    params={"where": query_by_name},
                    client_id=client_id
                )
                items = response.get("Items", [])
                if items:
                    logger.info(f"Item found by name: {item_name}")
                    return True
            except Exception as e:
                logger.error(f"Error checking item existence by name: {e} | Query: {query_by_name}")
                return False

        logger.info(f"Item not found: {item_name} (ID: {item_id})")
        return False

    @staticmethod
    async def create_item(
        request: Request,
        item_data: Dict[str, Any],
        client_id: str = ""
    ) -> str:
        # Ensure the item name is valid after sanitization.
        item_name = remove_non_alphanumeric(item_data.get("item_name", "Unnamed Item")[:100])
        if not item_name:
            item_name = "Unnamed Item"

        # Build the payload for Xero's Items endpoint.
        # Xero expects an object with an "Items" key containing a list of item definitions.
        payload_item = {
            "Code": item_data.get("sku", ""),  # Typically used as a unique identifier.
            "Name": item_name,
            "Description": item_data.get("description", ""),
            "SalesDetails": {
                "UnitPrice": float(item_data.get("unit_price", 0)),
                "AccountCode": item_data.get("sales_account", "200")  # Default sales account code.
            },
            "PurchaseDetails": {
                "UnitPrice": float(item_data.get("purchase_cost", 0)),
                "AccountCode": item_data.get("expense_account", "300")  # Default purchase/expense account code.
            },
            "IsTrackedAsInventory": item_data.get("type", "Service") == "Inventory"
        }

        # If the item is of type Inventory, include additional inventory-specific fields.
        if item_data.get("type") == "Inventory":
            from datetime import date
            today = date.today()
            payload_item.update({
                "InventoryAssetAccountCode": item_data.get("asset_account", "400"),  # Default inventory asset account.
                "QuantityOnHand": int(item_data.get("quantity", 0)),
                "PurchaseCost": float(item_data.get("purchase_cost", 0)),
                # Optionally include a start date for inventory tracking.
                "StartDate": today.strftime("%Y-%m-%d")
            })

        payload = {
            "Items": [payload_item]
        }

        logger.info(f"Creating item in Xero with payload (sanitized): {{'Name': {payload_item.get('Name')}}}")
        try:
            response = await xero_request(
                request,
                "Items",
                payload=payload,
                method="POST",
                client_id=client_id
            )
            logger.info("create_item response received.")
            if "Items" in response and isinstance(response["Items"], list) and len(response["Items"]) > 0:
                created_item = response["Items"][0]
                item_id = created_item.get("ItemID")
                logger.info(f"Item created with ID: {item_id}")
                return item_id
            else:
                logger.error(f"Failed to create item. Response: {response}")
                raise Exception("Failed to create item.")
        except Exception as e:
            logger.error(f"Exception in create_item: {e}")
            raise

# Helper functions to directly call the service methods.

async def check_item_exists(
    item_name: str,
    item_id: Optional[str] = None,
    client_id: str = "",
    request: Request = None
) -> bool:
    item_name = remove_non_alphanumeric(item_name)
    return await ItemService.check_item_exists(request, item_name, item_id, client_id=client_id)

async def create_item(
    item_data: Dict[str, Any],
    client_id: str = "",
    request: Request = None
) -> str:
    return await ItemService.create_item(request, item_data, client_id=client_id)
