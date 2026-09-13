from typing import Dict, Any, Optional
from fastapi import Request
from masyg_extractor.integrations.utils import format_date  # if needed elsewhere
from masyg_extractor.services.file_extractor_service import remove_non_alphanumeric
from masyg_extractor.services.my_log import logger
from masyg_extractor.integrations.xero.xero_client import xero_request
from masyg_extractor.integrations.accounting.shared.sku import generate_sku


class ItemService:
    @staticmethod
    async def check_item_exists(
            request: Request,
            item_name: str,
            user_id: str,
            item_id: Optional[str] = None,
            client_id: str = ""
    ) -> bool:
        # Sanitize the item name.
        item_name = remove_non_alphanumeric(item_name) if item_name else ""
        if not item_name and not item_id:
            logger.warning("check_item_exists called without item_name or item_id.")
            return False

        # First check by item_id if provided.
        if item_id:
            query_by_id = f"ItemID==guid'{item_id}'"
            try:
                response = await xero_request(

                    "Items",
                    user_id=user_id,
                    method="GET",
                    params={"where": query_by_id},
                )
                items = response.get("Items", [])
                if items:
                    logger.info(f"Item found by id: {item_id}")
                    return True
            except Exception as e:
                logger.error(f"Error checking item existence by id: {e} | Query: {query_by_id}")

        # Otherwise, check by item name.
        if item_name:
            query_by_name = f'Name=="{item_name}"'
            try:
                response = await xero_request(

                    "Items",
                    user_id=user_id,
                    method="GET",
                    params={"where": query_by_name},
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
            user_id: str,
            client_id: str = ""
    ) -> str:
        # Sanitize and limit the item name.
        raw_item_name = item_data.get("item_name", "Unnamed Item")[:50]
        item_name = remove_non_alphanumeric(raw_item_name) or "Unnamed Item"

        # Build the payload for Xero’s Items endpoint.
        payload_item = {
            # "Items": [{
                "Code": item_data.get("sku", generate_sku(item_name)),  # Often used as a unique identifier.
                "Name": item_name,
                "Description": item_data.get("description", "")
                # "SalesDetails": {
                #     "UnitPrice": float(item_data.get("unit_price", 0)),
                #     "AccountCode": item_data.get("sales_account", "200")  # Default sales account code.
                # },
                # "PurchaseDetails": {
                #     "UnitPrice": float(item_data.get("purchase_cost", 0)),
                #     "AccountCode": item_data.get("expense_account", "300")  # Default purchase/expense account code.
                # },
                # # Xero expects a boolean here.
                # "IsTrackedAsInventory": item_data.get("type", "Service") == "Inventory"
            # }

            # ]
        }

        # If the item is Inventory tracked, add extra inventory-specific fields.
        if item_data.get("type") == "Inventory":
            from datetime import date
            today = date.today()
            payload_item.update({
                "InventoryAssetAccountCode": item_data.get("asset_account", "400"),  # Default asset account code.
                "QuantityOnHand": int(item_data.get("quantity", 0)),
                "PurchaseCost": float(item_data.get("purchase_cost", 0)),
                "StartDate": today.strftime("%Y-%m-%d")  # Format: YYYY-MM-DD
            })

        payload = {"Items": [payload_item]}

        logger.info(f"Creating item in Xero with payload: {{'Name': {item_name}}}")
        try:
            response = await xero_request(

                "Items",
                user_id=user_id,
                payload=payload,
                method="POST",
            )
            logger.info("create_item response received.")
            # Log the full response for diagnostic purposes.
            logger.info(f"Response: {response}")

            if "Items" in response and isinstance(response["Items"], list) and response["Items"]:
                created_item = response["Items"][0]
                item_id = created_item.get("ItemID")
                logger.info(f"Item created with ID: {item_id}")
                return item_id
            else:
                logger.error(f"Failed to create item. Response payload: {response}")
                raise Exception("Failed to create item.")
        except Exception as e:
            # If available, include the error response content.
            logger.error(f"Exception in create_item: {e}")
            raise


# Top-level helper functions.

async def check_item_exists(
        item_name: str,
        user_id: str,
        item_id: Optional[str] = None,
        client_id: str = "",
        request: Request = None
) -> bool:
    sanitized_name = remove_non_alphanumeric(item_name)
    return await ItemService.check_item_exists(request, sanitized_name, user_id, item_id, client_id=client_id)


async def create_item(
        item_data: Dict[str, Any],
        user_id: str,
        client_id: str = "",
        request: Request = None
) -> str:
    return await ItemService.create_item(request, item_data, user_id, client_id=client_id)


import random
import string
import re


# Example usage:
if __name__ == "__main__":
    test_products = [
        "Hot Box LB1",
        "Ultra Clean X99",
        "Premium Juice",
        "Mini PC",
        "AA"
    ]

    for product in test_products:
        sku = generate_sku(product)
