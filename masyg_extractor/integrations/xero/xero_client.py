import httpx
from fastapi import Request
from typing import Optional, Dict, Any
from masyg_extractor.services.my_log import logger

XERO_BASE_URL = "https://api.xero.com/api.xro/2.0"


async def xero_request(
        request: Request,
        endpoint: str,
        payload: Optional[Dict[str, Any]] = None,
        method: str = "POST",
        **kwargs
) -> Dict[str, Any]:
    # Ensure the user is authenticated with Xero using namespaced session data.
    xero_data = request.session.get("xero")
    if not xero_data or "access_token" not in xero_data or "tenant_id" not in xero_data:
        raise Exception("User not authenticated")

    access_token = xero_data["access_token"]
    tenant_id = xero_data["tenant_id"]
    url = f"{XERO_BASE_URL}/{endpoint}"

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "Xero-tenant-id": tenant_id
    }

    # Debug prints (optional)
    print('xero_request started', endpoint)

    async with httpx.AsyncClient() as client:
        try:
            method = method.upper()
            if method == "GET":
                params = payload if payload is not None else kwargs.pop("params", None)
                print("GET params:", params)
                response = await client.get(url, headers=headers, params=params, **kwargs)
                print("GET response:", response)
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
            print("Response JSON:", response_json)
            return response_json
        except httpx.RequestError as e:
            error_message = f"Xero API Request Failed: {str(e)}"
            print(error_message)
            logger.error(error_message)
            return {"error": error_message}
