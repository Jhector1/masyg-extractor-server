from fastapi import APIRouter, Request, status, Depends
from fastapi.responses import JSONResponse, RedirectResponse
import base64
import requests
import os
from cryptography.fernet import Fernet

from masyg_extractor.config.jwt_config import get_current_user_from_cookie
from masyg_extractor.integrations.auth_helper import AuthHelper

# Configuration and encryption initialization
# CLIENT_ID = os.getenv("XERO_CLIENT_ID")
# CLIENT_SECRET = os.getenv("XERO_CLIENT_SECRET")
# SERVER_URL = os.getenv("SERVER_URL")
# CLIENT_URL = os.getenv("CLIENT_URL")
# REDIRECT_URI = f"{SERVER_URL}/integrations/xero/auth/callback"
#
# # Xero OAuth2 endpoints
# XERO_AUTH_URL = "https://login.xero.com/identity/connect/authorize"
# XERO_TOKEN_URL = "https://identity.xero.com/connect/token"
# # Example scopes: include offline_access to obtain refresh tokens
# SCOPES = "openid profile email accounting.transactions offline_access accounting.settings accounting.contacts"
#
# ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY")
# fernet = Fernet(ENCRYPTION_KEY)

router = APIRouter(prefix="/auth")

# def encrypt_token(token: str) -> str:
#     return fernet.encrypt(token.encode()).decode()
#
# def decrypt_token(token_encrypted: str) -> str:
#     return fernet.decrypt(token_encrypted.encode()).decode()
#
# def nocache_response(response):
#     response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
#     response.headers["Pragma"] = "no-cache"
#     response.headers["Expires"] = "0"
#     return response
xero_auth_helper = AuthHelper(
    integration="xero",         # This tells the helper to use QuickBooks settings.
    code_param="code",                # The query parameter name for the auth code.
    extra_param="id_token",            # QuickBooks provides an extra parameter 'realmId'.
    expires_param="expires_in"        # The expiry parameter name.
)
@router.get("/login")
async def login(current_user: dict = Depends(get_current_user_from_cookie),
                ):
    user_id = current_user.get("userId")
    return await xero_auth_helper.login(user_id)
    # auth_url = (
    #     f"{XERO_AUTH_URL}"
    #     f"?response_type=code"
    #     f"&client_id={CLIENT_ID}"
    #     f"&redirect_uri={REDIRECT_URI}"
    #     f"&scope={SCOPES}"
    #     f"&state=secure_random_state"  # In production, generate and validate a secure state
    # )
    # return RedirectResponse(auth_url)

@router.get("/callback")
async def callback(request: Request):
    return await xero_auth_helper.callback(request)
    # error = request.query_params.get("error")
    # if error:
    #     return JSONResponse({"error": error}, status_code=status.HTTP_400_BAD_REQUEST)
    #
    # auth_code = request.query_params.get("code")
    # if not auth_code:
    #     return JSONResponse({"error": "No authorization code received."}, status_code=status.HTTP_400_BAD_REQUEST)
    #
    # token_data = {
    #     "grant_type": "authorization_code",
    #     "code": auth_code,
    #     "redirect_uri": REDIRECT_URI,
    # }
    #
    # # Xero requires Basic Authentication using a base64-encoded client_id:client_secret pair
    # basic_auth = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    # headers = {
    #     "Authorization": f"Basic {basic_auth}",
    #     "Content-Type": "application/x-www-form-urlencoded"
    # }
    #
    # token_response = requests.post(XERO_TOKEN_URL, headers=headers, data=token_data)
    # response_json = token_response.json()
    #
    # access_token = response_json.get("access_token")
    # refresh_token = response_json.get("refresh_token")
    # id_token = response_json.get("id_token")  # Optional: contains user identity information
    #
    # if not access_token or not refresh_token:
    #     return JSONResponse(
    #         {"error": "Failed to obtain tokens.", "details": response_json},
    #         status_code=status.HTTP_400_BAD_REQUEST
    #     )
    #
    # # Store tokens in a dedicated Xero session namespace.
    # request.session["xero"] = {
    #     "access_token": access_token,
    #     "refresh_token_encrypted": encrypt_token(refresh_token),
    #     "id_token": id_token
    # }
    #
    # # Call the Xero connections endpoint to retrieve tenant info.
    # connections_url = "https://api.xero.com/connections"
    # conn_headers = {
    #     "Authorization": f"Bearer {access_token}",
    #     "Content-Type": "application/json"
    # }
    # connections_response = requests.get(connections_url, headers=conn_headers)
    # connections_data = connections_response.json()
    # if connections_data and isinstance(connections_data, list):
    #     tenant_id = connections_data[0].get("tenantId")
    #     if tenant_id:
    #         request.session["xero"]["tenant_id"] = tenant_id
    #     else:
    #         print("No tenantId found in the connection data.")
    # else:
    #     print("Failed to retrieve connections or no connections available.")
    #
    # return RedirectResponse(f"{CLIENT_URL}/data/shore/xero")

# @router.get("/connections")
# async def connections(request: Request):
#     """
#     Retrieves the list of Xero connections (organizations) the user has authorized.
#     """
#     if "xero" not in request.session:
#         return RedirectResponse("/integrations/xero/auth/login")
#
#     xero_data = request.session.get("xero")
#     headers = {
#         "Authorization": f"Bearer {xero_data.get('access_token')}",
#         "Content-Type": "application/json"
#     }
#     connections_url = "https://api.xero.com/connections"
#     connections_response = requests.get(connections_url, headers=headers)
#     return nocache_response(JSONResponse(connections_response.json()))

@router.get("/logout")
async def logout(request: Request):
    # Clear only the Xero session data.
    if "xero" in request.session:
        del request.session["xero"]
    return RedirectResponse("/")

@router.post("/refresh-token")
async def refresh_quickbooks_token( current_user: dict = Depends(get_current_user_from_cookie)):
    # Replace with your logic to get the current user's ID
    user_id = current_user.get("userId")
    return await xero_auth_helper.refresh_token(user_id)
    # xero_data = request.session.get("xero", {})
    # refresh_token_encrypted = xero_data.get("refresh_token_encrypted")
    # if not refresh_token_encrypted:
    #     return nocache_response(JSONResponse({"error": "Unauthorized. Please log in again."}, status_code=401))
    #
    # try:
    #     refresh_token = decrypt_token(refresh_token_encrypted)
    # except Exception:
    #     return nocache_response(JSONResponse({"error": "Internal error. Please try again."}, status_code=500))
    #
    # headers = {
    #     "Content-Type": "application/x-www-form-urlencoded",
    #     "Authorization": "Basic " + base64.b64encode(f'{CLIENT_ID}:{CLIENT_SECRET}'.encode()).decode()
    # }
    # data = {
    #     "grant_type": "refresh_token",
    #     "refresh_token": refresh_token,
    # }
    # token_response = requests.post(XERO_TOKEN_URL, headers=headers, data=data)
    # response_data = token_response.json()
    #
    # if "access_token" in response_data:
    #     access_token = response_data["access_token"]
    #     new_refresh_token = response_data.get("refresh_token", refresh_token)
    #     # Update only token values while preserving other Xero session data (e.g., tenant_id)
    #     xero_data["access_token"] = access_token
    #     xero_data["refresh_token_encrypted"] = encrypt_token(new_refresh_token)
    #     request.session["xero"] = xero_data
    #     return nocache_response(JSONResponse({
    #         "message": "Token refreshed successfully.",
    #         "access_token": access_token
    #     }))
    # else:
    #     error = response_data.get("error")
    #     if error == "invalid_grant":
    #         return nocache_response(JSONResponse({"error": "Session expired. Please reauthenticate."}, status_code=401))
    #     return nocache_response(JSONResponse({
    #         "error": "Token refresh failed.",
    #         "details": response_data
    #     }, status_code=400))
