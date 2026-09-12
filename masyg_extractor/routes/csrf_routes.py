import os
import secrets

from fastapi import APIRouter, Response

from masyg_extractor.security import cookie_security_options

csrf_router = APIRouter()


@csrf_router.get("/api/csrf-token")
async def get_csrf_token(response: Response):
    token = secrets.token_urlsafe(32)
    cookie_opts = cookie_security_options(os.getenv("FAST_API_ENV"))
    response.set_cookie(
        key="csrf_token",
        value=token,
        max_age=60 * 60,
        secure=cookie_opts["secure"],
        samesite=cookie_opts["samesite"],
        httponly=False,
        path=cookie_opts["path"],
    )
    return {"csrfToken": token}
