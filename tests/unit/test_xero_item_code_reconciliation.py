import asyncio
import sys
import types

from masyg_extractor.integrations.accounting.core.models import Account, Item


_firestore_module_name = (
    "masyg_extractor.integrations.accounting.shared.firestore_repository"
)
_firestore_stub = types.ModuleType(_firestore_module_name)


class _QuickBooksFirestoreService:
    pass


_firestore_stub.QuickBooksFirestoreService = _QuickBooksFirestoreService
sys.modules[_firestore_module_name] = _firestore_stub

try:
    from masyg_extractor.integrations.accounting.xero.entity_helper import EntityHelper
finally:
    sys.modules.pop(_firestore_module_name, None)


def make_item(*, item_id: str | None, name: str, tx: str, sku: str) -> Item:
    return Item(
        id=item_id,
        name=name,
        transaction_id=tx,
        quantity=1,
        unit_price=1.0,
        description="demo",
        income_account=Account(id=None, name=None, transaction_id=tx),
        expense_account=Account(id=None, name=None, transaction_id=tx),
        sku=sku,
        QtyOnHand=1,
        type=None,
        tax_code="NON",
    )


def test_item_tracker_uses_safe_uuid_prefix_before_sku():
    helper = EntityHelper(context=None, repo=None, client=None)
    provider_code = "117b0811f2c14319f8d4_083662367"

    async def fake_create_entity_in_bulk(entity, payload):
        assert entity == "Items"
        return [{"Name": "0834M32", "Code": provider_code}]

    helper.create_entity_in_bulk = fake_create_entity_in_bulk

    current = {
        "117b0811f2c14319f8d4": [
            make_item(
                item_id="198",
                name="0834M32",
                tx="e9ef5d6a-ac9f-4433-82b7-08236b931d83_yyyo_pdf-0",
                sku="083662367",
            )
        ]
    }

    merged = asyncio.run(
        helper.create_entity_in_bulk_and_merge_with_current(
            current,
            "Items",
            {"Items": [{"Name": "0834M32", "Code": provider_code}]},
            "Name",
            "Code",
        )
    )

    item = merged["117b0811f2c14319f8d4"][0]
    assert item.id == provider_code
    assert item.sku == provider_code


def test_duplicate_item_names_reconcile_by_provider_code_not_name_only():
    helper = EntityHelper(context=None, repo=None, client=None)
    code_a = "c9a2955c243befb190aa_QS5145263"
    code_b = "c9a2955c243befb190aa_QS5317780"

    async def fake_create_entity_in_bulk(entity, payload):
        return [
            {"Name": "QS50P", "Code": code_a},
            {"Name": "QS50P", "Code": code_b},
        ]

    helper.create_entity_in_bulk = fake_create_entity_in_bulk

    current = {
        "c9a2955c243befb190aa": [
            make_item(
                item_id=None,
                name="QS50P",
                tx="cc21b8b2-bf58-46f0-9f98-af8f394acfe4_yeee_pdf-0",
                sku="QS5145263",
            ),
            make_item(
                item_id="198",
                name="QS50P",
                tx="cc21b8b2-bf58-46f0-9f98-af8f394acfe4_yeee_pdf-0",
                sku="QS5317780",
            ),
        ]
    }

    merged = asyncio.run(
        helper.create_entity_in_bulk_and_merge_with_current(
            current,
            "Items",
            {
                "Items": [
                    {"Name": "QS50P", "Code": code_a},
                    {"Name": "QS50P", "Code": code_b},
                ]
            },
            "Name",
            "Code",
        )
    )

    first, second = merged["c9a2955c243befb190aa"]
    assert first.id == code_a
    assert first.sku == code_a
    assert second.id == code_b
    assert second.sku == code_b
