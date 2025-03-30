from pydantic import BaseModel

class Customer(BaseModel):
    id: str | None
    display_name: str
    # Add other fields (e.g., email) if needed
