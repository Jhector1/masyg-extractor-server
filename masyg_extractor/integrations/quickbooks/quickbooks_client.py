import httpx
from fastapi import Request
from typing import Optional, Dict, Any
from masyg_extractor.services.my_log import logger

QB_SANDBOX_URL = "https://sandbox-quickbooks.api.intuit.com/v3/company"


async def quickbooks_request(
        request: Request,
        endpoint: str,
        payload: Optional[Dict[str, Any]] = None,
        method: str = "POST",
        client_id: str = "",
        **kwargs
) -> Dict[str, Any]:
    # Retrieve QuickBooks authentication data from a dedicated namespace.
    qb_data = request.session.get("quickbooks")
    if not qb_data or "access_token" not in qb_data or "realm_id" not in qb_data:
        raise Exception("User not authenticated")

    access_token = qb_data["access_token"]
    realm_id = qb_data["realm_id"]
    url = f"{QB_SANDBOX_URL}/{realm_id}/{endpoint}?minorversion=75"

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Accept-Encoding": "identity"
    }

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
            logger.info(f"QuickBooks API Response: {response.status_code}")
            return response_json
        except httpx.RequestError as e:
            error_message = f"QuickBooks API Request Failed: {str(e)}"
            logger.error(error_message)
            return {"error": error_message}
