import asyncio
from typing import Optional
from fastapi import Request
from masyg_extractor.services.my_log import logger, send_log
from masyg_extractor.integrations.quickbooks_client import quickbooks_request
from masyg_extractor.integrations.repository.firestore_repository import (
    store_customer_record,
    customer_exists_in_firestore
)

class CustomerService:
    @staticmethod
    def check_customer_exists(
        request: Request,
        customer_id: Optional[str] = None,
        customer_name: Optional[str] = None,
        client_id: str = ""
    ) -> bool:
        found = False
        if customer_id:
            query = f"SELECT * FROM Customer WHERE Id = '{customer_id}'"
            response = quickbooks_request(request, "query", method="GET", params={"query": query}, client_id=client_id)
            if response.get("QueryResponse", {}).get("Customer"):
                found = True
        if not found and customer_name:
            query = f"SELECT * FROM Customer WHERE DisplayName = '{customer_name}'"
            response = quickbooks_request(request, "query", method="GET", params={"query": query}, client_id=client_id)
            if response.get("QueryResponse", {}).get("Customer"):
                found = True
        return found

    @staticmethod
    def create_customer(
        request: Request,
        customer_name: str,
        client_id: str = ""
    ) -> str:
        """
        Creates a new customer in QuickBooks and returns the new customer's ID.
        """
        sanitized_name = customer_name.lower().replace(' ', '_').replace("'", "")
        email_address = f"{sanitized_name}@example.com"
        payload = {
            "DisplayName": customer_name,
            "PrimaryEmailAddr": {"Address": email_address}
        }
        response = quickbooks_request(request, "customer", payload=payload, method="POST", client_id=client_id)
        logger.info("create_customer response received.")
        if not response or "Fault" in response:
            errors = response.get("Fault", {}).get("Error", [])
            error_msgs = "; ".join([err.get("Message", "Unknown error") for err in errors])
            asyncio.create_task(send_log(f"❌ Failed to create customer: {error_msgs if errors else 'Unknown error'}", user_room=client_id))
            raise Exception(f"Failed to create customer: {error_msgs if errors else 'Unknown error'}")

        customer_data = response.get("Customer")
        if customer_data and "Id" in customer_data:
            new_customer_id = customer_data["Id"]
            asyncio.create_task(
                send_log(f"✅ Customer created with ID: {new_customer_id}", user_room=client_id))
            logger.info(f"Customer created with ID: {new_customer_id}")
            return new_customer_id

        raise Exception(f"Unexpected response structure: {response}")

    @staticmethod
    def fetch_customer_id_by_name(
        request: Request,
        customer_name: str,
        client_id: str = ""
    ) -> Optional[str]:
        """
        Fetches a customer ID from QuickBooks given the customer's name.
        """
        query = f"SELECT Id FROM Customer WHERE DisplayName = '{customer_name}'"
        try:
            response = quickbooks_request(request, "query", method="GET", params={"query": query}, client_id=client_id)
            customers = response.get("QueryResponse", {}).get("Customer", [])
            if customers:
                return customers[0]["Id"]
        except Exception as e:
            logger.error(f"Error fetching customer ID by name: {e}")
        return None

def get_or_create_customer(
    request: Request,
    customer_id: Optional[str],
    customer_name: str,
    user_id: str,
    client_id: str = ""
) -> str:
    """
    Determines a valid customer ID (create or retrieve).
    """
    if not customer_name or customer_name.strip() == "":
        raise ValueError("customer_name is required.")

    # If we have no ID, try to find existing by name
    if not customer_id:
        asyncio.create_task(
            send_log(f"🔦 Checking QuickBooks for customer: {customer_name}", user_room=client_id))
        logger.info(f"Customer ID not provided; checking QuickBooks for {customer_name}")
        if CustomerService.check_customer_exists(request, customer_name=customer_name, client_id=client_id):
            customer_id = CustomerService.fetch_customer_id_by_name(request, customer_name, client_id=client_id)
            if customer_id:
                send_log(f"✅ Found existing customer in QuickBooks: {customer_name} (ID: {customer_id})", user_room=client_id)
                logger.info(f"Found customer in QuickBooks: {customer_name} (ID: {customer_id})")

    # If we do have an ID or we just fetched it, check if it actually exists
    if customer_id and CustomerService.check_customer_exists(request, customer_id, customer_name, client_id=client_id):
        # Check Firestore
        if not customer_exists_in_firestore(user_id, customer_id):
            store_customer_record(user_id, customer_id, {"Id": customer_id, "DisplayName": customer_name}, client_id=client_id)
            logger.info(f"Customer {customer_id} found in QuickBooks but not in Firestore. Saving...")
        else:
            logger.info(f"Customer {customer_id} exists in both Firestore and QuickBooks.")
        return customer_id

    # Otherwise, create a new one
    asyncio.create_task(
        send_log(f"❌ No valid customer found; creating new customer for {customer_name}.", user_room=client_id))
    logger.info(f"Creating new customer for {customer_name}.")
    new_customer_id = CustomerService.create_customer(request, customer_name, client_id=client_id)
    store_customer_record(user_id, new_customer_id, {"Id": new_customer_id, "DisplayName": customer_name}, client_id=client_id)
    return new_customer_id
