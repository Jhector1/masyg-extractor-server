from fastapi import APIRouter, Request, status, Depends, HTTPException
from fastapi.responses import JSONResponse, RedirectResponse
import base64
import requests
from cryptography.fernet import Fernet
import os

from masyg_extractor.config.jwt_config import get_current_user_from_cookie
from masyg_extractor.integrations.auth_helper import AuthHelper
from masyg_extractor.integrations.quickbooks.authentication.encryption_state import encrypt_state, decrypt_state
from masyg_extractor.integrations.quickbooks.repository.firestore_repository import firestore_db

# Config and encryption initialization (ensure these variables are set)
# CLIENT_ID = os.getenv("QB_CLIENT_ID")
# CLIENT_SECRET = os.getenv("QB_CLIENT_SECRET")
# SERVER_URL = os.getenv("SERVER_URL")
# CLIENT_URL = os.getenv("CLIENT_URL")
# REDIRECT_URI = f"{SERVER_URL}/integrations/quickbook/auth/callback"
# QB_AUTH_URL = os.getenv("QB_AUTH_URL")
# QB_TOKEN_URL = os.getenv("QB_TOKEN_URL")
# QB_USERINFO_URL = os.getenv("QB_USERINFO_URL")
# SCOPES = os.getenv("QB_SCOPES")
#
# ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY")
# fernet = Fernet(ENCRYPTION_KEY)

router = APIRouter(prefix="/auth")


# def encrypt_token(token: str) -> str:
#     return fernet.encrypt(token.encode()).decode()
#
#
# def decrypt_token(token_encrypted: str) -> str:
#     return fernet.decrypt(token_encrypted.encode()).decode()


# def nocache_response(response):
#     response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
#     response.headers["Pragma"] = "no-cache"
#     response.headers["Expires"] = "0"
#     return response

qb_auth_helper = AuthHelper(
    integration="quickbooks",         # This tells the helper to use QuickBooks settings.
    code_param="code",                # The query parameter name for the auth code.
    extra_param="realmId",            # QuickBooks provides an extra parameter 'realmId'.
    expires_param="expires_in"        # The expiry parameter name.
)

@router.get("/login")
async def login(current_user: dict = Depends(get_current_user_from_cookie),
                ):
    user_id = current_user.get("userId")
    return await qb_auth_helper.login(user_id)
    # if not user_id:
    #     raise HTTPException(status_code=401, detail="User ID missing in token payload")
    #
    #
    # state = encrypt_state(user_id)
    # auth_url = (
    #     f"{QB_AUTH_URL}"
    #     f"?client_id={CLIENT_ID}"
    #     f"&response_type=code"
    #     f"&scope={SCOPES}"
    #     f"&redirect_uri={REDIRECT_URI}"
    #     f"&state={state}"
    # )
    # return RedirectResponse(auth_url)


@router.get("/callback")
async def callback(request: Request):
    return await qb_auth_helper.callback(request)
    # error = request.query_params.get("error")
    # if error:
    #     return JSONResponse({"error": error}, status_code=status.HTTP_400_BAD_REQUEST)
    #
    # auth_code = request.query_params.get("code")
    # realm_id = request.query_params.get("realmId")
    # state = request.query_params.get("state")
    #
    # if not auth_code:
    #     return JSONResponse(
    #         {"error": "No authorization code received."},
    #         status_code=status.HTTP_400_BAD_REQUEST
    #     )
    #
    # token_data = {
    #     "grant_type": "authorization_code",
    #     "code": auth_code,
    #     "redirect_uri": REDIRECT_URI,
    # }
    # token_response = requests.post(
    #     QB_TOKEN_URL,
    #     auth=(CLIENT_ID, CLIENT_SECRET),
    #     data=token_data,
    #     headers={"Accept": "application/json"},
    # )
    # response_json = token_response.json()
    #
    # access_token = response_json.get("access_token")
    # refresh_token = response_json.get("refresh_token")
    # expires_in = response_json.get("expires_in")
    # if not access_token or not refresh_token or not expires_in:
    #     return JSONResponse(
    #         {"error": "Failed to obtain tokens."},
    #         status_code=status.HTTP_400_BAD_REQUEST
    #     )
    #
    # # Replace this with your method for determining the current user's ID.
    # user_id = decrypt_state(state)
    #
    # # Store tokens in Firestore
    # store_quickbooks_token(user_id, access_token, refresh_token, expires_in, realm_id)
    #
    # return RedirectResponse(f"{CLIENT_URL}/data/shore/quickbooks")


# @router.get("/profile")
# async def profile(request: Request):
#     # If the QuickBooks session data is missing, redirect to login.
#     if "quickbooks" not in request.session:
#         return RedirectResponse("/integrations/quickbook/login")
#
#     qb_data = request.session.get("quickbooks")
#     headers = {"Authorization": f"Bearer {qb_data.get('access_token')}"}
#     user_info = requests.get(QB_USERINFO_URL, headers=headers).json()
#     return nocache_response(JSONResponse(user_info))
#

@router.get("/logout")
async def logout(request: Request):
    # Clear only the QuickBooks session data
    if "quickbooks" in request.session:
        del request.session["quickbooks"]
    return RedirectResponse("/")


@router.post("/refresh-token")
async def refresh_quickbooks_token( current_user: dict = Depends(get_current_user_from_cookie)):
    # Replace with your logic to get the current user's ID
    user_id = current_user.get("userId")
    return await qb_auth_helper.refresh_token(user_id)

    # qb_doc = firestore_db.collection("users").document(user_id) \
    #     .collection("integrations").document("quickbooks").get()
    # if not qb_doc.exists:
    #     return JSONResponse({"error": "QuickBooks integration not set up."}, status_code=401)
    #
    # token_data = qb_doc.to_dict().get("tokenData", {})
    # refresh_token = token_data.get("refreshToken")
    # if not refresh_token:
    #     return JSONResponse({"error": "Refresh token missing."}, status_code=401)
    #
    # headers = {
    #     "Content-Type": "application/x-www-form-urlencoded",
    #     "Accept": "application/json",
    #     "Authorization": "Basic " + base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    # }
    # data = {
    #     "grant_type": "refresh_token",
    #     "refresh_token": refresh_token
    # }
    # token_response = requests.post(QB_TOKEN_URL, headers=headers, data=data)
    # response_data = token_response.json()
    #
    # if "access_token" in response_data:
    #     new_access_token = response_data["access_token"]
    #     new_refresh_token = response_data.get("refresh_token", refresh_token)
    #     new_expires_in = response_data.get("expires_in")
    #     # Update Firestore with the new tokens
    #     store_quickbooks_token(user_id, new_access_token, new_refresh_token, new_expires_in, token_data.get("realmId"))
    #     return JSONResponse({"message": "Token refreshed successfully.", "access_token": new_access_token})
    # else:
    #     error = response_data.get("error")
    #     if error == "invalid_grant":
    #         return JSONResponse(
    #             {"error": "Session expired. Please reauthenticate."},
    #             status_code=401
    #         )
    #     return JSONResponse(
    #         {"error": "Token refresh failed.", "details": response_data},
    #         status_code=400
    #     )
