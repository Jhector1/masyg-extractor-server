# ------------------------------------------------------------------------------
# QuickBooks Client Adapter
# ------------------------------------------------------------------------------
import asyncio
import json
import os
from typing import Optional, Dict, Any

import httpx
from fastapi import HTTPException, status

from masyg_extractor.integration_v2.core.integration_context import IntegrationContext
from masyg_extractor.integration_v2.intergrate.baseAdapter import IntegrationClientAdapter
from masyg_extractor.integrations.quickbooks.quickbooks_client import QUICKBOOKS_URL

from masyg_extractor.services.my_log import logger


class XeroClientAdapter(IntegrationClientAdapter):
    def __init__(self, context: IntegrationContext):
        super().__init__(context)
        self.base_url = os.getenv("XERO_URL", "https://api.xero.com/api.xro/2.0")

    async def request(
            self,
            xero_token: Dict[str, Any],
            endpoint: str,
            method: str = "POST",
            payload: Optional[Dict[str, Any]] = None,
            params: Optional[Dict[str, Any]] = None,
            **kwargs
    ) -> Dict[str, Any]:

        if not xero_token or "accessToken" not in xero_token or "tenant_id" not in xero_token:
            raise Exception("Access Token or Tenant ID not found")

        access_token = xero_token["accessToken"]
        tenant_id = xero_token["tenant_id"]
        url = f"{self.base_url}/{endpoint}"

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "Xero-tenant-id": tenant_id
        }
        print("[DEBUG] Payload:", json.dumps(payload, indent=2))

        async with httpx.AsyncClient() as client:
            try:
                method = method.upper()
                if method == "GET":
                    # print(params)
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
                # print(response)
                response.raise_for_status()
                response_json = response.json()
                logger.info(f"Xero API Response: {response.status_code}")

                return response_json
            except httpx.RequestError as e:
                error_message = f"Xero API Request Failed: {str(e)}"

                logger.error(error_message)
                return {"error": error_message}
