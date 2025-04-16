import asyncio
from typing import Optional, Dict, Any
from fastapi import Request
from masyg_extractor.services.my_log import logger, send_log
from masyg_extractor.integrations.quickbooks.quickbooks_client import quickbooks_request
from masyg_extractor.services.progress_log import IntegrationsProgressLog


async def check_entity_exists(
    request: Request,
    entity: str,
    identifier_field: str,
    identifier_value: str,
    user_id: str,
    client_id: str = ""
) -> bool:
    """
    Asynchronously checks if an entity (Customer, Vendor, etc.) exists in QuickBooks using a given identifier field.
    """
    query = f"SELECT * FROM {entity} WHERE {identifier_field} = '{identifier_value}'"

    response = await quickbooks_request(
        request,
        "query",
        user_id=user_id,
        method="GET",
        params={"query": query},

        client_id=client_id)

    exists = bool(response.get("QueryResponse", {}).get(entity))

    logger.info(f"{entity} exists check for {identifier_field}='{identifier_value}': {exists}")
    return exists

async def fetch_entity_id_by_name(
    request: Request,
    entity: str,
    name: str,
    user_id: str,
    client_id: str = ""
) -> Optional[str]:
    """
    Asynchronously fetches the ID of an entity from QuickBooks given its display name.
    """
    query = f"SELECT Id FROM {entity} WHERE DisplayName = '{name}'"
    try:
        response = await quickbooks_request(
            request,
            "query",
            user_id=user_id,
            method="GET",
            params={"query": query},
            client_id=client_id
        )
        results = response.get("QueryResponse", {}).get(entity, [])
        if results:
            entity_id = results[0]["Id"]
            logger.info(f"Fetched {entity} ID by name '{name}': {entity_id}")
            return entity_id
    except Exception as e:
        logger.error(f"Error fetching {entity} ID by name: {e}")
    return None

async def create_entity(
    request: Request,
    entity: str,
    display_name: str,
    user_id: str,

        progress_logger: IntegrationsProgressLog,
        progress: Dict[str, float],
    payload_extra: Optional[Dict[str, Any]] = None,
    client_id: str = ""
) -> str:
    """
    Asynchronously creates a new entity in QuickBooks and returns its ID.
    Adds a default PrimaryEmailAddr based on the display name.
    """
    steps = 5


    for step in range(steps):
        await asyncio.sleep(0.3)
        progress[f"creating_customer"] = ((step + 1) / steps) * IntegrationsProgressLog.CREATING_CUSTOMER_WEIGHT
        await progress_logger.safe_emit_progress(progress_logger.calculate_overall_progress(progress))

    sanitized_name = display_name.lower().replace(' ', '_').replace("'", "")
    email_address = f"{sanitized_name}@example.com"
    payload = {
        "DisplayName": display_name,
        "PrimaryEmailAddr": {"Address": email_address}
    }

    if payload_extra:
        payload.update(payload_extra)
    response = await quickbooks_request(
        request,
        entity.lower(),
        user_id=user_id,
        payload=payload,
        method="POST",
        client_id=client_id
    )
    logger.info(f"create_{entity.lower()} response received.")
    progress[f"creating_customer"] = IntegrationsProgressLog.CREATING_CUSTOMER_WEIGHT
    await progress_logger.safe_emit_progress(progress_logger.calculate_overall_progress(progress))

    if not response or "Fault" in response:
        errors = response.get("Fault", {}).get("Error", [])
        error_msgs = "; ".join([err.get("Message", "Unknown error") for err in errors])
        # Await the async logging call rather than scheduling it
        await send_log(f"❌ Failed to create {entity.lower()}: {error_msgs if errors else 'Unknown error'}", user_room=client_id)
        raise Exception(f"Failed to create {entity.lower()}: {error_msgs if errors else 'Unknown error'}")
    entity_data = response.get(entity)
    if entity_data and "Id" in entity_data:
        new_entity_id = entity_data["Id"]
        logger.info(f"{entity} created with ID: {new_entity_id}")
        return new_entity_id
    raise Exception(f"Unexpected response structure: {response}")
