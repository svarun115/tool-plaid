"""Pydantic models for Plaid data"""

from pydantic import BaseModel, Field


class Transaction(BaseModel):
    """Transaction data model."""

    transaction_id: str = Field(description="Unique transaction identifier")
    account_id: str = Field(description="Associated account ID")
    amount: float = Field(description="Transaction amount")
    date: str = Field(
        description="For posted transactions, the date it POSTED (bank settlement date, "
        "can lag the actual purchase by a few days). For pending transactions, the date "
        "it occurred. Prefer `authorized_date` when present — it reflects when the user "
        "actually made the transaction."
    )
    authorized_date: str = Field(
        default="",
        description="The date the transaction was authorized (i.e. when the user actually "
        "made it). Empty string if Plaid didn't return one for this transaction/pending "
        "state — fall back to `date` in that case.",
    )
    merchant_name: str = Field(default="", description="Merchant name")
    category: str = Field(default="", description="Transaction category")
    pending: bool = Field(description="Pending transaction status")


class AccountBalance(BaseModel):
    account_id: str = Field(description="Account identifier")
    name: str = Field(description="Account name")
    mask: str = Field(description="Account mask (last 4 digits)")
    type: str = Field(description="Account type (depository, credit, loan, investment)")
    available: float | None = Field(default=None, description="Available balance")
    current: float | None = Field(default=None, description="Current balance")
    iso_currency_code: str = Field(default="USD", description="Currency code")
    timestamp: str | None = Field(
        default=None, description="Cache timestamp (ISO format)"
    )
