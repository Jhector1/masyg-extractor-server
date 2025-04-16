# ------------------------------------------------------------------------------
# QuickBooks Client Adapter
# ------------------------------------------------------------------------------

import os
from typing import Optional, Dict, Any

import httpx

from masyg_extractor.integration_v3.core.integration_context import IntegrationContext
from masyg_extractor.integration_v3.intergrate.baseAdapter import IntegrationClientAdapter

from masyg_extractor.services.my_log import logger


class IntegrationClientAdapterImpl(IntegrationClientAdapter):
    def __init__(self, context: IntegrationContext, endpoint):
        super().__init__(context)
        self.context = context
        self.base_url = os.getenv(f"{self.context.integration.upper()}_URL", f"https://{self.context.integration}.api")

    async def request(
            self,
            integration_token: Dict[str, Any],
            endpoint: str,
            method: str = "POST",
            payload: Optional[Dict[str, Any]] = None,
            params: Optional[Dict[str, Any]] = None,
            **kwargs
    ) -> Dict[str, Any]:

        if not integration_token or "accessToken" not in integration_token or self.context.extra_auth_params not in integration_token:
            raise Exception("User not authenticated access token was not provide")

        access_token = integration_token["accessToken"]
        extra_auth = integration_token[self.context.extra_auth_params]

        if self.context.integration.lower() == 'xero':
            url = f"{self.base_url}/{endpoint.lower()}"

            headers = {
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
                "Xero-tenant-id": extra_auth
            }
        else:
            url = f"{self.base_url}/{extra_auth}/{endpoint.lower()}?minorversion=75"


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

                error_message = f"{self.context.integration.upper()} API Request Failed: {str(e)}. Response: {response.text if response is not None else 'No response'}"
                logger.error(error_message)

                return {"error": error_message}
