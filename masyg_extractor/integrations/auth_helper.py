

import os
import base64
import requests
from datetime import datetime, timedelta
from fastapi import Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from cryptography.fernet import Fernet
from masyg_extractor.integrations.quickbooks.authentication.encryption_state import encrypt_state, decrypt_state

# Import your Firestore service for QuickBooks.
from masyg_extractor.integration_v4.repository.firestore_repository import QuickBooksFirestoreService

class AuthHelper:
    SERVER_URL = os.getenv("SERVER_URL")
    CLIENT_URL = os.getenv("CLIENT_URL")
    ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY")
    fernet = Fernet(ENCRYPTION_KEY)

    def __init__(
        self,
        integration: str,
        code_param: str = "code",        # Name of the query param for auth code (default "code")
        extra_param: str = "realmId",     # Extra parameter (for example, QuickBooks passes "realmId")
        expires_param: str = "expires_in"   # Parameter for token expiry time
    ):
        self.integration = integration.lower()
        self.code_param = code_param
        self.extra_param = extra_param
        self.expires_param = expires_param

        self.CLIENT_ID = os.getenv(f"{integration.upper()}_CLIENT_ID")
        self.CLIENT_SECRET = os.getenv(f"{integration.upper()}_CLIENT_SECRET")
        self.AUTH_URL = os.getenv(f"{integration.upper()}_AUTH_URL")
        self.TOKEN_URL = os.getenv(f"{integration.upper()}_TOKEN_URL")
        self.USERINFO_URL = os.getenv(f"{integration.upper()}_USERINFO_URL")
        self.SCOPES = os.getenv(f"{integration.upper()}_SCOPES")
        self.REDIRECT_URI = f"{self.SERVER_URL}/integrations/{self.integration}/auth/callback"

    @staticmethod
    def nocache_response(response):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response

    def get_basic_auth_header(self):
        basic = base64.b64encode(f"{self.CLIENT_ID}:{self.CLIENT_SECRET}".encode()).decode()
        return {"Authorization": f"Basic {basic}"}

    async def login(self, user_id: str):
        # Encrypt the user ID into state to later validate callback.
        state = encrypt_state(user_id)
        auth_url = (
            f"{self.AUTH_URL}"
            f"?client_id={self.CLIENT_ID}"
            f"&response_type=code"
            f"&scope={self.SCOPES}"
            f"&redirect_uri={self.REDIRECT_URI}"
            f"&state={state}"

        )

        return RedirectResponse(auth_url)

    async def callback(self, request: Request):
        error = request.query_params.get("error")
        if error:
            return JSONResponse({"error": error}, status_code=status.HTTP_400_BAD_REQUEST)

        auth_code = request.query_params.get(self.code_param)
        state = request.query_params.get("state")
        extra_value = request.query_params.get(self.extra_param)  # e.g. realmId

        if not auth_code:
            return JSONResponse(
                {"error": "No authorization code received."},
                status_code=status.HTTP_400_BAD_REQUEST
            )

        # Prepare the token request data.
        token_data = {
            "grant_type": "authorization_code",
            "code": auth_code,
            "redirect_uri": self.REDIRECT_URI,
        }

        # Determine the correct headers for the request. For example, Xero expects credentials in the header.
        headers = {"Accept": "application/json"}
        if self.integration == "xero":
            headers.update({
                "Content-Type": "application/x-www-form-urlencoded",
                **self.get_basic_auth_header()
            })
            token_response = requests.post(self.TOKEN_URL, headers=headers, data=token_data)
        else:
            token_response = requests.post(
                self.TOKEN_URL,
                auth=(self.CLIENT_ID, self.CLIENT_SECRET),
                data=token_data,
                headers=headers,
            )

        response_json = token_response.json()

        access_token = response_json.get("access_token")
        refresh_token = response_json.get("refresh_token")
        expires_in = response_json.get(self.expires_param)


        if not access_token or not refresh_token or not expires_in:
            return JSONResponse(
                {"error": "Failed to obtain tokens.", "details": response_json},
                status_code=status.HTTP_400_BAD_REQUEST
            )
        user_id = decrypt_state(state)


        if self.integration == "xero":
            id_token = response_json.get("id_token")
            tenant_id = get_xero_tenant_id(access_token)
            QuickBooksFirestoreService.store_integration_token_statically(user_id, access_token, refresh_token, expires_in,  self.integration,
                                    id_token = id_token,tenant_id=tenant_id)

        # return RedirectResponse(f"{CLIENT_URL}/data/shore/xero")

        # For QuickBooks, use your Firestore service to store the token.
        # if self.integration == "quickbooks":
            # Calling store_integration_token with access_token, refresh_token, expires_in, and extra_value (realmId)
        else:
            QuickBooksFirestoreService.store_integration_token_statically(user_id, access_token, refresh_token, expires_in,  self.integration,realmId = extra_value,
                                   )
        # else:
        #     # Extend or override this block for other integrations.
        #     pass

        return RedirectResponse(f"{self.CLIENT_URL}/data/shore/{self.integration}")

    async def refresh_token(self, user_id: str):
        # if self.integration == "quickbooks":
        qb_service = QuickBooksFirestoreService(user_id, self.integration)
        token_data = qb_service.get_integration_token()
        if not token_data:
            return JSONResponse({"error": f"{self.integration} integration not set up."}, status_code=401)
        # QuickBooks token data may use different key names.
        refresh_token = token_data.get("refreshToken") or token_data.get("refresh_token")
        if not refresh_token:
            return JSONResponse({"error": "Refresh token missing."}, status_code=401)
        # else:
        #     return JSONResponse(
        #         {"error": "Refresh token mechanism not implemented for this integration."},
        #         status_code=400
        #     )

        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            **self.get_basic_auth_header()
        }
        data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token
        }
        token_response = requests.post(self.TOKEN_URL, headers=headers, data=data)
        response_data = token_response.json()

        if "access_token" in response_data:
            new_access_token = response_data["access_token"]
            new_refresh_token = response_data.get("refresh_token", refresh_token)
            new_expires_in = response_data.get(self.expires_param)

            if self.integration == "quickbooks":

                qb_service.store_integration_token(
                    new_access_token,
                    new_refresh_token,
                    new_expires_in,

                    realmId=token_data.get(self.extra_param) #or token_data.get("id_token")
                )
            else:
                qb_service.store_integration_token(
                    new_access_token,
                    new_refresh_token,
                    new_expires_in,

                    id_token=token_data.get(self.extra_param)  # or token_data.get("id_token")
                )

            return JSONResponse({"message": "Token refreshed successfully.", "access_token": new_access_token})
        else:
            error = response_data.get("error")
            if error == "invalid_grant":
                return JSONResponse(
                    {"error": "Session expired. Please reauthenticate."},
                    status_code=401
                )
            return JSONResponse(
                {"error": "Token refresh failed.", "details": response_data},
                status_code=400
            )



def get_xero_tenant_id(access_token: str):
    connections_url = "https://api.xero.com/connections"
    conn_headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    connections_response = requests.get(connections_url, headers=conn_headers)
    connections_data = connections_response.json()
    if connections_data and isinstance(connections_data, list):
        tenant_id = connections_data[0].get("tenantId")
        if tenant_id:
            return tenant_id
            # request.session["xero"]["tenant_id"] = tenant_id
        else:
            # print("No tenantId found in the connection data.")

            raise Exception("Failed to obtain tenant_id.")
    else:
        print("Failed to retrieve connections or no connections available.")

        raise Exception("Failed to obtain tenant_id.")

    # return RedirectResponse(f"{CLIENT_URL}/data/shore/xero")

#
# from fastapi import  Request, status
# from fastapi.responses import JSONResponse, RedirectResponse
# import base64
# import requests
# from cryptography.fernet import Fernet
# import os
#
# from masyg_extractor.integrations.quickbooks.authentication.encryption_state import encrypt_state, decrypt_state
# from masyg_extractor.integrations.quickbooks.repository.firestore_repository import firestore_db, \
#      store_integration_token
# # QuickBooks
#
# # Config and encryption initialization (ensure these variables are set)
# class AuthHelper:
#     ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY")
#     fernet = Fernet(ENCRYPTION_KEY)
#     SERVER_URL = os.getenv("SERVER_URL")
#     CLIENT_URL = os.getenv("CLIENT_URL")
#     def __init__(self,  integration:str, *args, **kwargs):
#         self.CLIENT_ID = os.getenv(f"{integration.upper()}_CLIENT_ID")
#         self.CLIENT_SECRET = os.getenv(f"{integration.upper()}_CLIENT_SECRET")
#
#         self.REDIRECT_URI = f"{AuthHelper.SERVER_URL}/integrations/{integration}/auth/callback"
#         self.AUTH_URL = os.getenv(f"{integration.upper()}_AUTH_URL")
#         self.TOKEN_URL = os.getenv(f"{integration.upper()}_TOKEN_URL")
#         self.USERINFO_URL = os.getenv(f"{integration.upper()}_USERINFO_URL")
#         self.SCOPES = os.getenv(f"{integration.upper()}_SCOPES")
#         self.args = args
#         self.kwargs = kwargs
#         self.integration = integration
#
#
#     @staticmethod
#     def nocache_response( response):
#         response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
#         response.headers["Pragma"] = "no-cache"
#         response.headers["Expires"] = "0"
#         return response
#
#
#
#     async def login(self, user_id
#                     ):
#
#         state = encrypt_state(user_id)
#         auth_url = (
#             f"{self.AUTH_URL}"
#             f"?client_id={self.CLIENT_ID}"
#             f"&response_type=code"
#             f"&scope={self.SCOPES}"
#             f"&redirect_uri={self.REDIRECT_URI}"
#             f"&state={state}"
#         )
#         return RedirectResponse(auth_url)
#
#
#
#     async def callback(self,request: Request ):
#         error = request.query_params.get("error")
#         if error:
#             return JSONResponse({"error": error}, status_code=status.HTTP_400_BAD_REQUEST)
#
#         auth_code = request.query_params.get(self.args[0])
#         realm_id = request.query_params.get(self.args[1])
#         state = request.query_params.get("state")
#
#         if not auth_code:
#             return JSONResponse(
#                 {"error": "No authorization code received."},
#                 status_code=status.HTTP_400_BAD_REQUEST
#             )
#
#         token_data = {
#             "grant_type": "authorization_code",
#             "code": auth_code,
#             "redirect_uri": self.REDIRECT_URI,
#         }
#         token_response = requests.post(
#             self.TOKEN_URL,
#             auth=(self.CLIENT_ID, self.CLIENT_SECRET),
#             data=token_data,
#             headers={"Accept": "application/json"},
#         )
#         response_json = token_response.json()
#
#         access_token = response_json.get("access_token")
#         refresh_token = response_json.get("refresh_token")
#         expires_in = response_json.get(self.args[2])
#         if not access_token or not refresh_token or not expires_in:
#             return JSONResponse(
#                 {"error": "Failed to obtain tokens."},
#                 status_code=status.HTTP_400_BAD_REQUEST
#             )
#
#         # Replace this with your method for determining the current user's ID.
#         user_id = decrypt_state(state)
#
#         # Store tokens in Firestore
#         store_integration_token(user_id, access_token, refresh_token, expires_in, realm_id, self.integration, more="")
#
#         return RedirectResponse(f"{self.CLIENT_URL}/data/shore/{self.integration}")
#
#
#
#     # async def profile(self,request: Request):
#     #     # If the QuickBooks session data is missing, redirect to login.
#     #     if "quickbooks" not in request.session:
#     #         return RedirectResponse("/integrations/quickbook/login")
#     #
#     #
#     #     headers = {"Authorization": f"Bearer {qb_data.get('access_token')}"}
#     #     user_info = requests.get(self.USERINFO_URL, headers=headers).json()
#     #     return self.nocache_response(JSONResponse(user_info))
#
#
#     # @router.get("/logout")
#     # async def logout(request: Request):
#     #     # Clear only the QuickBooks session data
#     #     if "quickbooks" in request.session:
#     #         del request.session["quickbooks"]
#     #     return RedirectResponse("/")
#
#
#
#     async def refresh_token(self,user_id):
#         # Replace with your logic to get the current user's ID
#
#
#         qb_doc = firestore_db.collection("users").document(user_id) \
#             .collection("integrations").document(self.integration).get()
#         if not qb_doc.exists:
#             return JSONResponse({"error": f"{self.integration} integration not set up."}, status_code=401)
#
#         token_data = qb_doc.to_dict().get("tokenData", {})
#         refresh_token = token_data.get("refreshToken")
#         if not refresh_token:
#             return JSONResponse({"error": "Refresh token missing."}, status_code=401)
#
#         headers = {
#             "Content-Type": "application/x-www-form-urlencoded",
#             "Accept": "application/json",
#             "Authorization": "Basic " + base64.b64encode(f"{self.CLIENT_ID}:{self.CLIENT_SECRET}".encode()).decode()
#         }
#         data = {
#             "grant_type": "refresh_token",
#             "refresh_token": refresh_token
#         }
#         token_response = requests.post(self.TOKEN_URL, headers=headers, data=data)
#         response_data = token_response.json()
#
#         if "access_token" in response_data:
#             new_access_token = response_data["access_token"]
#             new_refresh_token = response_data.get("refresh_token", refresh_token)
#             new_expires_in = response_data.get(self.args[2])
#             # Update Firestore with the new tokens
#             store_integration_token(user_id, new_access_token, new_refresh_token, new_expires_in, token_data.get("realmId"), self.integration, more="")
#             return JSONResponse({"message": "Token refreshed successfully.", "access_token": new_access_token})
#         else:
#             error = response_data.get("error")
#             if error == "invalid_grant":
#                 return JSONResponse(
#                     {"error": "Session expired. Please reauthenticate."},
#                     status_code=401
#                 )
#             return JSONResponse(
#                 {"error": "Token refresh failed.", "details": response_data},
#                 status_code=400
#             )
