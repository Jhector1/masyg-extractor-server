import asyncio

import httpx
from fastapi import Request
from typing import Optional, Dict, Any

from masyg_extractor.integrations.quickbooks.repository.firestore_repository import get_quickbooks_token
from masyg_extractor.services.my_log import logger
from fastapi import Request, HTTPException, status

XERO_BASE_URL = "https://api.xero.com/api.xro/2.0"


async def xero_request(
        endpoint: str,
        user_id: str,
        payload: Optional[Dict[str, Any]] = None,
        method: str = "POST",
        **kwargs
) -> Dict[str, Any]:
    # Ensure the user is authenticated with Xero using namespaced session data.
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not authenticated"
        )
    xero_data = await asyncio.to_thread(get_quickbooks_token, user_id, "xero")
    if not xero_data or "accessToken" not in xero_data or "tenant_id" not in xero_data:
        raise Exception("Access Token or Tenant ID not found")

    access_token = xero_data["accessToken"]
    tenant_id = xero_data["tenant_id"]
    url = f"{XERO_BASE_URL}/{endpoint}"

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "Xero-tenant-id": tenant_id
    }

    # Debug prints (optional)


    async with httpx.AsyncClient() as client:
        try:
            method = method.upper()
            if method == "GET":
                params = payload if payload is not None else kwargs.pop("params", None)

                response = await client.get(url, headers=headers, params=params, **kwargs)

            elif method == "POST":
                response = await client.post(url, headers=headers, json=payload, **kwargs)
            elif method == "PUT":
                response = await client.put(url, headers=headers, json=payload, **kwargs)
            elif method == "DELETE":
                response = await client.delete(url, headers=headers, json=payload, **kwargs)
            else:
                raise Exception(f"Unsupported HTTP method: {method}")

            response.raise_for_status()
            response_json = response.json()
            logger.info(f"Xero API Response: {response.status_code}")

            return response_json
        except httpx.RequestError as e:
            error_message = f"Xero API Request Failed: {str(e)}"

            logger.error(error_message)
            return {"error": error_message}
