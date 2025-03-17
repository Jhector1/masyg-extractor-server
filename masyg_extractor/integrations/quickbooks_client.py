import requests
from fastapi import Request
from typing import Optional, Dict, Any
from masyg_extractor.services.my_log import logger
QB_SANDBOX_URL = "https://sandbox-quickbooks.api.intuit.com/v3/company"

def quickbooks_request(
    request: Request,
    endpoint: str,
    payload: Optional[Dict[str, Any]] = None,
    method: str = "POST",
    client_id: str = "",
    **kwargs
) -> Dict[str, Any]:
    """
    Sends an authenticated request to the QuickBooks API.
    """
    if "access_token" not in request.session or "realm_id" not in request.session:
        raise Exception("User not authenticated")

    access_token = request.session["access_token"]
    realm_id = request.session["realm_id"]
    url = f"{QB_SANDBOX_URL}/{realm_id}/{endpoint}?minorversion=75"

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Accept-Encoding": "identity"
    }

    try:
        method = method.upper()
        if method == "GET":
            params = payload if payload is not None else kwargs.pop("params", None)
            response = requests.get(url, headers=headers, params=params, **kwargs)
        elif method == "POST":
            response = requests.post(url, headers=headers, json=payload, **kwargs)
        elif method == "PUT":
            response = requests.put(url, headers=headers, json=payload, **kwargs)
        elif method == "DELETE":
            response = requests.delete(url, headers=headers, json=payload, **kwargs)
        else:
            raise Exception(f"Unsupported HTTP method: {method}")

        response.raise_for_status()
        response_json = response.json()
        logger.info(f"QuickBooks API Response: {response.status_code}")
        return response_json

    except requests.exceptions.RequestException as e:
        error_message = f"QuickBooks API Request Failed: {str(e)}"
        logger.error(error_message)
        try:
            return response.json()
        except Exception:
            return {"error": error_message}
