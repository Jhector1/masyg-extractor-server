import asyncio

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse

from masyg_extractor.config.jwt_config import get_current_user_from_cookie
from masyg_extractor.services.analytics import aggregate_group_files
from masyg_extractor.services.cache import analytics_cache
from masyg_extractor.services.firestore_helpers import get_firestore_client

router = APIRouter()
CACHE_TTL_SECONDS = 300


@router.get("/dashboard/analytics")
async def get_dashboard_analytics(
    current_user: dict = Depends(get_current_user_from_cookie),
):
    user_id = current_user.get("userId")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User ID not found")

    cache_key = f"dashboard:analytics:{user_id}"
    cached_data = await analytics_cache.get_json(cache_key)
    if cached_data is not None:
        return JSONResponse(content=cached_data, status_code=200)

    firestore_client = await get_firestore_client()
    groups_ref = firestore_client.collection("users").document(user_id).collection("groups")
    group_docs = await asyncio.to_thread(lambda: list(groups_ref.stream()))

    groups_with_files = []
    for group_doc in group_docs:
        group_data = group_doc.to_dict() or {}
        files_ref = group_doc.reference.collection("files")
        file_docs = await asyncio.to_thread(lambda ref=files_ref: list(ref.stream()))
        file_data = [(file_doc.to_dict() or {}) for file_doc in file_docs]
        groups_with_files.append((group_data, file_data))

    analytics = aggregate_group_files(groups_with_files)

    # Cache is best-effort. An unavailable Redis instance must never make the
    # analytics endpoint fail after Firestore computation succeeded.
    await analytics_cache.set_json(cache_key, analytics, ttl_seconds=CACHE_TTL_SECONDS)
    return JSONResponse(content=analytics, status_code=200)
