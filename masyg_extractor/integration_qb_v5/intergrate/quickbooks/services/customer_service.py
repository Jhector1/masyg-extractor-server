# -------------------------------
# CustomerService with audit
# -------------------------------
import asyncio
from itertools import chain
from typing import Dict, List, Optional, cast

from fastapi import Request

from masyg_extractor.integration_qb_v5.core.integration_context import IntegrationContext
from masyg_extractor.integration_qb_v5.domain.models import Item, Customer
from masyg_extractor.integration_qb_v5.entity_helper import EntityHelper
from masyg_extractor.integration_qb_v5.intergrate.baseAdapter import IntegrationClientAdapter
from masyg_extractor.integration_qb_v5.intergrate.quickbooks.services.audit_log_service import AuditLogService, audit_op
from masyg_extractor.integration_qb_v5.repository.firestore_repository import QuickBooksFirestoreService
from masyg_extractor.integration_qb_v5.utils import safe_uuid_key
from masyg_extractor.integrations.xero.services.item_services import generate_sku
from masyg_extractor.services.file_extractor_service import remove_non_alphanumeric
from masyg_extractor.services.my_log import logger
#get_customers
from pprint import pprint

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
        self.audit = AuditLogService(context.user_id, integration="quickbooks")

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
            # Keep it minimal (no duplication into audit)
            return {"Customer": customer.name}
        except Exception as e:
            logger.error(f"Error creating payload for customer '{customer.name}': {str(e)}")
            return {}

    @staticmethod
    def create_bulk_customer_payload(customer: Customer) -> dict:
        """
        Constructs the payload for a customer in bulk creation.
        """
        sanitized_name = customer.name.lower().replace(" ", "_").replace("'", "")
        email_address = f"{sanitized_name}@example.com"
        try:
            payload = {
                "DisplayName": customer.name,
                "PrimaryEmailAddr": {"Address": email_address},
            }
            return {
                "bId": safe_uuid_key(customer.transaction_id) + "_" + generate_sku(customer.name),
                "Customer": payload,
                "operation": "create",
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

    @audit_op(doc_type="-", entity_type="Customer", operation="create")
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
        - Filters out existing customers.
        - Builds per-customer bId entries.
        - Starts audit PENDING per bId.
        - Sends batch and marks ok/fail per bId when batch info is available;
          otherwise marks all intended as ok (fallback).
        """
        try:
            name_key: str = "DisplayName"
            id_key: str = "Id"
            entity = "Customer"

            # Flatten dict to list
            customer_list = list(local_customers.values())

            # Filter out customers that already exist.
            non_existing_customers = cast(
                List[Customer],
                await self.entity_helper.get_non_existing_entities(customer_list, entity, name_key, id_key),
            )

            # Build the payload for bulk creation.
            payload_customers = [self.create_bulk_customer_payload(customer) for customer in non_existing_customers]
            payload_customers = [p for p in payload_customers if p]  # drop any {}
            payload = {"BatchItemRequest": payload_customers}


            # Start per-bId PENDING audit
            intended_bids = [p.get("bId") for p in payload_customers if p.get("bId")]
            for bid in intended_bids:
                self.audit.start(
                    event_id=f"-:Customer:{bid}:{bid}",
                    doc_type="-",
                    entity_type="Customer",
                    operation="bulk_create",
                    transaction_id=None,
                    group_id=None,
                    idempotency_key=bid,
                    payload=None,
                )

            result = await self.entity_helper.create_entity_in_bulk_and_merge_with_current(
                local_customers, entity, payload, name_key, id_key, tracker_key="bId"
            )

            # Support both return shapes:
            batch_resp = None
            all_current_customers: Dict[str, Customer]
            if isinstance(result, dict) and "merged" in result and "batch" in result:
                all_current_customers = cast(Dict[str, Customer], result["merged"])
                batch_resp = result["batch"]
            else:
                all_current_customers = cast(Dict[str, Customer], result)

            if isinstance(batch_resp, list):
                for entry in batch_resp:
                    bid = entry.get("bId")
                    if not bid:
                        continue
                    if "Fault" in entry:
                        self.audit.fail(
                            event_id=f"-:Customer:{bid}:{bid}",
                            group_id=None,
                            transaction_id=None,
                            error_category="Validation",
                            error_message="QuickBooks Fault",
                            error_details=None,
                            retryable=True,
                        )
                    else:
                        self.audit.ok(
                            event_id=f"-:Customer:{bid}:{bid}",
                            group_id=None,
                            transaction_id=None,
                        )
            else:
                # Fallback: mark all intended bIds as success.
                for bid in intended_bids:
                    self.audit.ok(
                        event_id=f"-:Customer:{bid}:{bid}",
                        group_id=None,
                        transaction_id=None,
                    )

            logger.info(f"Bulk customer creation merged payload: {all_current_customers}")
            return all_current_customers
        except Exception as e:
            logger.error(f"Error in create_customer_in_bulk: {str(e)}")
            return {}
