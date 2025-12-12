from __future__ import annotations
import hashlib, json, datetime, functools
from typing import Any, Dict, Optional, Callable
from firebase_admin import firestore

ISO = lambda: datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

def _hash_payload(payload: Any) -> str:
    try:
        return hashlib.sha1(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:16]
    except Exception:
        return ""

class AuditLogService:
    def __init__(self, user_id: str, integration: str = "quickbooks"):
        self.user_id = user_id
        self.integration = integration
        self.db = firestore.client()

    def _events(self):
        return (self.db.collection("users").document(self.user_id)
                .collection("integrations").document(self.integration)
                .collection("audit_events"))

    def _tx_ref(self, group_id: str, transaction_id: str):
        return (self.db.collection("users").document(self.user_id)
                .collection("integrations").document(self.integration)
                .collection("transactions").document(group_id)
                .collection("tx").document(transaction_id))

    def start(self, *, event_id: str, doc_type: str, entity_type: str, operation: str,
              transaction_id: Optional[str], group_id: Optional[str],
              idempotency_key: Optional[str], payload: Optional[dict] = None, attempt: int = 1) -> None:
        data = {
            "eventId": event_id,
            "txnRef": {"groupId": group_id, "transactionId": transaction_id, "docType": doc_type},
            "entityType": entity_type,
            "operation": operation,
            "status": "PENDING",
            "errorCategory": None,
            "errorMessage": None,
            "retryable": False,
            "attempt": attempt,
            "idempotencyKey": idempotency_key,
            "payloadHash": _hash_payload(payload),
            "createdAt": ISO(),
            "updatedAt": ISO(),
        }
        self._events().document(event_id).set(data)

    def ok(self, *, event_id: str, group_id: Optional[str], transaction_id: Optional[str]) -> None:
        self._events().document(event_id).set({"status": "SUCCESS", "updatedAt": ISO()}, merge=True)
        if group_id and transaction_id:
            self._tx_ref(group_id, transaction_id).set({
                "lastStatus": "SUCCESS",
                "lastError": None,
                "lastAuditEventId": event_id,
                "updatedAt": ISO()
            }, merge=True)

    def fail(self, *, event_id: str, group_id: Optional[str], transaction_id: Optional[str],
             error_category: str, error_message: str, error_details: Optional[dict],
             retryable: bool) -> None:
        self._events().document(event_id).set({
            "status": "FAILURE",
            "errorCategory": error_category,
            "errorMessage": (error_message or "")[:400],
            "retryable": retryable,
            "updatedAt": ISO()
        }, merge=True)
        if group_id and transaction_id:
            self._tx_ref(group_id, transaction_id).set({
                "lastStatus": "FAILURE",
                "lastError": (error_message or "")[:200],
                "lastAuditEventId": event_id,
                "updatedAt": ISO()
            }, merge=True)

def audit_op(doc_type: str, entity_type: str, operation: str):
    def deco(fn: Callable):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            context = None
            for a in args:
                if hasattr(a, "context"):
                    context = getattr(a, "context")
                    break
            context = context or kwargs.get("context")
            user_id = getattr(context, "user_id", None)
            if not user_id:
                return await fn(*args, **kwargs)

            audit = AuditLogService(user_id=user_id, integration="quickbooks")
            txid = kwargs.get("transaction_id") or getattr(kwargs.get("document", None), "transaction_id", None)
            gid  = kwargs.get("group_id") or getattr(kwargs.get("document", None), "group_id", None)
            idem = kwargs.get("idempotency_key") or kwargs.get("bId") or kwargs.get("bid")
            payload = kwargs.get("payload")

            event_id = f"{doc_type}:{entity_type}:{txid or '-'}:{idem or '-'}"
            audit.start(event_id=event_id, doc_type=doc_type, entity_type=entity_type, operation=operation,
                        transaction_id=txid, group_id=gid, idempotency_key=idem, payload=payload,
                        attempt=kwargs.get("attempt", 1))
            try:
                res = await fn(*args, **kwargs)
                if isinstance(res, dict) and ("error" in res or "Fault" in res):
                    msg = res.get("error") or res.get("Fault") or "Unknown error"
                    audit.fail(event_id=event_id, group_id=gid, transaction_id=txid,
                               error_category=_categorize_error(res),
                               error_message=str(msg),
                               error_details=None,
                               retryable=_is_retryable(res))
                else:
                    audit.ok(event_id=event_id, group_id=gid, transaction_id=txid)
                return res
            except Exception as e:
                audit.fail(event_id=event_id, group_id=gid, transaction_id=txid,
                           error_category="Unknown",
                           error_message=str(e),
                           error_details=None,
                           retryable=True)
                raise
        return wrapper
    return deco

def _categorize_error(res: dict) -> str:
    s = json.dumps(res).lower()
    if "duplicate" in s: return "Duplicate"
    if "validation" in s or "invalid" in s: return "Validation"
    if "token" in s or "auth" in s or "401" in s: return "Auth"
    if "429" in s or "rate" in s or "limit" in s: return "Limit"
    if "timeout" in s or "network" in s or "transport" in s: return "Transport"
    return "Unknown"

def _is_retryable(res: dict) -> bool:
    s = json.dumps(res).lower()
    return any(k in s for k in ["timeout", "network", "transport", "429", "limit"])
