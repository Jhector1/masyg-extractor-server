# ------------------------------------------------------------------------------
# Shared Context (Dependency Injection)
# ------------------------------------------------------------------------------
from dataclasses import dataclass
from typing import Dict

from fastapi import FastAPI, Request, HTTPException, status, APIRouter, Depends

from masyg_extractor.services.log_manager import LogManager
from masyg_extractor.services.progress_log import IntegrationsProgressLog


@dataclass
class IntegrationContext:
    request: Request
    user_id: str
    client_id: str
    progress_logger: IntegrationsProgressLog
    progress: Dict[str, float]
    doct_type: str
    extra_auth_params: str
    log_manager: LogManager
    integration: str
