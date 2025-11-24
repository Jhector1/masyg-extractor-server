import asyncio
from collections import defaultdict
from typing import Optional, Dict, Any, List, Union

from masyg_extractor.integration_qb_v5.core.integration_context import IntegrationContext
from masyg_extractor.integration_qb_v5.intergrate.baseAdapter import IntegrationClientAdapter
from masyg_extractor.integration_qb_v5.repository.firestore_repository import QuickBooksFirestoreService
from masyg_extractor.integration_qb_v5.domain.models import Item, Entity
from masyg_extractor.services.my_log import logger
from masyg_extractor.services.progress_log import IntegrationsProgressLog


class EntityHelper:
    """
    QuickBooks-focused helper:
      - Uses QBO SQL via /query for reads/existence checks
      - Uses singular endpoints (adapter lowercases) for creates
      - batch: POST /batch; returns BatchItemResponse
    """

    def __init__(
        self,
        context: IntegrationContext,
        repo: QuickBooksFirestoreService,
        client: IntegrationClientAdapter,
    ):
        self.context = context
        self.repo = repo
        self.client = client

    # ---------------------------
    # Progress utility
    # ---------------------------
    async def _simulate_progress(self, task: str, steps: int = 5) -> None:
        for step in range(steps):
            await asyncio.sleep(0.3)
            key = f"creating_{task.lower()}"
            self.context.progress[key] = ((step + 1) / steps) * IntegrationsProgressLog.CREATING_CUSTOMER_WEIGHT
            overall = self.context.progress_logger.calculate_overall_progress(self.context.progress)
            await self.context.progress_logger.safe_emit_progress(overall)

    # ---------------------------
    # Fetch helpers (QBO SQL)
    # ---------------------------
    @staticmethod
    def _qbo_name_field(entity: str, field: str) -> str:
        """
        QBO field normalization: Customers use DisplayName instead of Name.
        """
        if entity.lower() == "customer" and field.lower() == "name":
            return "DisplayName"
        return field

    async def fetch_all_entities(
        self,
        endpoint: str,
        name_field: str = "Name",
        id_field: str = "Id",
        extra_fields: Optional[List[str]] = None,
        limit: int = 1000,
    ) -> List[Dict[str, Any]]:
        """
        Fetch all entities of a type using QBO SQL.
        For Items, include Sku automatically.
        """
        # Normalize fields
        nf = self._qbo_name_field(endpoint, name_field)
        fields = {nf, id_field}
        if endpoint == "Item":
            fields.add("Sku")
        if extra_fields:
            fields.update(extra_fields)

        field_list = ", ".join(sorted(fields))
        query = f"SELECT {field_list} FROM {endpoint} STARTPOSITION 1 MAXRESULTS {int(limit)}"

        resp = await self.client.request(
            self.repo.get_integration_token(),
            "query",
            method="GET",
            params={"query": query},
        )
        rows = resp.get("QueryResponse", {}).get(endpoint, []) or []
        return rows

    async def check_entity_exists(
        self, entity: str, identifier_field: str, identifier_value: str
    ) -> bool:
        """
        QBO existence check via SQL: SELECT Id FROM Entity WHERE Field = 'value' LIMIT 1
        """
        fld = self._qbo_name_field(entity, identifier_field)
        # Basic quote escaping for SQL literal
        val = identifier_value.replace("'", "''")
        query = f"SELECT Id FROM {entity} WHERE {fld} = '{val}' STARTPOSITION 1 MAXRESULTS 1"

        resp = await self.client.request(
            self.repo.get_integration_token(),
            "query",
            method="GET",
            params={"query": query},
        )
        exists = bool(resp.get("QueryResponse", {}).get(entity, []))
        logger.info(f"{entity} exists check {fld}='{identifier_value}': {exists}")
        return exists

    async def fetch_entity_id_by_name(self, entity: str, name: str) -> Optional[str]:
        """
        QBO: SELECT Id FROM Entity WHERE Name/DisplayName = '...'
        """
        fld = self._qbo_name_field(entity, "Name")
        val = name.replace("'", "''")
        query = f"SELECT Id FROM {entity} WHERE {fld} = '{val}' STARTPOSITION 1 MAXRESULTS 1"
        try:
            resp = await self.client.request(
                self.repo.get_integration_token(),
                "query",
                method="GET",
                params={"query": query},
            )
            rows = resp.get("QueryResponse", {}).get(entity, []) or []
            if rows:
                ent_id = rows[0].get("Id")
                logger.info(f"Fetched {entity} Id by {fld}='{name}': {ent_id}")
                return ent_id
        except Exception as e:
            logger.error(f"Error fetching {entity} ID by name: {e}")
        return None

    # ---------------------------
    # Create (single/bulk)
    # ---------------------------
    async def create_entity(self, entity: str, payload: Optional[Dict[str, Any]] = None) -> str:
        """
        Create a single entity using singular endpoint.
        QBO returns top-level singular key: { "Customer": {...} } or { "Item": {...} }
        """
        await self._simulate_progress(entity)

        resp = await self.client.request(
            self.repo.get_integration_token(),
            entity,                 # adapter will lowercase
            payload=payload,
            method="POST",
        )
        logger.info(f"create_{entity.lower()} response: {resp}")

        if not isinstance(resp, dict):
            raise Exception(f"Unexpected response structure: {resp}")

        obj = resp.get(entity)  # singular
        if not obj:
            # Some APIs nest further (rare); keep explicit error to surface debugging
            raise Exception(f"Missing '{entity}' in response: {resp}")

        new_id = obj.get("Id") or obj.get(f"{entity}ID")
        if not new_id:
            raise Exception(f"No Id in '{entity}' create response: {resp}")

        return str(new_id)

    async def create_entity_in_bulk(
        self,
        entity: str,
        payload: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """
        POST /batch — return raw BatchItemResponse list (each entry may include 'bId' and either '{Entity}': {...} or 'Fault').
        """
        await self._simulate_progress(entity)

        resp = await self.client.request(
            self.repo.get_integration_token(),
            "batch",
            payload=payload,
            method="POST",
        )

        logger.info(f"bulk_create {entity} response: {resp}")

        # QBO: { "BatchItemResponse": [ { "bId": "...", "Item": {...} } , ... ] }
        batch = resp.get("BatchItemResponse", [])
        if isinstance(batch, list):
            return batch
        elif isinstance(batch, dict):
            return [batch]
        else:
            raise Exception(f"Unexpected batch response: {resp}")

    # ---------------------------
    # Merge helpers
    # ---------------------------
    async def get_non_existing_entities(
        self,
        local_entities: List[Entity],
        endpoint: str,
        name_field: str,
        id_field: str,
    ) -> List[Entity]:
        """
        Compare local vs integration; return locals that don't exist.
        - Items: prefer Sku match when available
        - Others: Name/Id
        """
        integration_entities = await self.fetch_all_entities(endpoint, name_field, id_field)
        existing_ids = {e.get("Id") for e in integration_entities if e.get("Id")}
        # For customers, integration uses DisplayName
        name_key = self._qbo_name_field(endpoint, name_field)
        existing_names = {e.get(name_key) for e in integration_entities if e.get(name_key)}
        existing_skus = {e.get("Sku") for e in integration_entities if e.get("Sku")}

        non_existing: List[Entity] = []
        for ent in local_entities:
            if endpoint == "Item" and isinstance(ent, Item):
                sku = getattr(ent, "sku", None)
                if sku:
                    if sku not in existing_skus:
                        non_existing.append(ent)
                else:
                    if (getattr(ent, "id", None) not in existing_ids) and (ent.name not in existing_names):
                        non_existing.append(ent)
                continue

            ent_id = getattr(ent, "id", None)
            if ent_id not in existing_ids and ent.name not in existing_names:
                non_existing.append(ent)

        return non_existing

    async def create_entity_in_bulk_and_merge_with_current(
        self,
        current_entities: Dict[str, Union[Any, List[Any]]],
        entity: str,
        payload: Optional[Dict[str, Any]] = None,
        name_key: str = "Name",
        id_key: str = "Id",
        tracker_key: str = "bId",
    ) -> Dict[str, Any]:
        """
        If payload present: POST /batch, then merge created IDs back into current_entities.
        Matching priority:
          1) bId = "<tracker>_<sku>" → match tracker + sku to local objects
          2) fallback by Name (when unique)
        Returns:
          { "merged": <current_entities after id backfill>, "batch": <BatchItemResponse list> }
        """
        # If no payload, just backfill from integration (no creation)
        if not payload or not payload.get("BatchItemRequest"):
            integration = await self.fetch_all_entities(entity, name_key, id_key)
            name_field = self._qbo_name_field(entity, name_key)
            name_to_id = {e.get(name_field): e.get(id_key) for e in integration if e.get(name_field) and e.get(id_key)}

            for tracker, items in current_entities.items():
                objs = items if isinstance(items, list) else [items]
                for obj in objs:
                    if getattr(obj, "id", None) is None:
                        obj.id = name_to_id.get(getattr(obj, "name", None))
            return {"merged": current_entities, "batch": []}

        # 1) create via batch
        created_entries = await self.create_entity_in_bulk(entity, payload)

        # 2) normalize tracker → [objects]
        tracker_map: Dict[str, List[Any]] = defaultdict(list)
        for tracker, val in current_entities.items():
            tracker_map[tracker].extend(val if isinstance(val, list) else [val])

        # 3) merge new IDs using bId parsing
        for entry in created_entries:
            b_id = entry.get(tracker_key)
            if not b_id:
                continue

            # bId format assumed: "<tracker>_<sku>"
            parts = b_id.split("_", 1)
            tracker = parts[0]
            sku_suffix = parts[1] if len(parts) > 1 else None

            entity_obj = entry.get(entity) or {}      # e.g., entry["Item"] or entry["Customer"]
            ent_id = entity_obj.get("Id")
            ent_name = entity_obj.get(self._qbo_name_field(entity, name_key))
            ent_sku = entity_obj.get("Sku")

            for local in tracker_map.get(tracker, []):
                # Prefer exact SKU match
                local_sku = getattr(local, "sku", None)
                if sku_suffix and local_sku and local_sku == sku_suffix:
                    if not getattr(local, "id", None):
                        local.id = ent_id
                        # keep local name if already set; otherwise fill from API
                        if not getattr(local, "name", None) and ent_name:
                            local.name = ent_name
                    break

                # Fallback by Name
                if ent_name and getattr(local, "name", None) == ent_name and not getattr(local, "id", None):
                    local.id = ent_id
                    if hasattr(local, "sku") and not getattr(local, "sku", None):
                        setattr(local, "sku", ent_sku)
                    break

        return {"merged": current_entities, "batch": created_entries}

    # ---------------------------
    # Simple list for routers (no JSONResponse here)
    # ---------------------------
    async def get_all_entities(
        self,
        endpoint: str,
        name_field: str = "Name",
        id_field: str = "Id",
    ) -> List[Dict[str, Any]]:
        """
        Return [{Name, Id, (Sku?)}] for routers to wrap in JSONResponse.
        """
        rows = await self.fetch_all_entities(endpoint, name_field, id_field)
        name_key = self._qbo_name_field(endpoint, name_field)
        out: List[Dict[str, Any]] = []
        for e in rows:
            row = {"Name": e.get(name_key), "Id": e.get(id_field)}
            if endpoint == "Item" and "Sku" in e:
                row["Sku"] = e.get("Sku")
            out.append(row)
        return out
