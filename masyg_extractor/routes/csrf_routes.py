from fastapi import APIRouter, Response
import secrets

# … your existing imports and router definition …

# If you want CSRF at `/api/csrf-token`, make a new router (no `/user` prefix)
csrf_router = APIRouter()

@csrf_router.get("/api/csrf-token")
async def get_csrf_token(response: Response):
    """
    Double‑submit CSRF: returns a fresh token in both
    a JS‑readable cookie and the JSON body.
    """
    token = secrets.token_urlsafe(32)
    response.set_cookie(
        key="csrf_token",
        value=token,
        max_age=60 * 60,    # 1 hour
        secure=True,        # HTTPS only in prod
        samesite="lax",     # defend against cross‑site posts
        httponly=False      # allow JS to read it
    )
    return {"csrfToken": token}
