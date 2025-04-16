from dataclasses import dataclass
from typing import Optional, List, Dict, Any
@dataclass
class Account:
    id: Optional[str]
    name: str

@dataclass
class Item:
    id: Optional[str]
    name: str
    quantity: int
    unit_price: float
    description: str
    income_account: Optional[Account]
    expense_account: Optional[Account]
    sku: Optional[str]
    QtyOnHand: Optional[float]
    type: str
    tax_code: Optional[str]
@dataclass
class Customer:
    id: Optional[str]
    name: str




@dataclass
class Document:
    # id: Optional[str]
    customer: Customer
    items: List[Item]
    # total_amount: Optional[float]
    date: str
    transaction_id: str
    group_id: str

@dataclass
class Invoice(Document):
    pass
@dataclass
class SalesReceipt(Document):
    pass
@dataclass
class Receipt:
    id: Optional[str]
    customer: Customer
    items: List[Item]
    total_amount: float
    date: str
    transaction_id: str


