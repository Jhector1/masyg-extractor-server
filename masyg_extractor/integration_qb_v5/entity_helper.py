import asyncio
import json
import re
from collections import defaultdict
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

    # In: masyg_extractor/integration_qb_v5/entity_helper.py
    # Replace the whole get_non_existing_entities method with this version.

    async def get_non_existing_entities(
            self,
            local_entities: List[Entity],
            endpoint: str,
            name_field: str,
            id_field: str
    ) -> List[Entity]:
        """
        Compares local entities with integration entities and returns the list
        of local entities that don't exist in the integration.
        - For Items, prefer Sku matching if available.
        - For Customers (and others), use Name/Id.
        """
        integration_entities = await self.fetch_all_entities(endpoint, name_field, id_field)

        existing_ids = {e.get("Id") for e in integration_entities}
        existing_names = {e.get("Name") for e in integration_entities}
        existing_skus = {e.get("Sku") for e in integration_entities if e.get("Sku")}

        non_existing: List[Entity] = []
        for entity in local_entities:
            # Items: prefer Sku
            if endpoint == "Item" and isinstance(entity, Item):
                sku = getattr(entity, "sku", None)
                if sku:
                    if sku not in existing_skus:
                        non_existing.append(entity)
                else:
                    # fallback to Name/Id if no SKU
                    if (getattr(entity, "id", None) not in existing_ids) and (entity.name not in existing_names):
                        non_existing.append(entity)
                continue

            # Others: Name/Id
            ent_id = getattr(entity, "id", None)
            if ent_id not in existing_ids and entity.name not in existing_names:
                non_existing.append(entity)

        return non_existing

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
        if not entity_data_list and entity.lower() == "customer" and "Customer" in response:
            expected_response_key = "DisplayName"
            expected_id_field = "Id"
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
        # print(payload)

        response = await self.client.request(
            self.repo.get_integration_token(),
            "batch",  # using singular endpoint for bulk creation as expected by the API
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
        entity_data = response.get("BatchItemResponse")
        if isinstance(entity_data, list):
            return entity_data
        elif isinstance(entity_data, dict):
            return [entity_data]
        else:
            raise Exception(f"Unexpected response structure: {response}")

    import logging
    from collections import defaultdict
    from typing import Any, Dict, List, Optional, Union

    logger = logging.getLogger(__name__)

    # In: masyg_extractor/integration_qb_v5/entity_helper.py
    # Replace the whole create_entity_in_bulk_and_merge_with_current method with this version.

    from collections import defaultdict
    from typing import Any, Dict, List, Optional, Union

    async def create_entity_in_bulk_and_merge_with_current(
            self,
            current_entities: Dict[str, Union[Any, List[Any]]],
            entity: str,
            payload: Optional[Dict[str, Any]] = None,
            name_key: str = "Name",
            id_key: str = "Id",
            tracker_key: str = "bId"
    ) -> Dict[str, Union[Any, List[Any]]]:
        """
        Creates entities in bulk (if payload provided) and merges/updates IDs into
        `current_entities` based on SKU encoded in bId (best) or Name (fallback).

        - Expects QBO batch response entries like:
          { "Item": {...}, "bId": "...", "operation": "create" }
        """
        logger.info("Merging current entities for %s: %r", entity, current_entities)

        # If no batch to create, just backfill any missing IDs from integration
        batch = payload.get("BatchItemRequest") if payload else None
        if not batch:
            integration = await self.fetch_all_entities(entity, name_key, id_key)
            name_to_id = {e.get("Name"): e.get("Id") for e in integration if e.get("Name") and e.get("Id")}
            for tracker, items in current_entities.items():
                objs = items if isinstance(items, list) else [items]
                for obj in objs:
                    if getattr(obj, "id", None) is None:
                        obj.id = name_to_id.get(obj.name)
            return current_entities

        # 1) create new in bulk
        created = await self.create_entity_in_bulk(entity, payload)
        logger.info("Bulk-created entities: %r", created)

        # 2) normalize current_entities into tracker → [objects]
        tracker_map: Dict[str, List[Any]] = defaultdict(list)
        for tracker, val in current_entities.items():
            tracker_map[tracker].extend(val if isinstance(val, list) else [val])

        # 3) merge back using sku encoded in bId
        for e in created:
            b_id = e.get(tracker_key)
            if not b_id:
                continue

            # bId format: "<tracker>_<sku>"
            parts = b_id.split("_", 1)
            tracker = parts[0]
            sku_suffix = parts[1] if len(parts) > 1 else None

            # Pull inner object for the entity, e.g. "Item": {...}
            entity_obj = e.get(entity) or {}
            ent_id = entity_obj.get("Id")
            ent_name = entity_obj.get("Name")
            ent_sku = entity_obj.get("Sku")

            candidates = tracker_map.get(tracker, [])
            for local in candidates:
                # Prefer exact SKU match
                local_sku = getattr(local, "sku", None)
                if sku_suffix and local_sku and local_sku == sku_suffix:
                    if not getattr(local, "id", None):
                        local.id = ent_id
                        local.name = ent_name or local.name
                    break

                # Fallback: match by Name (only safe when unique)
                if local.name == ent_name and not getattr(local, "id", None):
                    local.id = ent_id
                    if hasattr(local, "sku") and not getattr(local, "sku", None):
                        setattr(local, "sku", ent_sku)
                    break

        return current_entities

    # In: masyg_extractor/integration_qb_v5/entity_helper.py
    # Replace the whole fetch_all_entities method with this version.

    async def fetch_all_entities(
            self,
            endpoint: str,
            name_field: str = "Name",
            id_field: str = "Id",
            params: Optional[Dict[str, str]] = None
    ) -> List[Dict[str, Any]]:
        """
        Fetches all entities from QuickBooks. Includes Sku for Items.
        """
        response = await self.client.request(
            self.repo.get_integration_token(),
            "query",
            method="GET",
            params={"query": f"SELECT * FROM {endpoint} STARTPOSITION 1 MAXRESULTS 1000"},
        )
        entities = response.get("QueryResponse", {}).get(endpoint, [])
        out: List[Dict[str, Any]] = []
        for entity in entities:
            row = {
                "Name": entity.get(name_field),
                "Id": entity.get(id_field),
            }
            # Capture Sku if this is an Item
            if endpoint == "Item" and "Sku" in entity:
                row["Sku"] = entity.get("Sku")
            out.append(row)
        return out

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
