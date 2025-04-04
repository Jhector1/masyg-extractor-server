import asyncio
from typing import Optional, Dict, Any
from fastapi import Request
from masyg_extractor.services.my_log import logger, send_log
from masyg_extractor.integrations.xero.xero_client import xero_request


async def check_entity_exists(
        request: Request,
        entity: str,
        identifier_field: str,
        identifier_value: str,
        client_id: str = ""
) -> bool:
    """
    Asynchronously checks if an entity (e.g. Contact, Invoice) exists in Xero using a given identifier field.
    """
    # Construct the endpoint and filtering clause.
    endpoint = entity + "s"  # e.g., "Contacts", "Invoices"
    where_clause = f'{identifier_field}=="{identifier_value}"'

    response = await xero_request(
        request,
        endpoint,
        method="GET",
        params={"where": where_clause},
        client_id=client_id
    )
    # Xero returns a list under the plural key.
    exists = bool(response.get(endpoint, []))
    logger.info(f"{entity} exists check for {identifier_field}='{identifier_value}': {exists}")
    return exists


async def fetch_entity_id_by_name(
        request: Request,
        entity: str,
        name: str,
        client_id: str = ""
) -> Optional[str]:
    """
    Asynchronously fetches the ID of an entity from Xero given its name.
    For example, for a Contact, it will search by the "Name" field.
    """
    endpoint = entity + "s"
    where_clause = f'Name=="{name}"'
    try:
        response = await xero_request(
            request,
            endpoint,
            method="GET",
            params={"where": where_clause},
            client_id=client_id
        )
        results = response.get(endpoint, [])
        if results:
            # Assume the ID field follows the convention: e.g., ContactID, InvoiceID, etc.
            id_field = entity + "ID"
            entity_id = results[0].get(id_field)
            logger.info(f"Fetched {entity} ID by name '{name}': {entity_id}")
            return entity_id
    except Exception as e:
        logger.error(f"Error fetching {entity} ID by name: {e}")
    return None


async def create_entity(
        request: Request,
        entity: str,
        display_name: str,
        payload_extra: Optional[Dict[str, Any]] = None,
        client_id: str = ""
) -> str:
    """
    Asynchronously creates a new entity in Xero and returns its ID.
    For example, when creating a Contact, this function wraps the contact payload inside a "Contacts" list.
    It also generates a default email address based on the display name.
    """
    sanitized_name = display_name.lower().replace(' ', '_').replace("'", "")
    email_address = f"{sanitized_name}@example.com"
    base_payload = {
        "Name": display_name,
        "EmailAddress": email_address
    }
    if payload_extra:
        base_payload.update(payload_extra)

    # Xero expects the payload to be wrapped in a list under the pluralized key.
    endpoint = entity + "s"  # e.g., "Contacts"
    payload = {endpoint: [base_payload]}

    response = await xero_request(
        request,
        endpoint,
        payload=payload,
        method="POST",
        client_id=client_id
    )
    logger.info(f"create_{entity.lower()} response received.")

    # Check for errors in the response (this may vary based on Xero's error structure)
    if not response or "Error" in response:
        error_msgs = response.get("Error", "Unknown error")
        await send_log(f"❌ Failed to create {entity.lower()}: {error_msgs}", user_room=client_id)
        raise Exception(f"Failed to create {entity.lower()}: {error_msgs}")

    # Expect the created entity data to be returned in a list under the same key.
    entity_data_list = response.get(endpoint, [])
    if entity_data_list and len(entity_data_list) > 0:
        id_field = entity + "ID"
        new_entity_id = entity_data_list[0].get(id_field)
        if new_entity_id:

            logger.info(f"{entity} created with ID: {new_entity_id}")
            return new_entity_id
    raise Exception(f"Unexpected response structure: {response}")
