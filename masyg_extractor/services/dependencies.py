import json
from datetime import datetime
from fastapi import Request, HTTPException, status

async def get_firebase_user(request: Request) -> dict:
    firebase_user = request.session.get("user")
    if not firebase_user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not authenticated"
        )
    if isinstance(firebase_user, dict):
        return firebase_user
    try:
        return json.loads(firebase_user)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid firebase user data"
        )

def generate_group_id() -> str:
    """Generate a unique group ID based on the current timestamp."""
    return datetime.now().strftime('%Y%m%d%H%M%S')
