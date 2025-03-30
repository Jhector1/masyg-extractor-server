from pydantic import BaseModel

class Item(BaseModel):
    id: str | None
    name: str
    description: str | None = None
    income_account_id: str | None = None
    expense_account_id: str | None = None
