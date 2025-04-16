# ------------------------------------------------------------------------------
# Integration Client Adapter (Abstract Base Class)
# ------------------------------------------------------------------------------
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any

from masyg_extractor.integration_v2.core.integration_context import IntegrationContext


class IntegrationClientAdapter(ABC):
    def __init__(self, context: IntegrationContext):
        self.context = context

    @abstractmethod
    async def request(
        self,
        quickbooks_token: Dict[str, Any],
        endpoint: str,
        method: str = "POST",
        payload: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        pass
