# services/progress_log.py
from __future__ import annotations

import time
from typing import Dict, Tuple, Optional
from fastapi import Request, HTTPException

from masyg_extractor.services.my_log import logger
from masyg_extractor.utils.extensions import sio


class ProgressLog:
    """
    Emits monotonic, thresholded progress updates.
    Tracks last value per (log_key, file_id) so multiple files can progress independently.
    Also keeps a small registry of per-file totals to auto-emit overall on every per-file update.
    """

    def __init__(
        self,
        client_id: str,
        log_key: str = "data-progress",
        min_interval_ms: int = 80,  # frequent for smooth UI
    ):
        # key: (log_key, file_id_or_overall) -> last emitted value
        self._last: Dict[Tuple[str, str], float] = {}
        # key: (log_key, file_id_or_overall) -> last emitted timestamp (ms)
        self._last_ts: Dict[Tuple[str, str], float] = {}
        # per-file registry for computing overall
        self._registry: Dict[str, float] = {}

        self.client_id = client_id
        self.log_key = log_key
        self.min_interval_ms = min_interval_ms

    def calculate_overall_progress(self, progress: Dict[str, float]) -> float:
        overall = float(sum(progress.values()))
        logger.debug(f"Calculated overall progress: {overall:.2f} for {progress}")
        return overall

    def clear(self):
        self._last.clear()
        self._last_ts.clear()
        self._registry.clear()

    async def _emit_single(
        self,
        key_id: str,
        value: float,
        *,
        stage: Optional[str] = None,
        threshold: float = 0.1,
    ):
        """
        Internal monotonic emitter for a single (file_id or __overall__) key.
        """
        key = (self.log_key, key_id)
        last_val = self._last.get(key, 0.0)
        new_val = max(float(value), last_val)

        now = time.time() * 1000.0
        last_ts = self._last_ts.get(key, 0.0)
        interval_ok = (now - last_ts) >= self.min_interval_ms

        if (new_val - last_val) >= threshold and interval_ok:
            try:
                # For __overall__, file_id=None
                payload = {"progress": new_val, "file_id": None if key_id == "__overall__" else key_id}
                if stage is not None and key_id != "__overall__":
                    payload["stage"] = stage
                await sio.emit(self.log_key, payload, room=self.client_id)
                self._last[key] = new_val
                self._last_ts[key] = now
                logger.debug(f"Emit {self.log_key} id={key_id} -> {new_val:.2f} (stage={stage})")
            except Exception as e:
                logger.error(f"Emit failed id={key_id}: {e}")
        else:
            logger.debug(
                f"Skip emit id={key_id} (last={last_val:.2f}, new={new_val:.2f}, interval_ok={interval_ok})"
            )

        # services/progress_log.py  (append inside class ProgressLog)

    async def safe_emit_progress(
            self,
            progress_map: Optional[Dict[str, float]] = None,
            *,
            stage: Optional[str] = None,
            threshold: float = 0.1,
    ) -> float:
        """
        Safely emit overall progress and return the value emitted.

        If progress_map is provided, overall is computed from it using
        calculate_overall_progress(progress_map). Otherwise overall is the
        average of the internal per-file registry (as in emit()).

        Never raises; logs and returns the computed overall.
        """
        try:
            if isinstance(progress_map, (int, float)):
                overall = max(0.0, min(100.0, float(progress_map)))
                await self._emit_single("__overall__", overall, stage=stage, threshold=threshold)
                return overall
                # ↑↑↑ ADD THIS GUARD ↑↑↑

            if progress_map is not None:
                overall = float(self.calculate_overall_progress(progress_map))
                overall = max(0.0, min(100.0, overall))
                await self._emit_single("__overall__", overall, stage=stage, threshold=threshold)
                return overall

            if progress_map is not None:
                overall = float(self.calculate_overall_progress(progress_map))
                overall = max(0.0, min(100.0, overall))
                await self._emit_single("__overall__", overall, stage=stage, threshold=threshold)
                return overall

            # Fall back to registry average (same as in emit)
            if self._registry:
                overall = sum(self._registry.values()) / len(self._registry)
            else:
                overall = 0.0
            overall = max(0.0, min(100.0, float(overall)))
            await self._emit_single("__overall__", overall, stage=stage, threshold=threshold)
            return overall
        except Exception as e:
            logger.error(f"safe_emit_progress failed: {e}")
            # best-effort report
            if progress_map is not None:
                return max(0.0, min(100.0, float(self.calculate_overall_progress(progress_map))))
            if self._registry:
                return max(0.0, min(100.0, float(sum(self._registry.values()) / len(self._registry))))
            return 0.0

    async def emit(
        self,
        progress_value: float,
        *,
        file_id: Optional[str] = None,
        stage: Optional[str] = None,
        threshold: float = 0.1,
    ):
        """
        Public API. Emits per-file progress (with optional stage) and ALSO updates overall
        by averaging the per-file registry. If file_id is None, only overall is emitted.
        """
        if file_id:
            # record per-file value, emit it
            self._registry[file_id] = progress_value
            await self._emit_single(file_id, progress_value, stage=stage, threshold=threshold)

            # recompute overall and emit
            if self._registry:
                overall = sum(self._registry.values()) / len(self._registry)
                await self._emit_single("__overall__", overall, stage=None, threshold=threshold)
        else:
            # explicit overall-only update
            await self._emit_single("__overall__", progress_value, stage=None, threshold=threshold)


class ExtractorProgressLog(ProgressLog):
    FILE_READ_WEIGHT = 5.0
    TEXT_EXTRACTION_WEIGHT = 10.0
    GPT_PROCESSING_WEIGHT = 50.0
    COMPRESSION_WEIGHT = 20.0
    FIRESTORE_UPDATE_WEIGHT = 15.0

    @staticmethod
    def get_file_progress_dict() -> Dict[str, float]:
        return {
            "file_read": 0.0,
            "text_extraction": 0.0,
            "gpt_processing": 0.0,
            "compression": 0.0,
            "firestore_update": 0.0,
        }


async def get_extractor_progress_logger(request: Request) -> ExtractorProgressLog:
    client_id = request.session.get("client_id", "Guest")
    if not client_id:
        logger.error("Client ID not found in session")
        raise HTTPException(status_code=400, detail="Client ID not found in session.")
    return ExtractorProgressLog(client_id=client_id)


class IntegrationsProgressLog(ProgressLog):
    CREATING_ITEM_WEIGHT = 20.0
    CREATING_CUSTOMER_WEIGHT = 30.0
    CREATING_DOCUMENTS_WEIGHT = 50.0

    @staticmethod
    def get_file_progress_dict() -> Dict[str, float]:
        return {"creating_item": 0.0, "creating_customer": 0.0, "creating_invoice": 0.0}

    def getWeight(self, title: str) -> float:
        t = (title or "").lower()
        if t == "creating_item": return self.CREATING_ITEM_WEIGHT
        if t == "creating_customer": return self.CREATING_CUSTOMER_WEIGHT
        if t == "creating_invoice": return self.CREATING_DOCUMENTS_WEIGHT
        return 10.0

    async def update_weighted(
            self,
            title: str,
            fraction: float,
            progress_map: Dict[str, float],
            *,
            stage: Optional[str] = None,
            threshold: float = 0.1,
    ) -> float:
        """
        Convenience: update a weighted key inside `progress_map` and emit overall.

        `title` should be one of get_file_progress_dict() keys
        (e.g., 'creating_item', 'creating_customer', 'creating_invoice').

        `fraction` is 0..1 of that phase's weight.
        """
        key = (title or "").strip()
        weight = float(self.getWeight(key)) if hasattr(self, "getWeight") else 10.0
        value = max(0.0, min(1.0, float(fraction))) * weight
        progress_map[key] = value
        return await self.safe_emit_progress(progress_map, stage=stage, threshold=threshold)

class XeroIntegrationsProgressLog(ProgressLog):
    CREATING_ITEM_WEIGHT = 20.0
    CREATING_CONTACT_WEIGHT = 30.0
    CREATING_DOCUMENTS_WEIGHT = 50.0

    @staticmethod
    def get_file_progress_dict() -> Dict[str, float]:
        return {"creating_items": 0.0, "creating_contacts": 0.0, "creating_invoices": 0.0}

    def getWeight(self, title: str) -> float:
        t = (title or "").lower()
        if t == "creating_items": return self.CREATING_ITEM_WEIGHT
        if t == "creating_contacts": return self.CREATING_CONTACT_WEIGHT
        if t == "creating_invoices": return self.CREATING_DOCUMENTS_WEIGHT
        return 10.0


def get_integrations_progress_logger_factory(default_log_key: str = "quickbooks-invoice-progress"):
    async def get_integrations_progress_logger(request: Request) -> IntegrationsProgressLog:
        client_id = request.session.get("client_id", "Guest")
        if not client_id:
            logger.error("Client ID not found in session")
            raise HTTPException(status_code=400, detail="Client ID not found in session.")
        return IntegrationsProgressLog(client_id=client_id, log_key=default_log_key)
    return get_integrations_progress_logger
