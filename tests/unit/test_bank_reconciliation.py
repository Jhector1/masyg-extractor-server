from masyg_extractor.integrations.bank.reconciliation import (
    BankReconciliationService,
    document_amount,
    document_direction,
    is_provider_transfer,
    normalize_party_name,
    score_document_candidate,
    transaction_direction,
)


def transaction(**overrides):
    value = {
        "amount": 12.00,
        "merchant_name": "McDonald's",
        "name": "MCDONALDS 123",
        "date": "2026-09-08",
        "iso_currency_code": "USD",
        "category": "FOOD_AND_DRINK",
        "category_detail": "FOOD_AND_DRINK_FAST_FOOD",
    }
    value.update(overrides)
    return value


def document(**overrides):
    value = {
        "group_id": "group-1",
        "file_id": "receipt.pdf",
        "vendor_name": "McDonalds",
        "date": "2026-09-08",
        "document_type": "purchase_receipt",
        "amount": 12.00,
        "currency": "USD",
        "direction": "out",
    }
    value.update(overrides)
    return value


def test_party_normalization_is_case_and_punctuation_insensitive():
    assert normalize_party_name("McDonald's") == "mcdonalds"
    assert normalize_party_name("McDonald’s") == "mcdonalds"
    assert normalize_party_name("McDonalds") == "mcdonalds"
    assert normalize_party_name("  ACME, INC. ") == "acme inc"


def test_transaction_direction_uses_plaid_amount_semantics():
    assert transaction_direction({"amount": 10}) == "out"
    assert transaction_direction({"amount": -10}) == "in"
    assert transaction_direction({"amount": 0}) is None


def test_accounting_intent_is_reused_for_document_direction():
    assert document_direction("purchase_receipt") == "out"
    assert document_direction("vendor_bill") == "out"
    assert document_direction("sales_invoice") == "in"
    assert document_direction("sales_receipt") == "in"
    assert document_direction("bank_statement") is None


def test_document_amount_reuses_line_item_math_before_legacy_total():
    value = document_amount(
        {
            "line_items": [
                {"quantity": 2, "unit_price": "4.25"},
                {"quantity": 1, "unit_price": "3.50"},
            ],
            "total": 999,
        }
    )
    assert value == 12.00


def test_document_amount_falls_back_to_top_level_total():
    assert document_amount({"line_items": [], "total_amount": "41.27"}) == 41.27


def test_exact_amount_party_and_date_is_a_full_confidence_candidate():
    result = score_document_candidate(transaction(), document())

    assert result is not None
    assert result["score"] == 100
    assert result["confidence"] == 1.0
    assert result["evidence"] == [
        "amount_exact",
        "party_exact",
        "date_exact",
        "date_within_3_days",
    ]


def test_nearby_date_keeps_candidate_but_with_lower_confidence():
    result = score_document_candidate(
        transaction(date="2026-09-10"),
        document(date="2026-09-08"),
    )

    assert result is not None
    assert result["score"] == 80
    assert result["confidence"] == 0.8


def test_amount_and_exact_date_can_suggest_when_party_is_unavailable():
    result = score_document_candidate(
        transaction(merchant_name=None, name=None),
        document(vendor_name=None),
    )

    assert result is not None
    assert result["score"] == 70


def test_amount_mismatch_is_not_a_candidate():
    assert (
        score_document_candidate(
            transaction(amount=12),
            document(amount=13),
        )
        is None
    )


def test_currency_mismatch_is_not_a_candidate():
    assert (
        score_document_candidate(
            transaction(iso_currency_code="USD"),
            document(currency="EUR"),
        )
        is None
    )


def test_accounting_direction_mismatch_is_not_a_candidate():
    assert (
        score_document_candidate(
            transaction(amount=12),
            document(direction="in"),
        )
        is None
    )


def test_provider_transfer_primary_category_is_detected():
    assert is_provider_transfer(
        transaction(
            category="TRANSFER_OUT",
            category_detail="TRANSFER_OUT_ACCOUNT_TRANSFER",
        )
    )


def test_non_transfer_is_not_classified_as_transfer():
    assert not is_provider_transfer(transaction())


class _FakeSnapshot:
    def __init__(self, data=None, exists=True):
        self._data = data or {}
        self.exists = exists

    def to_dict(self):
        return dict(self._data)


class _FakeTransactionRef:
    def __init__(self, data):
        self.data = dict(data)

    def get(self):
        return _FakeSnapshot(self.data)

    def update(self, payload):
        self.data.update(payload)

    def set(self, payload, merge=False):
        if merge:
            self.data.update(payload)
        else:
            self.data = dict(payload)


def test_provider_merge_preserves_existing_reconciliation_map():
    stored = {
        "transaction_id": "tx-1",
        "amount": 12.00,
        "merchant_name": "McDonalds",
        "reconciliation": {
            "status": "matched",
            "group_id": "group-1",
            "file_id": "receipt.pdf",
        },
    }

    provider_refresh = {
        "transaction_id": "tx-1",
        "amount": 12.00,
        "merchant_name": "McDonald's",
        "category": "FOOD_AND_DRINK",
    }

    ref = _FakeTransactionRef(stored)
    ref.set(provider_refresh, merge=True)

    assert ref.data["merchant_name"] == "McDonald's"
    assert ref.data["category"] == "FOOD_AND_DRINK"
    assert ref.data["reconciliation"] == {
        "status": "matched",
        "group_id": "group-1",
        "file_id": "receipt.pdf",
    }


def test_replacing_reconciliation_map_clears_stale_document_identity():
    ref = _FakeTransactionRef(
        {
            "transaction_id": "tx-1",
            "reconciliation": {
                "status": "matched",
                "group_id": "group-1",
                "file_id": "receipt.pdf",
                "confidence": 1.0,
            },
        }
    )

    ref.update(
        {
            "reconciliation": {
                "status": "unreviewed",
                "source": "manual",
                "confidence": None,
            },
        }
    )

    state = ref.data["reconciliation"]

    assert state["status"] == "unreviewed"
    assert "group_id" not in state
    assert "file_id" not in state


def test_matching_vendor_and_amount_do_not_bridge_distant_dates():
    assert (
        score_document_candidate(
            transaction(date="2026-08-09"),
            document(date="2026-09-08"),
        )
        is None
    )


def test_claimed_document_is_not_suggested_to_another_transaction():
    candidates = {
        ("out", 1200): [
            document(
                group_id="group-1",
                file_id="receipt.pdf",
            )
        ]
    }

    result = BankReconciliationService._candidate_documents(
        transaction(),
        candidates,
        excluded_document_ids={
            ("group-1", "receipt.pdf"),
        },
    )

    assert result == []
