from typing import Dict

from masyg_extractor.services.my_log import logger
from masyg_extractor.utils.extensions import sio


def calculate_overall_progress(progress: Dict[str, float]) -> float:
    """
    For a single file, the overall progress is the sum of its stages.
    (Max is 100 if all stages are complete.)
    """
    return sum(progress.values())

# We'll use a global dictionary to track the last emitted progress per client.
# A dictionary to store last emitted progress per client to avoid duplicate emissions.
_last_emitted_overall: Dict[str, float] = {}

async def safe_emit_progress(client_id: str, progress_value: float, threshold: float = 1.0):
    """
        Emit a progress update that never goes backwards.
        """
    last_val = _last_emitted_overall.get(client_id, 0)
    # Ensure that the new value is at least as high as the last emitted one.
    monotonic_progress = max(progress_value, last_val)
    if abs(monotonic_progress - last_val) >= threshold:
        await sio.emit("data-progress", {"progress": monotonic_progress}, room=client_id)
        _last_emitted_overall[client_id] = monotonic_progress

FILE_READ_WEIGHT = 5.0
TEXT_EXTRACTION_WEIGHT = 10.0
GPT_PROCESSING_WEIGHT = 50.0
COMPRESSION_WEIGHT = 20.0
FIRESTORE_UPDATE_WEIGHT = 15.0
def get_file_progress_dict() -> Dict[str, float]:
    # Define keys for each stage with a target weight (all out of 100)
    return {
        "file_read": 0.0,
        "text_extraction": 0.0,
        "gpt_processing": 0.0,
        "compression": 0.0,
        "firestore_update": 0.0,
    }
