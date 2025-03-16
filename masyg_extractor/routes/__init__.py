from fastapi import FastAPI

from masyg_extractor.integrations.router  import router as quickbook_router
from masyg_extractor.integrations.authentication.quickbook_auth  import router as quickbook_auth_router

from .admin.admin_webhook import router as webhook_router

def register_routers(app: FastAPI):
    from .data_extractor_routes import router as file_extractor_router
    from .payment_routes import router as payment_router
    from .user_routes import router as user_router

    # Include routers under the '/api' prefix
    app.include_router(file_extractor_router, prefix="/api")
    app.include_router(payment_router, prefix="/api")
    app.include_router(user_router, prefix="/api")
    app.include_router(webhook_router, prefix="/api")

    # Include QuickBooks API router under a different prefix
    app.include_router(quickbook_router, prefix="")
    # app.include_router(quickbook_auth_router, prefix="/api")
