import asyncio

import httpx
from fastapi import Request
from typing import Optional, Dict, Any

from masyg_extractor.integrations.accounting.shared.token_repository import get_integration_token
from masyg_extractor.services.my_log import logger
from fastapi import Request, HTTPException, status

XERO_BASE_URL = "https://api.xero.com/api.xro/2.0"


def _safe_xero_text(value: Any, fallback: str = "", limit: int = 500) -> str:
    text = str(value or "").strip()
    if not text:
        return fallback
    if len(text) > limit:
        return text[: limit - 1].rstrip() + "…"
    return text


def _xero_validation_messages(element: Any) -> list[str]:
    if not isinstance(element, dict):
        return []

    messages: list[str] = []
    validation_errors = element.get("ValidationErrors")
    if isinstance(validation_errors, list):
        for entry in validation_errors:
            if not isinstance(entry, dict):
                continue
            message = _safe_xero_text(entry.get("Message"))
            if message and message not in messages:
                messages.append(message)

    element_message = _safe_xero_text(element.get("Message"))
    if element_message and element_message not in messages:
        messages.append(element_message)

    return messages


def _normalize_xero_http_error(response: httpx.Response) -> Dict[str, Any]:
    status_code = int(response.status_code)
    try:
        body = response.json()
    except Exception:
        body = {}

    if not isinstance(body, dict):
        body = {}

    if status_code in {401, 403}:
        default_message = "Xero authorization expired. Reconnect Xero and try again."
    elif status_code == 429:
        default_message = "Xero rate limit reached. Please try again shortly."
    elif 500 <= status_code:
        default_message = "Xero is temporarily unavailable. Please try again."
    else:
        default_message = "Xero rejected the request."

    top_message = _safe_xero_text(body.get("Message"), default_message)
    document_errors: list[dict[str, Any]] = []

    elements = body.get("Elements")
    if isinstance(elements, list):
        for index, element in enumerate(elements):
            messages = _xero_validation_messages(element)
            if not messages:
                continue
            document_errors.append(
                {
                    "index": index,
                    "message": _safe_xero_text("; ".join(messages), default_message),
                }
            )

    top_validation = _xero_validation_messages(body)
    if top_validation:
        top_message = _safe_xero_text("; ".join(top_validation), top_message)

    return {
        "error": top_message,
        "status_code": status_code,
        "document_errors": document_errors,
    }



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
    xero_data = await asyncio.to_thread(get_integration_token, user_id, "xero")
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
        except httpx.HTTPStatusError as exc:
            normalized = _normalize_xero_http_error(exc.response)
            logger.warning(
                "Xero API rejected request status=%s error=%s",
                normalized["status_code"],
                normalized["error"],
            )
            return normalized
        except httpx.RequestError as exc:
            logger.warning(
                "Xero API transport failure error_type=%s",
                type(exc).__name__,
            )
            return {
                "error": "Xero service unavailable. Please try again.",
                "status_code": 502,
                "document_errors": [],
            }
