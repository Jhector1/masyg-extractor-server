import asyncio
import os

import requests
from fastapi import Request, HTTPException, status
from fastapi.responses import JSONResponse

from masyg_extractor.config.jwt_config import get_current_user_from_cookie
from masyg_extractor.integrations.quickbooks.repository.firestore_repository import get_quickbooks_token


async def get_entities(request: Request, entity_type: str, user_id: str):
    """
    Fetches all customers or vendors from QuickBooks dynamically.

    Args:
        request (Request): The FastAPI Request object.
        entity_type (str): Either "Customer" or "Vendor".

    Returns:
        A JSONResponse containing the list of entities.
    """
    # First, extract the user information from the JWT using your dependency

    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not authenticated need user id"
        )

    # Retrieve QuickBooks token data from Firestore
    qb_token_data =  await asyncio.to_thread(get_quickbooks_token,user_id, "quickbooks")
    access_token = qb_token_data.get("accessToken")
    realm_id = qb_token_data.get("realmId")

    if not realm_id or not access_token:

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="QuickBooks integration not configured for user"
        )

    # Construct the URL using the environment variable for QB base URL
    QUICKBOOKS_URL = os.getenv('QUICKBOOKS_URL')
    if not QUICKBOOKS_URL:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="QuickBooks URL is not configured"
        )

    url = f"{QUICKBOOKS_URL}/{realm_id}/query"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json"
    }
    params = {"query": f"SELECT * FROM {entity_type} STARTPOSITION 1 MAXRESULTS 1000"}

    try:
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        data = response.json()
        entities = data.get("QueryResponse", {}).get(entity_type, [])
        return JSONResponse(content=entities, status_code=status.HTTP_200_OK)

    except requests.exceptions.HTTPError:
        try:
            error_details = response.json()
        except Exception:
            error_details = {}
        raise HTTPException(
            status_code=response.status_code,
            detail={
                "error": f"Failed to fetch {entity_type.lower()}s",
                "details": error_details
            }
        )


    except Exception as err:

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An error occurred: {str(err)}"
        )

