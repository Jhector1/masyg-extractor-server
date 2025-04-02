from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
import base64
import requests
from cryptography.fernet import Fernet
import os

# Config and encryption initialization (ensure these variables are set)
CLIENT_ID = os.getenv("QB_CLIENT_ID")
CLIENT_SECRET = os.getenv("QB_CLIENT_SECRET")
SERVER_URL = os.getenv("SERVER_URL")
CLIENT_URL = os.getenv("CLIENT_URL")
REDIRECT_URI = f"{SERVER_URL}/integrations/quickbook/auth/callback"
QB_AUTH_URL = "https://appcenter.intuit.com/connect/oauth2"
QB_TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
QB_USERINFO_URL = "https://accounts.platform.intuit.com/v1/openid_connect/userinfo"
SCOPES = "com.intuit.quickbooks.accounting openid profile email phone"

ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY")
fernet = Fernet(ENCRYPTION_KEY)

router = APIRouter(prefix="/auth")

def encrypt_token(token: str) -> str:
    return fernet.encrypt(token.encode()).decode()

def decrypt_token(token_encrypted: str) -> str:
    return fernet.decrypt(token_encrypted.encode()).decode()

def nocache_response(response):
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

@router.get("/login")
async def login():
    auth_url = (
        f"{QB_AUTH_URL}"
        f"?client_id={CLIENT_ID}"
        f"&response_type=code"
        f"&scope={SCOPES}"
        f"&redirect_uri={REDIRECT_URI}"
        f"&state=secure_random_state"
    )
    return RedirectResponse(auth_url)

@router.get("/callback")
async def callback(request: Request):
    error = request.query_params.get("error")
    if error:
        return JSONResponse({"error": error}, status_code=status.HTTP_400_BAD_REQUEST)

    auth_code = request.query_params.get("code")
    realm_id = request.query_params.get("realmId")
    if not auth_code:
        return JSONResponse(
            {"error": "No authorization code received."},
            status_code=status.HTTP_400_BAD_REQUEST
        )

    token_data = {
        "grant_type": "authorization_code",
        "code": auth_code,
        "redirect_uri": REDIRECT_URI,
    }
    token_response = requests.post(
        QB_TOKEN_URL,
        auth=(CLIENT_ID, CLIENT_SECRET),
        data=token_data,
        headers={"Accept": "application/json"},
    )
    response_json = token_response.json()

    access_token = response_json.get("access_token")
    refresh_token = response_json.get("refresh_token")
    if not access_token or not refresh_token:
        return JSONResponse(
            {"error": "Failed to obtain tokens."},
            status_code=status.HTTP_400_BAD_REQUEST
        )

    # Save tokens and realm id in a dedicated QuickBooks session namespace.
    request.session["quickbooks"] = {
        "access_token": access_token,
        "refresh_token_encrypted": encrypt_token(refresh_token),
        "realm_id": realm_id
    }
    return RedirectResponse(f"{CLIENT_URL}/data/shore/quickbooks")

@router.get("/profile")
async def profile(request: Request):
    # If the QuickBooks session data is missing, redirect to login.
    if "quickbooks" not in request.session:
        return RedirectResponse("/integrations/quickbook/login")

    qb_data = request.session.get("quickbooks")
    headers = {"Authorization": f"Bearer {qb_data.get('access_token')}"}
    user_info = requests.get(QB_USERINFO_URL, headers=headers).json()
    return nocache_response(JSONResponse(user_info))

@router.get("/logout")
async def logout(request: Request):
    # Clear only the QuickBooks session data
    if "quickbooks" in request.session:
        del request.session["quickbooks"]
    return RedirectResponse("/")

@router.post("/refresh_token")
async def refresh_quickbooks_token(request: Request):
    qb_data = request.session.get("quickbooks", {})
    refresh_token_encrypted = qb_data.get("refresh_token_encrypted")
    if not refresh_token_encrypted:
        return nocache_response(JSONResponse(
            {"error": "Unauthorized. Please log in again."},
            status_code=401
        ))

    try:
        refresh_token = decrypt_token(refresh_token_encrypted)
    except Exception:
        return nocache_response(JSONResponse(
            {"error": "Internal error. Please try again."},
            status_code=500
        ))

    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
        "Authorization": "Basic " + base64.b64encode(f'{CLIENT_ID}:{CLIENT_SECRET}'.encode()).decode()
    }
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token
    }
    token_response = requests.post(QB_TOKEN_URL, headers=headers, data=data)
    response_data = token_response.json()

    if "access_token" in response_data:
        access_token = response_data["access_token"]
        new_refresh_token = response_data.get("refresh_token", refresh_token)
        # Update only token values while preserving existing data such as realm_id.
        qb_data["access_token"] = access_token
        qb_data["refresh_token_encrypted"] = encrypt_token(new_refresh_token)
        request.session["quickbooks"] = qb_data
        return nocache_response(JSONResponse({
            "message": "Token refreshed successfully.",
            "access_token": access_token
        }))
    else:
        error = response_data.get("error")
        if error == "invalid_grant":
            return nocache_response(JSONResponse(
                {"error": "Session expired. Please reauthenticate."},
                status_code=401
            ))
        return nocache_response(JSONResponse(
            {"error": "Token refresh failed.", "details": response_data},
            status_code=400
        ))
