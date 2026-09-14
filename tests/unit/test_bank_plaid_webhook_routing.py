import hashlib
from pathlib import Path

from masyg_extractor.integrations.bank.webhook_repository import (
    build_webhook_event_record,
    item_owner_doc_id,
    webhook_delivery_id,
)


ROOT = Path(__file__).resolve().parents[2]


def test_item_owner_key_is_deterministic_and_case_sensitive():
    assert item_owner_doc_id("item-ABC") == hashlib.sha256(
        b"item-ABC"
    ).hexdigest()
    assert item_owner_doc_id("item-ABC") != item_owner_doc_id("item-abc")


def test_exact_duplicate_delivery_has_stable_id():
    raw = b'{"webhook_type":"TRANSACTIONS","item_id":"item-1"}'
    token = "signed-jwt"

    first = webhook_delivery_id(raw, token)
    second = webhook_delivery_id(raw, token)

    assert first == second
    assert first != webhook_delivery_id(raw + b" ", token)
    assert first != webhook_delivery_id(raw, token + "-different")


def test_durable_event_record_is_minimal_and_does_not_store_raw_body_or_jwt():
    raw = b'{"webhook_type":"ITEM","webhook_code":"ERROR"}'

    record = build_webhook_event_record(
        payload={
            "webhook_type": "ITEM",
            "webhook_code": "ERROR",
            "item_id": "item-1",
            "environment": "sandbox",
            "error": {"error_code": "ITEM_LOGIN_REQUIRED"},
        },
        claims={"iat": 123},
        raw_body=raw,
        user_id="user-1",
    )

    assert record["userId"] == "user-1"
    assert record["routingStatus"] == "routed"
    assert record["processingStatus"] == "pending"
    assert record["errorCode"] == "ITEM_LOGIN_REQUIRED"
    assert record["rawBodySha256"] == hashlib.sha256(raw).hexdigest()
    assert "rawBody" not in record
    assert "signedJwt" not in record


def test_bank_item_storage_owns_reverse_index_lifecycle():
    repository = (
        ROOT / "masyg_extractor/integrations/bank/repository.py"
    ).read_text()

    save_start = repository.index("    def save_item(")
    save_end = repository.index("    def list_items(", save_start)
    save_block = repository[save_start:save_end]

    delete_start = repository.index("    def delete_item(")
    delete_end = repository.index("    @staticmethod", delete_start)
    delete_block = repository[delete_start:delete_end]

    assert "register_item_owner(" in save_block
    assert "unregister_item_owner(" in delete_block


def test_webhook_route_durably_records_before_ack_and_does_not_process_inline():
    router = (
        ROOT / "masyg_extractor/integrations/bank/router.py"
    ).read_text()

    start = router.index('@router.post("/webhook")')
    end = router.index('@router.post("/link-token")', start)
    block = router[start:end]

    assert "BankWebhookRepository()" in block
    assert "repository.record_verified_event" in block
    assert '"duplicate": not delivery["created"]' in block
    assert '"routed": bool(delivery["user_id"])' in block

    assert "sync_transactions(" not in block
    assert "refresh_item_status(" not in block
    assert "BackgroundTasks" not in block
