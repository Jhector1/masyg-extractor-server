from typing import Dict, Any, Optional
from fastapi import Request

from masyg_extractor.services.file_extractor_service import remove_non_alphanumeric
from masyg_extractor.services.my_log import logger
from masyg_extractor.integrations.quickbooks.quickbooks_client import quickbooks_request


class AccountService:
    @staticmethod
    async def check_account_exists(
        request: Request,
        account_name: str,
        account_id: Optional[str] = None,
        client_id: str = ""
    ) -> bool:
        # Sanitize the account name
        account_name = remove_non_alphanumeric(account_name)
        if not account_name and not account_id:
            logger.warning("check_account_exists called without account_name or account_id.")
            return False

        # Validate account_id if provided, then convert to string.
        account_id_str = None
        if account_id is not None and str(account_id).strip():
            try:
                # Validate by converting to int, then back to string
                validated_id = int(account_id)
                account_id_str = str(validated_id)
            except (ValueError, TypeError):
                logger.error(f"Invalid account_id provided: {account_id}. It must be an integer.")
                return False

        # First, check using the account_id if available.
        if account_id_str:
            query_by_id = f"SELECT * FROM Account WHERE Id = '{account_id_str}'"
            try:
                response = await quickbooks_request(
                    request,
                    "query",
                    method="GET",
                    params={"query": query_by_id},
                    client_id=client_id
                )
                accounts = response.get("QueryResponse", {}).get("Account", [])
                if accounts:
                    logger.info(f"Account found by id: {account_id_str}")
                    return True
            except Exception as e:
                logger.error(f"Error checking account existence by id: {e} | Query: {query_by_id}")

        # If not found by id or only account_name provided, check by name.
        if account_name:
            query_by_name = f"SELECT * FROM Account WHERE Name = '{account_name}'"
            try:
                response = await quickbooks_request(
                    request,
                    "query",
                    method="GET",
                    params={"query": query_by_name},
                    client_id=client_id
                )
                accounts = response.get("QueryResponse", {}).get("Account", [])
                if accounts:
                    logger.info(f"Account found by name: {account_name}")
                    return True
            except Exception as e:
                logger.error(f"Error checking account existence by name: {e} | Query: {query_by_name}")
                return False

        logger.info(f"Account not found: {account_name} (ID: {account_id_str})")
        return False

    @staticmethod
    async def create_account(
        request: Request,
        account_data: Dict[str, Any],
        client_id: str = ""
    ) -> str:
        # Sanitize and validate the account name.
        account_name = remove_non_alphanumeric(account_data.get("account_name", "Unnamed Account")[:100])
        if not account_name:
            account_name = "Unnamed Account"

        # Build the payload using provided or default data.
        payload = {
            "Name": account_name,
            "AccountType": account_data.get("account_type", "Other Income"),
            # "AccountSubType": account_data.get("account_sub_type", "OtherIncome"),
            "Description": account_data.get("description", ""),
            "Active": True
        }

        logger.info(f"Creating account with payload (sanitized): {{'Name': {payload.get('Name')}}}")
        try:
            response = await quickbooks_request(
                request,
                "account",
                payload=payload,
                method="POST",
                client_id=client_id
            )
            logger.info("create_account response received.")
            if "Account" in response and "Id" in response["Account"]:
                account_id = response["Account"]["Id"]
                logger.info(f"Account created with ID: {account_id}")
                return account_id
            else:
                logger.error(f"Failed to create account. Response: {response}")
                raise Exception("Failed to create account.")
        except Exception as e:
            logger.error(f"Exception in create_account: {e}")
            raise


# Helper functions to directly call the service methods.
async def check_account_exists(
    account_name: str,
    account_id: Optional[str] = None,
    client_id: str = "",
    request: Request = None
) -> bool:
    account_name = remove_non_alphanumeric(account_name)
    return await AccountService.check_account_exists(request, account_name, account_id, client_id=client_id)


async def create_account(
    account_data: Dict[str, Any],
    client_id: str = "",
    request: Request = None
) -> str:
    return await AccountService.create_account(request, account_data, client_id=client_id)
