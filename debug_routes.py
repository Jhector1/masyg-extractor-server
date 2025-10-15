# debug_routes.py
import asyncio
from fastapi import APIRouter, Query
from masyg_extractor.utils.extensions import sio

router = APIRouter(prefix="/debug")

@router.post("/ticker")
async def start_ticker(client_id: str = Query(...), file_id: str = Query("debug-file")):
    async def run():
        # ensure the latest state renders even if you missed initial events
        await sio.emit("data-progress", {"progress": 0.0, "file_id": file_id, "stage": "Starting"}, room=client_id)
        for i in range(1, 101):
            await asyncio.sleep(0.1)  # 100ms
            await sio.emit("data-progress",
                           {"progress": float(i), "file_id": file_id, "stage": "Debug ticker"},
                           room=client_id)
        # overall for the top bar
        await sio.emit("data-progress", {"progress": 100.0, "file_id": None}, room=client_id)

    # fire-and-forget
    asyncio.create_task(run())
    return {"ok": True}
