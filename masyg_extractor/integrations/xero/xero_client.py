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
    # client_id: str = "",
    **kwargs
) -> Dict[str, Any]:
    print('true', 3333)
    # Ensure the user is authenticated with Xero
    if "xero_access_token" not in request.session or "xero_tenant_id" not in request.session:
        raise Exception("User not authenticated")

    access_token = request.session["xero_access_token"]
    tenant_id = request.session["xero_tenant_id"]
    url = f"{XERO_BASE_URL}/{endpoint}"

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        # "Content-Type": "application/json",
        "Xero-tenant-id": tenant_id
        # "Accept-Encoding": "identity"
    }
    #GET https://api.xero.com/api.xro/2.0/Items

    print('true', 99)
    async with httpx.AsyncClient() as client:
        try:
            method = method.upper()
            if method == "GET":
                params = payload if payload is not None else kwargs.pop("params", None)
                print(params)
                response = await client.get(url, headers=headers, params=params, **kwargs)
                print(response)
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
            print(response_json)

            return response_json
        except httpx.RequestError as e:
            error_message = f"Xero API Request Failed: {str(e)}"
            print(error_message)
            logger.error(error_message)
            return {"error": error_message}
