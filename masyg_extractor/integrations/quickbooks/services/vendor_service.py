import asyncio
from typing import Optional
from fastapi import Request
from masyg_extractor.services.my_log import logger, send_log
from masyg_extractor.integrations.quickbooks.quickbooks_client import quickbooks_request
from masyg_extractor.integrations.quickbooks.repository.firestore_repository import (
    store_vendor_record,
    vendor_exists_in_firestore
)
from masyg_extractor.integrations.quickbooks.helper.qb_helpers import check_entity_exists, fetch_entity_id_by_name, create_entity


class VendorService:
    @staticmethod
    def check_vendor_exists(
            request: Request,
            vendor_id: Optional[str] = None,
            vendor_name: Optional[str] = None,
            client_id: str = ""
    ) -> bool:
        if vendor_id:
            return check_entity_exists(request, "Vendor", "Id", vendor_id, client_id=client_id)
        if vendor_name:
            return check_entity_exists(request, "Vendor", "DisplayName", vendor_name, client_id=client_id)
        return False

    @staticmethod
    def create_vendor(
            request: Request,
            vendor_name: str,
            client_id: str = ""
    ) -> str:
        return create_entity(request, "Vendor", vendor_name, client_id=client_id)

    @staticmethod
    def fetch_vendor_id_by_name(
            request: Request,
            vendor_name: str,
            client_id: str = ""
    ) -> Optional[str]:
        return fetch_entity_id_by_name(request, "Vendor", vendor_name, client_id=client_id)


def get_or_create_vendor(
        request: Request,
        vendor_id: Optional[str],
        vendor_name: str,
        user_id: str,
        client_id: str = ""
) -> str:
    if not vendor_name or vendor_name.strip() == "":
        raise ValueError("vendor_name is required.")

    # If no vendor ID is provided, try to find an existing vendor by name.
    if not vendor_id:
        logger.info(f"Vendor ID not provided; checking QuickBooks for {vendor_name}")
        if VendorService.check_vendor_exists(request, vendor_name=vendor_name, client_id=client_id):
            vendor_id = VendorService.fetch_vendor_id_by_name(request, vendor_name, client_id=client_id)
            if vendor_id:
                logger.info(f"Found vendor in QuickBooks: {vendor_name} (ID: {vendor_id})")

    # If we have a vendor ID, ensure it exists in Firestore.
    if vendor_id and VendorService.check_vendor_exists(request, vendor_id, vendor_name, client_id=client_id):
        if not vendor_exists_in_firestore(user_id, vendor_id):
            store_vendor_record(user_id, vendor_id, {"Id": vendor_id, "DisplayName": vendor_name}, client_id=client_id)
            logger.info(f"Vendor {vendor_id} found in QuickBooks but not in Firestore. Saving...")
        else:
            logger.info(f"Vendor {vendor_id} exists in both Firestore and QuickBooks.")
        return vendor_id

    # Otherwise, create a new vendor.
    logger.info(f"Creating new vendor for {vendor_name}.")
    new_vendor_id = VendorService.create_vendor(request, vendor_name, client_id=client_id)
    store_vendor_record(user_id, new_vendor_id, {"Id": new_vendor_id, "DisplayName": vendor_name}, client_id=client_id)
    return new_vendor_id
