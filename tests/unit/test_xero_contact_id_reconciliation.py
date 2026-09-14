import asyncio
import sys
import types

from masyg_extractor.integrations.accounting.core.models import Customer


# These reconciliation tests do not exercise Firestore. The production
# firestore_repository module creates a Firebase client at import time, which
# makes an otherwise unit-only test depend on global Firebase initialization.
# Stub only that repository owner while importing the Xero services, then
# immediately remove the stub so the rest of the test session is unaffected.
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
    from masyg_extractor.integrations.accounting.xero.services.customer_service import (
        CustomerService,
    )
finally:
    sys.modules.pop(_firestore_module_name, None)


CONTACT_GUID_A = "78471c5f-be08-4b31-8f34-627f081d8147"
CONTACT_GUID_B = "7faffebb-015d-49a2-a8a7-63730a423ac1"


def test_existing_xero_contact_by_name_replaces_local_non_guid_id():
    helper = EntityHelper(context=None, repo=None, client=None)

    async def fake_fetch_all_entities(*args, **kwargs):
        return [{"Name": "89", "Id": CONTACT_GUID_A}]

    helper.fetch_all_entities = fake_fetch_all_entities

    customer = Customer(
        id="76",
        name="89",
        transaction_id="tx-yeee",
    )

    non_existing = asyncio.run(
        helper.get_non_existing_entities(
            [customer],
            "Contacts",
            "Name",
            "ContactID",
        )
    )

    assert non_existing == []
    assert customer.id == CONTACT_GUID_A


def test_newly_created_xero_contact_overwrites_local_non_guid_id():
    helper = EntityHelper(context=None, repo=None, client=None)

    async def fake_create_entity_in_bulk(entity, payload):
        assert entity == "Contacts"
        return [
            {
                "Name": "89",
                "ContactID": CONTACT_GUID_A,
                "ContactNumber": "customer_a_",
            },
            {
                "Name": "Amys Bird Sanctuary",
                "ContactID": CONTACT_GUID_B,
                "ContactNumber": "customer_b_",
            },
        ]

    helper.create_entity_in_bulk = fake_create_entity_in_bulk

    current = {
        "customer_a": Customer(
            id="76",
            name="89",
            transaction_id="tx-yeee",
        ),
        "customer_b": Customer(
            id="1",
            name="Amys Bird Sanctuary",
            transaction_id="tx-yyyo",
        ),
    }

    merged = asyncio.run(
        helper.create_entity_in_bulk_and_merge_with_current(
            current,
            "Contacts",
            {
                "Contacts": [
                    {"Name": "89", "ContactNumber": "customer_a_"},
                    {
                        "Name": "Amys Bird Sanctuary",
                        "ContactNumber": "customer_b_",
                    },
                ]
            },
            "Name",
            "ContactID",
            tracker_key="ContactNumber",
        )
    )

    assert merged["customer_a"].id == CONTACT_GUID_A
    assert merged["customer_b"].id == CONTACT_GUID_B
    assert merged["customer_a"].name == "89"
    assert merged["customer_b"].name == "Amys Bird Sanctuary"
    assert merged["customer_a"].transaction_id == "tx-yeee"
    assert merged["customer_b"].transaction_id == "tx-yyyo"


def test_xero_contact_id_guard_rejects_local_ids_and_accepts_guids():
    assert CustomerService._is_xero_contact_id(CONTACT_GUID_A)
    assert CustomerService._is_xero_contact_id(CONTACT_GUID_B)

    assert not CustomerService._is_xero_contact_id("76")
    assert not CustomerService._is_xero_contact_id("1")
    assert not CustomerService._is_xero_contact_id("")
    assert not CustomerService._is_xero_contact_id(None)
