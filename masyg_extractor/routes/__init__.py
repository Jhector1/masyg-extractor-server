import os
from fastapi import FastAPI

from masyg_extractor.integrations.accounting.registry import iter_accounting_routers
from masyg_extractor.integrations.accounting.shared.status_router import (
    router as accounting_status_router,
)
from masyg_extractor.integrations.bank.router import router as bank_router
from masyg_extractor.integrations.document_sources.google_drive.router import router as google_drive_router
from masyg_extractor.integrations.document_sources.gmail.router import router as gmail_router

from .admin.admin_webhook import router as webhook_router
from .csrf_routes import csrf_router


def register_routers(app: FastAPI):
    from .data_extractor_routes import router as file_extractor_router
    from .analytics_routes import router as analytics_router
    from .payment_routes import router as payment_router
    from .user_routes import router as user_router
    environment = os.getenv("FAST_API_ENV", "development").lower()

    # Test-only extraction endpoints must never be reachable in production.
    if environment != "production":
        from masyg_extractor.dummy_endpoint.fake_http import router as fake_router
        app.include_router(fake_router, prefix="/api")

    app.include_router(file_extractor_router, prefix="/api")
    app.include_router(payment_router, prefix="/api")
    app.include_router(user_router, prefix="/api")
    app.include_router(webhook_router, prefix="/api")
    app.include_router(csrf_router)

    for accounting_router in iter_accounting_routers():
        app.include_router(accounting_router, prefix="")
    app.include_router(accounting_status_router, prefix="")
    app.include_router(bank_router, prefix="")
    app.include_router(google_drive_router, prefix="")
    app.include_router(gmail_router, prefix="")
    app.include_router(analytics_router, prefix="/api")

    # Debug mutation endpoints must never be exposed in production.
    if environment != "production":
        from debug_routes import router as debug_router
        app.include_router(debug_router)
