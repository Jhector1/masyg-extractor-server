from typing import List
from pydantic import BaseModel

class InvoiceItem(BaseModel):
    item_name: str
    item_id: str | None
    description: str | None = None
    quantity: float
    unit_price: float
    income_account_id: str | None = None
    expense_account_id: str | None = None

class Invoice(BaseModel):
    transaction_id: str
    group_id: str
    customer_id: str | None
    customer_name: str
    date: str
    line_items: List[InvoiceItem]
