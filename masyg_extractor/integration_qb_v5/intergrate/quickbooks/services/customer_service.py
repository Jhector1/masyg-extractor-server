import asyncio
from typing import Dict, List, cast

from masyg_extractor.integration_qb_v5.core.integration_context import IntegrationContext
from masyg_extractor.integration_qb_v5.domain.models import Customer
from masyg_extractor.integration_qb_v5.entity_helper import EntityHelper
from masyg_extractor.integration_qb_v5.intergrate.baseAdapter import IntegrationClientAdapter
from masyg_extractor.integration_qb_v5.repository.firestore_repository import QuickBooksFirestoreService
from masyg_extractor.integration_qb_v5.utils import extract_uuid
from masyg_extractor.integrations.xero.services.item_services import generate_sku
from masyg_extractor.services.my_log import logger


class CustomerService:
    def __init__(
            self,
            context: IntegrationContext,
            repo: QuickBooksFirestoreService,
            client: IntegrationClientAdapter,
    ):
        self.context = context
        self.repo = repo
        self.client = client
        self.entity_helper = EntityHelper(context, repo, client)

    @staticmethod
    def get_sanitized_name(name: str) -> str:
        """
        Sanitizes the customer name by lowercasing, stripping spaces,
        replacing spaces with underscores, and removing apostrophes.
        """
        try:
            return name.strip().lower().replace(" ", "_").replace("'", "")
        except Exception as e:
            logger.error(f"Error sanitizing name '{name}': {str(e)}")
            return name

    @staticmethod
    def create_single_customer_payload(customer: Customer) -> dict:
        """
        Constructs the payload for creating a single customer.
        """
        try:
            sanitized_name = CustomerService.get_sanitized_name(customer.name)
            # Uncomment and modify the email logic if email info is needed.
            # email_address = f"{sanitized_name}@example.com"
            return {
                "Contact": customer.name,
                # "PrimaryEmailAddr": {"Address": email_address},
            }
        except Exception as e:
            logger.error(f"Error creating payload for customer '{customer.name}': {str(e)}")
            return {}

    @staticmethod
    def create_bulk_customer_payload(customer: Customer) -> dict:
        """
        Constructs the payload for a customer in bulk creation.
        Adds a generated ContactNumber based on a truncated transaction UUID.
        """
        sanitized_name = customer.name.lower().replace(' ', '_').replace("'", "")
        email_address = f"{sanitized_name}@example.com"
        try:
            payload = {
                "DisplayName": customer.name,
                "PrimaryEmailAddr": {"Address": email_address}
            }
            return {
                "bId":  extract_uuid(customer.transaction_id)[:20] + "_"+generate_sku(customer.name),
                "Customer": payload,
                "operation": "create"
            }


        except Exception as e:
            logger.error(f"Error creating bulk payload for customer '{customer.name}': {str(e)}")
        return {}


async def check_customer_exists(self, customer: Customer) -> bool:
    """
    Checks for the existence of a customer using either the ID or the name.
    """
    try:
        if customer.id:
            logger.info("Customer ID provided; checking by ID")
            return await self.entity_helper.check_entity_exists("Contact", "ContactID", customer.id)
        if customer.name:
            return await self.entity_helper.check_entity_exists("Contact", "Name", customer.name)
        return False
    except Exception as e:
        logger.error(f"Error checking existence for customer '{customer.name}': {str(e)}")
        return False


async def fetch_customer_id_by_name(self, customer_name: str) -> str:
    """
    Fetches the customer ID based on their name.
    """
    try:
        return await self.entity_helper.fetch_entity_id_by_name("Contact", customer_name)
    except Exception as e:
        logger.error(f"Error fetching customer ID for name '{customer_name}': {str(e)}")
        return ""


async def create_customer(self, customer: Customer) -> str:
    """
    Creates a customer and returns the generated customer ID.
    """
    try:
        payload = self.create_single_customer_payload(customer)
        logger.info(f"Payload for creating customer: {payload}")
        return await self.entity_helper.create_entity("Contact", payload)
    except Exception as e:
        logger.error(f"Error creating customer '{customer.name}': {str(e)}")
        return ""


async def get_or_create_customer(self, customer: Customer) -> str:
    """
    Retrieves an existing customer or creates a new one if not found.
    Additionally, ensures the customer record is stored in Firestore.
    """
    try:
        if not customer.name or customer.name.strip() == "":
            raise ValueError("Customer name is required.")

        # If no customer ID, check in the target system.
        if not customer.id:
            logger.info(f"Customer ID not provided; checking system for customer: {customer.name}")
            if await self.check_customer_exists(customer):
                found_customer_id = await self.fetch_customer_id_by_name(customer.name)
                if found_customer_id:
                    logger.info(f"Found customer '{customer.name}' with ID: {found_customer_id}")
                    customer.id = found_customer_id

        # Store the customer in Firestore if necessary.
        if customer.id:
            exists_in_firestore = await asyncio.to_thread(self.repo.customer_exists, customer.id)
            if not exists_in_firestore:
                await asyncio.to_thread(
                    self.repo.store_customer,
                    customer.id,
                    {"ContactID": customer.id, "Name": customer.name},
                )
                logger.info(f"Customer {customer.id} found in system but not in Firestore. Saving...")
            else:
                logger.info(f"Customer {customer.id} exists in both Firestore and system.")
            return customer.id

        # Create a new customer if not found.
        logger.info(f"Creating new customer for {customer.name}.")
        new_customer_id = await self.create_customer(customer)
        await asyncio.to_thread(
            self.repo.store_customer,
            new_customer_id,
            {"ContactID": new_customer_id, "Name": customer.name},
        )
        return new_customer_id
    except Exception as e:
        logger.error(f"Error in get_or_create_customer for '{customer.name}': {str(e)}")
        return ""


async def create_customer_in_bulk(self, local_customers: Dict[str, Customer]) -> Dict[str, Customer]:
    """
    Creates customers in bulk.
    Filters out existing customers and merges newly created customers with the already stored ones.
    """
    try:
        name_key: str = "DisplayName"
        id_key: str = "Id"
        entity = "Customer"

        # Convert the dictionary values to a list of customers.
        customer_list = list(local_customers.values())

        # Filter out customers that already exist.
        non_existing_customers = cast(
            List[Customer],
            await self.entity_helper.get_non_existing_entities(customer_list, entity, name_key, id_key)
        )
        print("non-existing customer", non_existing_customers)

        # Build the payload for bulk creation.
        payload_customers = [self.create_bulk_customer_payload(customer) for customer in non_existing_customers]
        payload = {"BatchItemRequest": payload_customers}

        all_current_customers = cast(
            Dict[str, Customer],
            await self.entity_helper.create_entity_in_bulk_and_merge_with_current(
                local_customers, entity, payload, name_key, id_key, tracker_key="bId"
            )
        )
        logger.info(f"Bulk customer creation merged payload: {all_current_customers}")
        return all_current_customers
    except Exception as e:
        logger.error(f"Error in create_customer_in_bulk: {str(e)}")
        return {}


async def get_or_create_customers_bulk(self, customers: List[Customer]) -> Dict[str, str]:
    """
    Deduplicates customers (based on lowercased, trimmed names) and concurrently retrieves or creates each.
    Returns a mapping of the unique key to the customer ID.
    """
    try:
        unique_customers: Dict[str, Customer] = {}
        for customer in customers:
            key = customer.name.strip().lower()
            if key not in unique_customers:
                unique_customers[key] = customer

        tasks = {key: asyncio.create_task(self.get_or_create_customer(cust)) for key, cust in unique_customers.items()}
        results: Dict[str, str] = {}
        for key, task in tasks.items():
            try:
                customer_id = await task
                results[key] = customer_id
                logger.info(f"Processed customer '{key}' with ID: {customer_id}")
            except Exception as e:
                logger.error(f"Error processing customer with key '{key}': {str(e)}")
        return results
    except Exception as e:
        logger.error(f"Error in get_or_create_customers_bulk: {str(e)}")
        return {}
