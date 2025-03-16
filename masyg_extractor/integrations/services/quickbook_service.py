import requests
from fastapi import Request, HTTPException, status
from fastapi.responses import JSONResponse

async def get_entities(request: Request, entity_type: str):
    """
    Fetches all customers or vendors from QuickBooks dynamically.

    Args:
        request (Request): The FastAPI Request object (with session available via SessionMiddleware).
        entity_type (str): Either "Customer" or "Vendor".

    Returns:
        A JSON response containing the list of entities, or raises an HTTPException with an error message.
    """
    # Access session from the request (make sure to add SessionMiddleware in your app)
    session = request.session
    realm_id = session.get("realm_id")
    access_token = session.get("access_token")

    if not realm_id or not access_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="User not authenticated")

    url = f"https://sandbox-quickbooks.api.intuit.com/v3/company/{realm_id}/query"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json"
    }
    params = {"query": f"SELECT * FROM {entity_type}"}

    try:
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()  # Raises HTTPError for bad responses (4xx or 5xx)
        data = response.json()
        entities = data.get("QueryResponse", {}).get(entity_type, [])
        return JSONResponse(content=entities, status_code=status.HTTP_200_OK)

    except requests.exceptions.HTTPError:
        try:
            error_details = response.json()
        except Exception:
            error_details = {}
        raise HTTPException(
            status_code=response.status_code,
            detail={
                "error": f"Failed to fetch {entity_type.lower()}s",
                "details": error_details
            }
        )
    except Exception as err:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An error occurred: {str(err)}"
        )
