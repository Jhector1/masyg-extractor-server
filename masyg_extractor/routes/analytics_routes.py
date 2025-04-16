import json
import asyncio
import os
from datetime import datetime
from fastapi import APIRouter, Request, Depends, HTTPException, status
from fastapi.responses import JSONResponse
import redis.asyncio as redis

from masyg_extractor.config.jwt_config import get_current_user_from_cookie
from masyg_extractor.services.firestore_helpers import get_firestore_client, document_get
from masyg_extractor.services.dependencies import get_firebase_user

router = APIRouter()

# Create a global async Redis client (adjust host/port as needed)
# redis_host = os.environ.get("REDISHOST", "localhost")
# redis_port = int(os.environ.get("REDISPORT", 6379))
# redis_password = os.environ.get("REDISPASSWORD", None)  # Optional, if your production Redis requires auth
#
# redis_client = redis.Redis(
#     host=redis_host,
#     port=redis_port,
#     password=redis_password,
#     decode_responses=True
# )# Use an environment variable for the Redis URL, defaulting to localhost if not set.
redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
redis_client = redis.from_url(redis_url, decode_responses=True)

@router.get("/dashboard/analytics")
async def get_dashboard_analytics( current_user: dict = Depends(get_current_user_from_cookie),):
    user_id = current_user.get("userId")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="User ID not found"
        )

    cache_key = f"dashboard:analytics:{user_id}"
    cached_data = await redis_client.get(cache_key)
    if cached_data:
        return JSONResponse(content=json.loads(cached_data), status_code=200)

    # Get Firestore client and reference the user's groups
    firestore_client = await get_firestore_client()
    groups_ref = firestore_client.collection("users").document(user_id).collection("groups")

    # Retrieve groups using a blocking stream in an async thread
    groups_list = list(await asyncio.to_thread(lambda: list(groups_ref.stream())))

    # Initialize aggregation dictionaries and counters
    monthly_uploads = {}  # e.g., { "2023-04": 3, "2023-05": 5 }
    total_spending_by_month = {}
    top_vendors = {}
    category_breakdown = {}
    total_files = 0
    successful_files = 0

    # Iterate over groups and their file subcollections
    for group_doc in groups_list:
        group_data = group_doc.to_dict() or {}
        metadata = group_data.get("metadata", {})
        upload_time_str = metadata.get("upload_time")
        if upload_time_str:
            try:
                dt = datetime.fromisoformat(upload_time_str)
            except Exception:
                continue
            month_key = dt.strftime("%Y-%m")
            for _ in metadata.get("files", []):
                monthly_uploads[month_key] = monthly_uploads.get(month_key, 0) + 1
        else:
            month_key = "unknown"

        # Get files for this group
        files_ref = group_doc.reference.collection("files")
        file_docs = list(await asyncio.to_thread(lambda: list(files_ref.stream())))
        # print(file_docs)
        for file_doc in file_docs:
            total_files += 1
            file_data = file_doc.to_dict()
            # print(file_data)
            # Determine if extraction was successful (no "error" field)
            if file_data.get("error"):
                continue
            successful_files += 1

            # Sum up total spending per month if available
            total_amount = 0
            if total_amount is not None:
                try:
                    amt = float(total_amount)
                    total_spending_by_month[month_key] = total_spending_by_month.get(month_key, 0) + amt
                except ValueError:
                    pass

            # Count vendors (for top vendors)
            vendor = file_data.get("vendor")
            if vendor:
                top_vendors[vendor] = top_vendors.get(vendor, 0) + 1

            # Aggregate spending by category
            for line_item in file_data.get("line_items", []):
                category = line_item.get("category", "").capitalize()
                total_amount= line_item.get("unit_price")
                if category and total_amount is not None:
                    try:
                        amt = float(total_amount)
                        category_breakdown[category] = round(category_breakdown.get(category, 0) + amt, 2)
                    except ValueError:
                        pass

    extraction_accuracy = (successful_files / total_files * 100) if total_files > 0 else 0

    # Prepare the aggregated analytics data
    analytics = {
        "monthly_uploads": monthly_uploads,
        "total_spending_by_month": total_spending_by_month,
        "top_vendors": sorted(top_vendors.items(), key=lambda x: x[1], reverse=True),
        "extraction_accuracy": extraction_accuracy,
        "category_breakdown": category_breakdown,
    }

    # Cache the result for 5 minutes (300 seconds)
    await redis_client.set(cache_key, json.dumps(analytics), ex=300)

    return JSONResponse(content=analytics, status_code=200)
