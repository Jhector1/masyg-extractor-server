import asyncio
from typing import Optional
from fastapi import Request
from masyg_extractor.services.my_log import logger
from masyg_extractor.integrations.xero.repository.firestore_repository import (
    store_customer_record,
    customer_exists_in_firestore
)
from masyg_extractor.integrations.xero.helper.xero_helpers import (
    check_entity_exists,
   fetch_entity_id_by_name,
    create_entity,
)

class CustomerService:
    @staticmethod
    async def check_customer_exists(
            request: Request,
            user_id: str,
            customer_id: Optional[str] = None,
            customer_name: Optional[str] = None,
            client_id: str = ""
    ) -> bool:
        # If a customer ID is provided, check using it.
        if customer_id:
            return await check_entity_exists(request,"Contact", "ContactID", customer_id, user_id,client_id=client_id)
        # Otherwise, check by the contact name.
        if customer_name:
            return await check_entity_exists(request, "Contact", "Name", customer_name,user_id, client_id=client_id)
        return False

    @staticmethod
    async def create_customer(
            request: Request,
            customer_name: str,
            user_id: str,
            client_id: str = ""
    ) -> str:
        # Create a new contact in Xero for the customer.
        return await create_entity(request, "Contact", customer_name,user_id, client_id=client_id)

    @staticmethod
    async def fetch_customer_id_by_name(
            request: Request,
            customer_name: str,
            user_id: str,
            client_id: str = ""
    ) -> Optional[str]:
        # Retrieve the ContactID for the given customer name.
        return await fetch_entity_id_by_name(request, "Contact", customer_name,user_id, client_id=client_id)

async def get_or_create_customer(
        request: Request,
        customer_id: Optional[str],
        customer_name: str,
        user_id: str,
        client_id: str = ""
) -> str:
    if not customer_name or customer_name.strip() == "":
        raise ValueError("customer_name is required.")

    # If no customer ID is provided, try to locate an existing contact by name in Xero.
    if not customer_id:
        logger.info(f"Customer ID not provided; checking Xero for {customer_name}")
        if await CustomerService.check_customer_exists(request,user_id, customer_name=customer_name, client_id=client_id):
            customer_id = await CustomerService.fetch_customer_id_by_name(request, customer_name, user_id=user_id,client_id=client_id)
            if customer_id:
                logger.info(f"Found customer in Xero: {customer_name} (ID: {customer_id})")

    # If a customer ID exists, ensure it's stored in Firestore.
    if customer_id:
        exists_in_firestore = await asyncio.to_thread(customer_exists_in_firestore, user_id, customer_id)
        if not exists_in_firestore:
            await asyncio.to_thread(
                store_customer_record,
                user_id,
                customer_id,
                {"ContactID": customer_id, "Name": customer_name},
                client_id=client_id
            )
            logger.info(f"Customer {customer_id} found in Xero but not in Firestore. Saving...")
        else:
            logger.info(f"Customer {customer_id} exists in both Firestore and Xero.")
        return customer_id

    # Otherwise, create a new contact in Xero.
    logger.info(f"Creating new customer for {customer_name}.")
    new_customer_id = await CustomerService.create_customer(request, customer_name, user_id=user_id,client_id=client_id)
    await asyncio.to_thread(
        store_customer_record,
        user_id,
        new_customer_id,
        {"ContactID": new_customer_id, "Name": customer_name},
        client_id=client_id
    )
    return new_customer_id
