from typing import Dict
from masyg_extractor.services.my_log import logger
from masyg_extractor.utils.extensions import sio
from fastapi import FastAPI, Depends, Request, HTTPException


class ProgressLog:
    def __init__(self, client_id: str, log_key= "data-progress"):
        self._last_emitted_overall: Dict[str, float] = {}
        self.client_id = client_id
        self.log_key = log_key


    def calculate_overall_progress(self, progress: Dict[str, float]) -> float:
        """
        Calculate the overall progress for a single file based on its stages.
        The overall progress is the sum of all stage progress values.

        :param progress: Dictionary with stage names as keys and progress values as floats.
        :return: Sum of progress values, ideally ranging from 0 to 100.
        """
        overall = sum(progress.values())
        logger.debug(f"Calculated overall progress: {overall} for progress dict: {progress}")
        return overall

    def clear(self):
        """
        Clear the record of last emitted progress.
        """
        self._last_emitted_overall.clear()
        logger.debug("Cleared last emitted progress values.")

    async def safe_emit_progress(self, progress_value: float,  threshold: float = 1.0):
        """
        Emit a progress update that only increases (never goes backwards) and respects a threshold difference.

        :param progress_value: The new progress value (float) to be emitted.
        :param log_key: The event key to use when emitting the progress.
        :param threshold: The minimum change in progress required to trigger an emit.
        """
        last_val = self._last_emitted_overall.get(self.client_id, 0.0)
        # Ensure that the new value is at least as high as the last emitted one.
        monotonic_progress = max(progress_value, last_val)

        logger.debug(
            f"Client {self.client_id}: Last progress {last_val}, New progress {progress_value}, "
            f"Using monotonic progress {monotonic_progress}."
        )

        if abs(monotonic_progress - last_val) >= threshold:
            try:
                await sio.emit(self.log_key, {"progress": monotonic_progress}, room=self.client_id)
                self._last_emitted_overall[self.client_id] = monotonic_progress
                logger.debug(
                    f"Emitted progress update for client {self.client_id}: {monotonic_progress} (threshold: {threshold})."
                )
            except Exception as e:
                logger.error(f"Failed to emit progress for client {self.client_id}: {e}")
        else:
            logger.debug(
                f"Progress update for client {self.client_id} not emitted due to insufficient change "
                f"(Current: {last_val}, New: {monotonic_progress}, Threshold: {threshold})."
            )


class ExtractorProgressLog(ProgressLog):
    FILE_READ_WEIGHT = 5.0
    TEXT_EXTRACTION_WEIGHT = 10.0
    GPT_PROCESSING_WEIGHT = 50.0
    COMPRESSION_WEIGHT = 20.0
    FIRESTORE_UPDATE_WEIGHT = 15.0

    @staticmethod
    def get_file_progress_dict() -> Dict[str, float]:
        """
        Returns a dictionary representing the initial progress for each extraction stage.
        Each stage starts at 0.0 progress.

        :return: Dictionary with keys for each stage.
        """
        return {
            "file_read": 0.0,
            "text_extraction": 0.0,
            "gpt_processing": 0.0,
            "compression": 0.0,
            "firestore_update": 0.0,
        }


async def get_extractor_progress_logger(request: Request) -> ExtractorProgressLog:
    # Extract client ID from the session
    client_id = request.session.get("client_id", 'Guest')
    if not client_id:
        logger.error("Client ID not found in session")
        raise HTTPException(status_code=400, detail="Client ID not found in session.")
    # Return a new progress logger instance for this request
    return ExtractorProgressLog(client_id=client_id)




class IntegrationsProgressLog(ProgressLog):
    CREATING_ITEM_WEIGHT = 20.0
    CREATING_CUSTOMER_WEIGHT = 30.0
    CREATING_DOCUMENTS_WEIGHT = 50.0


    @staticmethod
    def get_file_progress_dict() -> Dict[str, float]:
        """
        Returns a dictionary representing the initial progress for each extraction stage.
        Each stage starts at 0.0 progress.

        :return: Dictionary with keys for each stage.
        """
        return {
            "creating_item": 0.0,
            "creating_customer": 0.0,
            "creating_invoice": 0.0,

        }
    def getWeight(self, title):
        if title.lower() == "creating_item":
            return IntegrationsProgressLog.CREATING_ITEM_WEIGHT
        elif title.lower() == "creating_customer":
            return IntegrationsProgressLog.CREATING_CUSTOMER_WEIGHT
        elif title.lower() == "creating_invoice":

            return IntegrationsProgressLog.CREATING_DOCUMENTS_WEIGHT
        else:
            return 10
async def get_integrations_progress_logger(request: Request, default_log_key ="quickbooks-progress") -> ExtractorProgressLog:
    # Extract client ID from the session
    client_id = request.session.get("client_id", 'Guest')
    if not client_id:
        logger.error("Client ID not found in session")
        raise HTTPException(status_code=400, detail="Client ID not found in session.")
    # Return a new progress logger instance for this request
    return IntegrationsProgressLog(client_id=client_id, log_key=default_log_key)

from fastapi import Request, Depends, HTTPException


def get_integrations_progress_logger_factory(default_log_key: str = "quickbooks-invoice-progress"):
    async def get_integrations_progress_logger(request: Request) -> ExtractorProgressLog:
        client_id = request.session.get("client_id", 'Guest')
        if not client_id:
            logger.error("Client ID not found in session")
            raise HTTPException(status_code=400, detail="Client ID not found in session.")
        return IntegrationsProgressLog(client_id=client_id, log_key=default_log_key)

    return get_integrations_progress_logger
