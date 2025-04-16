import asyncio
from typing import Optional, Dict
from fastapi import Request
from masyg_extractor.services.my_log import logger
from masyg_extractor.integrations.quickbooks.repository.firestore_repository import (
    store_customer_record,
    customer_exists_in_firestore
)
from masyg_extractor.integrations.quickbooks.helper.qb_helpers import (
    check_entity_exists,
    fetch_entity_id_by_name,
    create_entity
)
from masyg_extractor.services.progress_log import IntegrationsProgressLog


class CustomerService:
    @staticmethod
    async def check_customer_exists(
            request: Request,
            customer_id: Optional[str] = None,
            customer_name: Optional[str] = None,
            client_id: str = ""
    ) -> bool:

        if customer_id:
            return await check_entity_exists(request, "Customer", "Id", customer_id, client_id=client_id)
        if customer_name:
            return await check_entity_exists(request, "Customer", "DisplayName", customer_name, client_id=client_id)
        return False

    @staticmethod
    async def create_customer(
            request: Request,
            customer_name: str
            , progress_logger: IntegrationsProgressLog, progress: Dict[str, float],
            client_id: str = "",

    ) -> str:
        return await create_entity(request, "Customer", customer_name, progress_logger, progress, client_id=client_id)

    @staticmethod
    async def fetch_customer_id_by_name(
            request: Request,
            customer_name: str,
            client_id: str = ""
    ) -> Optional[str]:
        return await fetch_entity_id_by_name(request, "Customer", customer_name, client_id=client_id)


async def get_or_create_customer(
        request: Request,
        customer_id: Optional[str],
        customer_name: str,
        user_id: str
        , progress_logger: IntegrationsProgressLog, progress: Dict[str, float],
        client_id: str = ""
) -> str:
    if not customer_name or customer_name.strip() == "":
        raise ValueError("customer_name is required.")

    # If no customer ID is provided, try to find an existing customer by name.
    if not customer_id:

        logger.info(f"Customer ID not provided; checking QuickBooks for {customer_name}")
        if await CustomerService.check_customer_exists(request, customer_name=customer_name, client_id=client_id):

            customer_id = await CustomerService.fetch_customer_id_by_name(request, customer_name, client_id=client_id)
            if customer_id:
                logger.info(f"Found customer in QuickBooks: {customer_name} (ID: {customer_id})")

    # If a customer ID exists, ensure it's stored in Firestore.
    if customer_id:
        # Offload the Firestore check to a worker thread.
        exists_in_firestore = await asyncio.to_thread(customer_exists_in_firestore, user_id, customer_id)
        if not exists_in_firestore:
            await asyncio.to_thread(
                store_customer_record,
                user_id,
                customer_id,
                {"Id": customer_id, "DisplayName": customer_name},
                client_id=client_id
            )
            logger.info(f"Customer {customer_id} found in QuickBooks but not in Firestore. Saving...")
        else:
            logger.info(f"Customer {customer_id} exists in both Firestore and QuickBooks.")
        return customer_id

    # Otherwise, create a new customer.
    logger.info(f"Creating new customer for {customer_name}.")
    new_customer_id = await CustomerService.create_customer(request, customer_name, progress_logger, progress,
                                                            client_id=client_id)
    await asyncio.to_thread(
        store_customer_record,
        user_id,
        new_customer_id,
        {"Id": new_customer_id, "DisplayName": customer_name},
        client_id=client_id
    )

    return new_customer_id
