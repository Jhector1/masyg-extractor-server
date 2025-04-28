# ------------------------------------------------------------------------------
# QuickBooks Client Adapter
# ------------------------------------------------------------------------------
import asyncio
import os
from typing import Optional, Dict, Any

import httpx
from fastapi import HTTPException, status

from masyg_extractor.integration_qb_v5.core.integration_context import IntegrationContext
from masyg_extractor.integration_qb_v5.intergrate.baseAdapter import IntegrationClientAdapter
from masyg_extractor.integrations.quickbooks.quickbooks_client import QUICKBOOKS_URL

from masyg_extractor.services.my_log import logger


class QuickBooksClientAdapter(IntegrationClientAdapter):
    def __init__(self, context: IntegrationContext):
        super().__init__(context)
        self.base_url = os.getenv("QUICKBOOKS_URL", "https://quickbooks.api")

    async def request(
            self,
            quickbooks_token: Dict[str, Any],
            endpoint: str,
            method: str = "POST",
            payload: Optional[Dict[str, Any]] = None,
            params: Optional[Dict[str, Any]] = None,
            **kwargs
    ) -> Dict[str, Any]:



        if not quickbooks_token or "accessToken" not in quickbooks_token or "realmId" not in quickbooks_token:
            raise Exception("User not authenticated access token was not provide")

        access_token = quickbooks_token["accessToken"]
        realm_id = quickbooks_token["realmId"]
        url = f"{QUICKBOOKS_URL}/{realm_id}/{endpoint.lower()}?minorversion=75"

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
                    # params =kwargs.pop("params", None)
                    print('params', params)

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


                return response_json
            except httpx.RequestError as e:

                error_message = f"QuickBooks API Request Failed: {str(e)}. Response: {response.text if response is not None else 'No response'}"
                logger.error(error_message)

                return {"error": error_message}
