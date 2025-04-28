from fastapi import FastAPI

from masyg_extractor.integration_qb_v5.routers.qb_router import router as quickbook_router
from masyg_extractor.integration_v4.routers.xero_router  import router as xero_router

from .admin.admin_webhook import router as webhook_router

def register_routers(app: FastAPI):
    from .data_extractor_routes import router as file_extractor_router
    from .analytics_routes import router as analytics_router

    from .payment_routes import router as payment_router
    from .user_routes import router as user_router
    from masyg_extractor.dummy_endpoint.fake_http import router as fake_router

    # Include routers under the '/api' prefix
    app.include_router(fake_router, prefix="/api")
    app.include_router(file_extractor_router, prefix="/api")
    app.include_router(payment_router, prefix="/api")
    app.include_router(user_router, prefix="/api")
    app.include_router(webhook_router, prefix="/api")

    # Include QuickBooks API router under a different prefix
    app.include_router(quickbook_router, prefix="")

    # Include Xero API router under a different prefix

    app.include_router(xero_router, prefix="")
    # app.include_router(quickbook_auth_router, prefix="/api")

    app.include_router(analytics_router, prefix="/api")
