import asyncio
from typing import Optional

from masyg_extractor.integration_v2.core.integration_context import IntegrationContext
from masyg_extractor.integration_v2.domain.models import Customer
from masyg_extractor.integration_v2.entity_helper import EntityHelper
from masyg_extractor.integration_v2.intergrate.baseAdapter import IntegrationClientAdapter
from masyg_extractor.integration_v2.repository.firestore_repository import QuickBooksFirestoreService
from masyg_extractor.services.my_log import logger

class CustomerService:
    def __init__(self, context: IntegrationContext, repo: QuickBooksFirestoreService, client: IntegrationClientAdapter):
        self.context = context
        self.repo = repo
        self.client = client
        self.entity_helper = EntityHelper(context, repo, client)

    async def check_customer_exists(self, customer: Customer) -> bool:
        # If the customer already has an ID, check by ID; otherwise, check by DisplayName.
        if customer.id:
            logger.info("Customer ID provided; checking by ID")
            return await self.entity_helper.check_entity_exists("Customer", "Id", customer.id)
        if customer.name:
            # Use DisplayName for customers.
            return await self.entity_helper.check_entity_exists("Customer", "DisplayName", customer.name)
        return False

    async def fetch_customer_id_by_name(self, customer_name: str) -> Optional[str]:
        return await self.entity_helper.fetch_entity_id_by_name("Customer", customer_name)

    async def create_customer(self, customer: Customer) -> str:
        # Sanitize customer name to generate an email address.
        sanitized_name = customer.name.lower().replace(' ', '_').replace("'", "")
        email_address = f"{sanitized_name}@example.com"
        payload = {
            "DisplayName": customer.name,
            "PrimaryEmailAddr": {"Address": email_address}
        }
        logger.info(f"Payload for creating customer: {payload}")
        return await self.entity_helper.create_entity("Customer", payload)

    async def get_or_create_customer(self, customer: Customer) -> str:
        if not customer.name or customer.name.strip() == "":
            raise ValueError("Customer name is required.")

        # If no customer ID is provided, try to find an existing customer by name.
        if not customer.id:
            logger.info(f"Customer ID not provided; checking QuickBooks for customer: {customer.name}")
            if await self.check_customer_exists(customer):
                found_customer_id = await self.fetch_customer_id_by_name(customer.name)
                if found_customer_id:
                    logger.info(f"Found customer in QuickBooks: {customer.name} with ID: {found_customer_id}")
                    customer.id = found_customer_id  # Update customer with the fetched ID.

        # If customer.id now exists (either it was originally provided or we just found one),
        # ensure it is stored in Firestore.
        if customer.id:
            # Offload Firestore existence check to a worker thread.
            exists_in_firestore = await asyncio.to_thread(self.repo.customer_exists, customer.id)
            if not exists_in_firestore:
                # Store the customer record in Firestore if it's not already stored.
                await asyncio.to_thread(
                    self.repo.store_customer,
                    customer.id,
                    {"Id": customer.id, "DisplayName": customer.name},
                )
                logger.info(f"Customer {customer.id} found in QuickBooks but not in Firestore. Saving...")
            else:
                logger.info(f"Customer {customer.id} exists in both Firestore and QuickBooks.")
            return customer.id

        # If no customer exists in QuickBooks, create a new customer.
        logger.info(f"Creating new customer for {customer.name}.")
        new_customer_id = await self.create_customer(customer)
        # Save the new customer record in Firestore.
        await asyncio.to_thread(
            self.repo.store_customer,
            new_customer_id,
            {"Id": new_customer_id, "DisplayName": customer.name},
        )
        return new_customer_id
