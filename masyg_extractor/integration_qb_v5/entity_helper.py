import asyncio
import re
from pprint import pprint
from typing import Optional, Dict, Any, List
from fastapi.responses import JSONResponse

from masyg_extractor.integration_qb_v5.core.integration_context import IntegrationContext
from masyg_extractor.integration_qb_v5.intergrate.baseAdapter import IntegrationClientAdapter
from masyg_extractor.integration_qb_v5.repository.firestore_repository import QuickBooksFirestoreService
from masyg_extractor.integration_qb_v5.domain.models import Item, Entity, Customer
from masyg_extractor.services.my_log import logger, send_log
from masyg_extractor.integrations.quickbooks.quickbooks_client import quickbooks_request
from masyg_extractor.services.progress_log import IntegrationsProgressLog


class EntityHelper:
    def __init__(
        self,
        context: IntegrationContext,
        repo: QuickBooksFirestoreService,
        client: IntegrationClientAdapter,
    ):
        self.context = context
        self.repo = repo
        self.client = client

    def entity_exists_locally(self, entity_id: str, cached_items: List[Dict[str, Any]]) -> bool:
        """Checks if an item with a particular code exists within the locally cached items."""
        return any(item.get("Id") == entity_id for item in cached_items)

    async def get_non_existing_entities(
        self, local_entities: List[Entity],
        endpoint: str, name_field: str, id_field: str
    ) -> List[Entity]:
        """
        Compares local entities with integration entities and returns
        a list of local entities that don't exist in the integration.
        """
        integration_entities = await self.fetch_all_entities(endpoint, name_field, id_field)
        integration_ids = {entity.get("Id") for entity in integration_entities}
        integration_names = {entity.get("Name") for entity in integration_entities}
        non_existing_entities = []
        for entity in local_entities:
            if isinstance(entity, Customer):
                if entity.name not in integration_names or entity.id not in integration_ids:
                    non_existing_entities.append(entity)
            else:
                if entity.id not in integration_ids:
                    non_existing_entities.append(entity)


        return non_existing_entities

    async def check_entity_exists(
        self, entity: str, identifier_field: str, identifier_value: str
    ) -> bool:
        """
        Asynchronously checks if an entity (Customer, Vendor, etc.) exists in QuickBooks using a given identifier field.
        """
        where_clause = f'{identifier_field}=="{identifier_value}"'
        endpoint_plural = f"{entity}s"
        response = await self.client.request(
            self.repo.get_integration_token(),
            endpoint_plural,
            method="GET",
            params={"where": where_clause},
        )
        exists = bool(response.get(endpoint_plural, []))
        logger.info(f"{entity} exists check for {identifier_field}='{identifier_value}': {exists}")
        return exists

    async def fetch_entity_id_by_name(self, entity: str, name: str) -> Optional[str]:
        """
        Asynchronously fetches the ID of an entity from QuickBooks given its display name.
        """
        endpoint_plural = f"{entity}s"
        where_clause = f'Name=="{name}"'
        try:
            response = await self.client.request(
                self.repo.get_integration_token(),
                endpoint_plural,
                method="GET",
                params={"where": where_clause},
            )
            results = response.get(endpoint_plural, [])
            if results:
                id_field = f"{entity}ID"
                entity_id = results[0].get(id_field)
                logger.info(f"Fetched {entity} ID by name '{name}': {entity_id}")
                return entity_id
        except Exception as e:
            logger.error(f"Error fetching {entity} ID by name: {e}")
        return None

    async def _simulate_progress(self, task: str, steps: int = 5) -> None:
        """
        Simulates progress for a given task by updating progress logs.
        """
        for step in range(steps):
            await asyncio.sleep(0.3)
            self.context.progress[f"creating_{task.lower()}"] = ((step + 1) / steps) * IntegrationsProgressLog.CREATING_CUSTOMER_WEIGHT
            overall = self.context.progress_logger.calculate_overall_progress(self.context.progress)
            await self.context.progress_logger.safe_emit_progress(overall)

    async def create_entity(self, entity: str, payload: Optional[Dict[str, Any]] = None) -> str:
        """
        Asynchronously creates a new entity in QuickBooks and returns its ID.
        Adjusts for alternative response structures (e.g., Xero may return "Contacts" for a customer).
        """
        await self._simulate_progress(entity)

        # Default expected key and id field.
        endpoint_plural = f"{entity}s"
        expected_response_key = endpoint_plural
        expected_id_field = f"{entity}ID"

        response = await self.client.request(
            self.repo.get_integration_token(),
            entity,  # using singular endpoint for creation
            payload=payload,
            method="POST",
        )
        logger.info(f"create_{entity.lower()} response received: {response}")
        pprint(response)
        # Update progress weight explicitly if needed
        self.context.progress[f"creating_{entity.lower()}"] = self.context.progress_logger.getWeight(
            f"creating_{entity.lower()}")
        overall = self.context.progress_logger.calculate_overall_progress(self.context.progress)
        await self.context.progress_logger.safe_emit_progress(overall)

        if not response or "Error" in response:
            error_msgs = response.get("Error", "Unknown error")
            await send_log(f"❌ Failed to create {entity.lower()}: {error_msgs}", user_room=self.context.client_id)
            raise Exception(f"Failed to create {entity.lower()}: {error_msgs}")

        # Try to get the list from the expected key.
        entity_data_list = response.get(expected_response_key, [])

        # Fallback for known alternative structure.
        if not entity_data_list and entity.lower() == "customer" and "Contacts" in response:
            expected_response_key = "Contacts"
            expected_id_field = "ContactID"
            entity_data_list = response.get(expected_response_key, [])

        if entity_data_list:
            new_entity_id = entity_data_list[0].get(expected_id_field)
            if new_entity_id:
                logger.info(f"{entity} created with ID: {new_entity_id}")
                return new_entity_id

        raise Exception(f"Unexpected response structure: {response}")

    async def create_entity_in_bulk(
        self, entity: str, payload: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """
        Asynchronously creates one or more entities in QuickBooks in bulk.
        Returns a list of entity data dictionaries.
        """
        await self._simulate_progress(entity)

        response = await self.client.request(
            self.repo.get_integration_token(),
            entity,  # using singular endpoint for bulk creation as expected by the API
            payload=payload,
            method="POST",
        )

        logger.info(f"create_{entity.lower()} response received: {response}")
        self.context.progress[f"creating_{entity.lower()}"] = self.context.progress_logger.getWeight(f"creating_{entity.lower()}")
        overall = self.context.progress_logger.calculate_overall_progress(self.context.progress)
        await self.context.progress_logger.safe_emit_progress(overall)

        if not response or "Fault" in response:
            errors = response.get("Fault", {}).get("Error", [])
            error_msgs = "; ".join(err.get("Message", "Unknown error") for err in errors) if errors else "Unknown error"
            await send_log(f"❌ Failed to create {entity.lower()}: {error_msgs}", user_room=self.context.client_id)
            raise Exception(f"Failed to create {entity.lower()}: {error_msgs}")

        # Assuming the response returns a list of created entities under the key 'entity'
        entity_data = response.get(entity)
        if isinstance(entity_data, list):
            return entity_data
        elif isinstance(entity_data, dict):
            return [entity_data]
        else:
            raise Exception(f"Unexpected response structure: {response}")

    async def create_entity_in_bulk_and_merge_with_current(
        self,
        current_entities: Dict[str, Any],
        entity: str,
        payload: Optional[Dict[str, Any]] = None,
        name_key: str = "Name",
        id_key: str = "ID",
        tracker_key: str = "bId"
    ) -> Dict[str, Any]:
        """
        Creates entities in bulk and merges the created entities with the current local entities.
        Updates the local entity's ID when the names match.
        """

        logger.info(f"Merging current entities: {current_entities}")
        if len(payload.get(entity)) <= 0:
            return current_entities
        created_entities = await self.create_entity_in_bulk(entity, payload)
        print(created_entities, "created_entities")

        # updated_entities = await self.create_entity(entity, payload)
        # Loop over each created entity and update the matching local entity by name.
        for created_entity in created_entities:
            if (name_key in created_entity and
                id_key in created_entity and
                tracker_key in created_entity):
                parts = re.split(r'_', created_entity[tracker_key])

                tracker = parts[0]

                any_object = current_entities.get(tracker, [])

                # print(any_object, name_key, id_key)

                if isinstance(any_object, list):
                    for local_entity in any_object:
                        if local_entity.name == created_entity.get(name_key) and (local_entity.id is None or local_entity.id == ''):
                            local_entity.name = created_entity.get(name_key)
                            if isinstance(local_entity, Item):
                                local_entity.sku = created_entity.get("Code")

                            local_entity.id = created_entity.get(id_key)
                            break
                else:
                    if any_object.name == created_entity.get(name_key) and (any_object.id is None or any_object.id ==''):
                        any_object.name = created_entity.get(name_key)
                        print(id_key)
                        any_object.id = created_entity.get(id_key)
        # print("jjfjf", current_entities)

        return current_entities

    async def fetch_all_entities(
        self, endpoint: str, name_field: str = "Name", id_field: str = "Id", where_clause: Optional[Dict[str, str]] = None
    ) -> List[Dict[str, Any]]:
        """
        Fetches all entities from QuickBooks. If a where_clause is provided, it is added to the GET request.
        """
        params = {}
        if where_clause:
            params["where"] = where_clause

        response = await self.client.request(
            self.repo.get_integration_token(),
            endpoint,
            method="GET",
            params=params,
        )
        entities = response.get(endpoint, [])
        return [{"Name": entity.get(name_field), "Id": entity.get(id_field)} for entity in entities]

    async def get_all_entities(
        self, endpoint: str, name_field: str = "Name", id_field: str = "Id", where_clause: Optional[str] = None
    ) -> JSONResponse:
        """
        Retrieves entities from QuickBooks, returning only the Name and Id for each.
        """
        try:
            filtered_entities = await self.fetch_all_entities(endpoint, name_field, id_field, where_clause)
            return JSONResponse(filtered_entities, status_code=200)
        except Exception as e:
            logger.error(f"Error retrieving items: {str(e)}")
            return JSONResponse(
                {"error": "Exception while retrieving items", "details": str(e)},
                status_code=500,
            )
