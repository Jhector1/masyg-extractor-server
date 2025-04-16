import asyncio
from typing import Optional, Dict, Any
from fastapi import Request

from masyg_extractor.integration_v3.core.integration_context import IntegrationContext
from masyg_extractor.integration_v3.intergrate.baseAdapter import IntegrationClientAdapter
from masyg_extractor.integration_v3.repository.firestore_repository import QuickBooksFirestoreService
from masyg_extractor.services.my_log import logger, send_log
from masyg_extractor.integrations.quickbooks.quickbooks_client import quickbooks_request
from masyg_extractor.services.progress_log import IntegrationsProgressLog


class EntityHelper:
    def __init__(self, context: IntegrationContext, repo: QuickBooksFirestoreService, client: IntegrationClientAdapter):
        self.context = context
        self.repo = repo
        self.client = client

    async def check_entity_exists(self,

                                  entity: str,
                                  identifier_field: str,
                                  identifier_value: str,

                                  ) -> bool:
        """
        Asynchronously checks if an entity (Customer, Vendor, etc.) exists in QuickBooks using a given identifier field.
        """
        exists=  bool(self.fetch_entity(entity, identifier_field, identifier_value))
        # if self.context.integration == 'quickbooks':
        #     query = {"query": f"SELECT * FROM {entity} WHERE {identifier_field} = '{identifier_value}'"}
        #     endpoint = "query"
        # else:
        #     endpoint = entity + "s"
        #     query ={"where": f'{identifier_field}=="{identifier_value}"'}
        #
        # response = await self.client.request(
        #     self.repo.get_integration_token(),
        #     endpoint,
        #
        #     method="GET",
        #     params=query )
        # if self.context.integration == 'quickbooks':
        #
        #     exists = bool(response.get("QueryResponse", {}).get(entity))
        # else:
        #     exists = bool(response.get(endpoint, []))
        #
        logger.info(f"{entity} exists check for {identifier_field}='{identifier_value}': {exists}")
        return exists

    async def fetch_entity(self,

                                  entity: str,
                                  identifier_field: str,
                                  identifier_value: str,
                           field= "*"

                                  ) -> Optional[Dict[str, Any]]:
        """
        Asynchronously checks if an entity (Customer, Vendor, etc.) exists in QuickBooks using a given identifier field.
        """
        if self.context.integration == 'quickbooks':
            query = {"query": f"SELECT {field} FROM {entity} WHERE {identifier_field} = '{identifier_value}'"}
            endpoint = "query"
        else:
            endpoint = entity
            query ={"where": f'{identifier_field}=="{identifier_value}"'}

        response = await self.client.request(
            self.repo.get_integration_token(),
            endpoint,

            method="GET",
            params=query )
        if self.context.integration == 'quickbooks':

            results = response.get("QueryResponse", {}).get(entity)
        else:
            results = response.get(endpoint, [])

        # logger.info(f"{entity} exists check for {identifier_field}='{identifier_value}': {exists}")
        return results


    async def fetch_entity_id_by_name(self,

                                      entity: str,
                                      identifier_field: str,
                                      identifier_value: str,

                                      ) -> Optional[str]:
        """
        Asynchronously fetches the ID of an entity from QuickBooks given its display name.
        """
        # if self.context.

        # query = f"SELECT Id FROM {entity} WHERE DisplayName = '{name}'"
        try:
            # response = await self.client.request(
            #     self.repo.get_integration_token(),
            #
            #     "query",
            #
            #     method="GET",
            #     params={"query": query},
            #
            # )
            results = self.fetch_entity(entity, identifier_field, identifier_value )
            if results:
                if self.context.integration == 'quickbooks':
                    entity_id = results[0]["Id"]
                else:
                    id_field = entity + "ID"
                    entity_id = results[0].get(id_field)
                logger.info(f"Fetched {entity} ID by name '{identifier_value}': {entity_id}")
                return entity_id
        except Exception as e:
            logger.error(f"Error fetching {entity} ID by name: {e}")
        return None

    async def create_entity(
            self,
            entity: str,

            payload: Optional[Dict[str, Any]] = None,

    ) -> str:
        """
        Asynchronously creates a new entity in QuickBooks and returns its ID.
        Adds a default PrimaryEmailAddr based on the display name.
        """
        steps = 5


        for step in range(steps):
            await asyncio.sleep(0.3)
            self.context.progress[f"creating_{entity.lower()}"] = ((
                                                                               step + 1) / steps) * IntegrationsProgressLog.CREATING_CUSTOMER_WEIGHT
            await self.context.progress_logger.safe_emit_progress(
                self.context.progress_logger.calculate_overall_progress(self.context.progress))

        # sanitized_name = display_name.lower().replace(' ', '_').replace("'", "")
        # email_address = f"{sanitized_name}@example.com"
        # payload = {
        #     default_field: display_name,
        #     # "PrimaryEmailAddr": {"Address": email_address}
        # }

        # if payload_extra:
        #     payload.update(payload_extra)

        response = await self.client.request(
            self.repo.get_integration_token(),
            entity,

            payload=payload,
            method="POST",

        )
        print(response)
        logger.info(f"create_{entity.lower()} response received.")
        self.context.progress[f"creating_{entity.lower()}"] = self.context.progress_logger.getWeight(
            f"creating_{entity.lower()}")

        await self.context.progress_logger.safe_emit_progress(
            self.context.progress_logger.calculate_overall_progress(self.context.progress))

        if not response or "Error" in response or "Fault" in response:
            if self.context.integration == 'quickbooks':
                errors = response.get("Fault", {}).get("Error", [])
                error_msgs = "; ".join([err.get("Message", "Unknown error") for err in errors])

            else:
                error_msgs = response.get("Error", "Unknown error")
            # Await the async logging call rather than scheduling it
            await send_log(f"❌ Failed to create {entity.lower()}: {error_msgs if errors else 'Unknown error'}",
                           user_room=self.context.client_id)
            raise Exception(f"Failed to create {entity.lower()}: {error_msgs if errors else 'Unknown error'}")
        entity_data = response.get(entity)
        if entity_data and "Id" in entity_data:
            new_entity_id = entity_data["Id"]
            logger.info(f"{entity} created with ID: {new_entity_id}")
            return new_entity_id
        raise Exception(f"Unexpected response structure: {response}")
