import asyncio
from itertools import chain
from typing import Dict, List, Optional, cast

from fastapi import Request

from masyg_extractor.integration_qb_v5.core.integration_context import IntegrationContext
from masyg_extractor.integration_qb_v5.domain.models import Item, Customer
from masyg_extractor.integration_qb_v5.entity_helper import EntityHelper
from masyg_extractor.integration_qb_v5.intergrate.baseAdapter import IntegrationClientAdapter
from masyg_extractor.integration_qb_v5.intergrate.quickbooks.services.audit_log_service import AuditLogService, audit_op
from masyg_extractor.integration_qb_v5.repository.firestore_repository import QuickBooksFirestoreService
from masyg_extractor.integration_qb_v5.utils import safe_uuid_key
from masyg_extractor.integrations.xero.services.item_services import generate_sku
from masyg_extractor.services.file_extractor_service import remove_non_alphanumeric
from masyg_extractor.services.my_log import logger


# -------------------------------
# ItemService with audit
# -------------------------------
class ItemService:
    def __init__(
        self,
        context: IntegrationContext,
        repo: QuickBooksFirestoreService,
        client: IntegrationClientAdapter,
    ):
        self.context = context
        self.repo = repo
        self.client = client
        self.entity_helper = EntityHelper(context, repo, client)
        self.audit = AuditLogService(context.user_id, integration="quickbooks")

    @staticmethod
    def get_sanitized_name(name: Optional[str], max_length: int = 50) -> str:
        """
        Sanitize and truncate the item name.
        Returns "Unnamed Item" if the result is empty.
        """
        try:
            raw_name = (name or "Unnamed Item")[:max_length]
            sanitized = remove_non_alphanumeric(raw_name)
            return sanitized or "Unnamed Item"
        except Exception as e:
            logger.error(f"Error sanitizing name '{name}': {str(e)}")
            return "Unnamed Item"

    @staticmethod
    def create_single_item_payload(item: Item) -> dict:
        """
        Generate the payload for creating a single item.
        Uses a cleaned name and either the given SKU or one generated from the name.
        """
        try:
            name = ItemService.get_sanitized_name(item.name)
            sku = item.sku if item.sku else generate_sku(name)
            return {
                "Code": sku,
                "Name": name,
                "Description": item.description,
            }
        except Exception as e:
            logger.error(f"Error creating single item payload for item '{item.name}': {str(e)}")
            return {}

    async def create_bulk_item_payload(self, item: Item) -> dict:
        """
        Generate payload for creating an item in bulk.
        - Uses sanitized Name + Sku to keep names unique in QBO.
        - Includes Sku in payload.
        - Encodes sku into bId so we can deterministically merge created items.
        - Ensures Income/Expense accounts exist (falls back to defaults).
        """
        try:
            name = ItemService.get_sanitized_name(item.name)
            if not item.sku:
                item.sku = generate_sku(name)

            tracker_prefix = safe_uuid_key(item.transaction_id)
            sku = item.sku

            income_account_id = getattr(item.income_account, "id", None) if item.income_account else None
            expense_account_id = getattr(item.expense_account, "id", None) if item.expense_account else None

            if not income_account_id or not expense_account_id:
                try:
                    default_income, default_expense = await self.get_default_service_accounts()
                    income_account_id = income_account_id or default_income
                    expense_account_id = expense_account_id or default_expense
                except Exception as e:
                    logger.error(f"Could not fetch default accounts: {e}")
                    raise

            payload = {
                "TrackQtyOnHand": (item.type == "Inventory"),
                "QtyOnHand": int(item.QtyOnHand) if item.QtyOnHand is not None else 1,
                "Name": f"{name} ({sku})",       # Keep Name unique (include sku)
                "Sku": sku,
                "Type": item.type if item.type else "Service",
                "Active": True,
                "IncomeAccountRef": {"value": str(income_account_id)},
                "ExpenseAccountRef": {"value": str(expense_account_id)},
            }

            if item.type == "Inventory":
                from datetime import date
                payload.update({
                    "AssetAccountRef": {"value": "81"},  # adjust to your environment if needed
                    "InvStartDate": date.today().strftime("%Y-%m-%d"),
                    "PurchaseCost": float(item.unit_price or 0),
                    "ExpenseAccountRef": {"value": "80"},
                    "IncomeAccountRef": {"value": "79"},
                })

            return {
                "bId": f"{tracker_prefix}_{sku}",
                "Item": payload,
                "operation": "create",
            }

        except Exception as e:
            logger.error(f"Error creating bulk item payload for item '{item.name}': {str(e)}")
            return {}

    async def check_item_exists(self, item: Item) -> bool:
        """
        Check if an item exists based on its ID or Name.
        """
        try:
            if item.id:
                return await self.entity_helper.check_entity_exists("Item", "Id", item.id)
            if item.name:
                return await self.entity_helper.check_entity_exists("Item", "Name", item.name)
            return False
        except Exception as e:
            logger.error(f"Error checking existence for item '{item.name}': {str(e)}")
            return False

    async def get_default_service_accounts(self) -> tuple[str, str]:
        """
        AuditLogService
        Retrieve default income and expense accounts from QuickBooks.
        Uses primary criteria for account selection with fallback to Uncategorized types.
        """
        query = "SELECT * FROM Account WHERE Active = true"
        params = {"query": query}

        try:
            response = await self.client.request(
                self.repo.get_integration_token(),
                "query",
                method="GET",
                params=params,
            )

            accounts = response.get("QueryResponse", {}).get("Account")
            if not accounts:
                raise Exception("No accounts returned from QuickBooks")

            income_account_id = None
            expense_account_id = None

            # First pass: preferred criteria.
            for acc in accounts:
                if (
                    not income_account_id
                    and acc.get("AccountType") == "Income"
                    and acc.get("AccountSubType") in ["SalesOfProductIncome", "ServiceFeeIncome"]
                ):
                    income_account_id = acc["Id"]
                if not expense_account_id and acc.get("AccountType") == "Cost of Goods Sold":
                    expense_account_id = acc["Id"]
                if income_account_id and expense_account_id:
                    break

            # Second pass: fall back to Uncategorized accounts.
            if not income_account_id:
                for acc in accounts:
                    if acc.get("AccountType") == "Income" and acc.get("AccountSubType") == "UncategorizedIncome":
                        income_account_id = acc["Id"]
                        break

            if not expense_account_id:
                for acc in accounts:
                    if (
                        acc.get("AccountType") == "Cost of Goods Sold"
                        and acc.get("AccountSubType") == "UncategorizedExpense"
                    ):
                        expense_account_id = acc["Id"]
                        break

            if not income_account_id or not expense_account_id:
                raise Exception("Could not find suitable Income/Expense accounts in QuickBooks.")

            return income_account_id, expense_account_id

        except Exception as e:
            logger.error(f"Error fetching default service accounts: {e}")
            return "", ""

    @audit_op(doc_type="-", entity_type="Item", operation="create")
    async def create_item(self, item: Item) -> str:
        """
        Create a single item in the target system.
        """
        try:
            payload = {"Items": [self.create_single_item_payload(item)]}
            logger.info(f"Creating item with payload: {payload}")
            return await self.entity_helper.create_entity("Item", payload)
        except Exception as e:
            logger.error(f"Error creating item '{item.name}': {str(e)}")
            return ""

    async def create_item_in_bulk(self, local_items: Dict[str, List[Item]]) -> Dict[str, List[Item]]:
        """
        Create items in bulk in the target system.
db.collection("users").collection(user_id).collection("audit_logs")
        1. Flatten input map.
        2. Filter out existing items.
        3. Build BatchItemRequest with bId per entry.
        4. Start PENDING audit events per bId.
        5. Send + mark ok/fail per bId (if batch returned); fallback: mark all as ok.
        """
        try:
            name_key = "Name"
            id_key = "Id"
            entity = "Item"

            # Flatten the list of items from dictionary values.
            flat_items: List[Item] = [item for sublist in local_items.values() for item in sublist]

            # Filter out existing items.
            non_existing_items = cast(
                List[Item],
                await self.entity_helper.get_non_existing_entities(flat_items, entity, name_key, id_key),
            )

            # Prepare payload for each new item.
            payload_items = [await self.create_bulk_item_payload(item) for item in non_existing_items]
            payload_items = [p for p in payload_items if p]  # guard against {}
            payload = {"BatchItemRequest": payload_items}

            # Start per-bId PENDING audit
            intended_bids = [p.get("bId") for p in payload_items if p.get("bId")]
            for bid in intended_bids:
                self.audit.start(
                    event_id=f"-:Item:{bid}:{bid}",
                    doc_type="-",
                    entity_type="Item",
                    operation="bulk_create",
                    transaction_id=None,
                    group_id=None,
                    idempotency_key=bid,
                    payload=None,
                )

            logger.info(f"Creating bulk items with payload: {payload}")
            result = await self.entity_helper.create_entity_in_bulk_and_merge_with_current(
                local_items, entity, payload, name_key, id_key
            )

            # Support both return shapes:
            batch_resp = None
            merged_items: Dict[str, List[Item]]
            if isinstance(result, dict) and "merged" in result and "batch" in result:
                merged_items = cast(Dict[str, List[Item]], result["merged"])
                batch_resp = result["batch"]  # expected list of entries with {'bId', ...}
            else:
                merged_items = cast(Dict[str, List[Item]], result)

            # If we have raw BatchItemResponse, mark per bId precisely
            if isinstance(batch_resp, list):
                for entry in batch_resp:
                    bid = entry.get("bId")
                    if not bid:
                        continue
                    if "Fault" in entry:
                        self.audit.fail(
                            event_id=f"-:Item:{bid}:{bid}",
                            group_id=None,
                            transaction_id=None,
                            error_category="Validation",
                            error_message="QuickBooks Fault",
                            error_details=None,
                            retryable=True,
                        )
                    else:
                        self.audit.ok(
                            event_id=f"-:Item:{bid}:{bid}",
                            group_id=None,
                            transaction_id=None,
                        )
            else:
                # Fallback heuristic: mark all intended bIds as success.
                # (Improve by making EntityHelper return batch entries to get accurate failures.)
                for bid in intended_bids:
                    self.audit.ok(
                        event_id=f"-:Item:{bid}:{bid}",
                        group_id=None,
                        transaction_id=None,
                    )

            return merged_items
        except Exception as e:
            logger.error(f"Error creating items in bulk: {str(e)}")
            # In a hard failure, any PENDING events will remain; caller can retry or you can add a batch fail here.
            return {}


