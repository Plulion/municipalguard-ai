from datetime import date
from pydantic import BaseModel, ConfigDict, Field

MAX_BIGINT = 9_223_372_036_854_775_807
MIN_BIGINT = -9_223_372_036_854_775_808


class BaseSchema(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)


class TransactionIn(BaseSchema):
    transaction_date: date
    transaction_type: str = Field(min_length=1, max_length=30)
    account_code: str = Field(default="", max_length=80)
    supplier_name: str = Field(default="", max_length=200)
    supplier_tax_id: str = Field(default="", max_length=40)
    invoice_number: str = Field(default="", max_length=100)
    description: str = Field(default="", max_length=2000)
    amount_clp: int = Field(ge=MIN_BIGINT, le=MAX_BIGINT)
    budget_amount_clp: int | None = Field(default=None, gt=0, le=MAX_BIGINT)


class StatusUpdate(BaseSchema):
    status: str = Field(min_length=1, max_length=30)
    reason: str = Field(min_length=3, max_length=1000)


class QuestionIn(BaseSchema):
    question: str = Field(min_length=3, max_length=1000)


class ProcurementIn(BaseSchema):
    external_id: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=500)
    publication_date: date | None = None
    closing_date: date | None = None
    status: str = Field(default="unknown", min_length=1, max_length=80)
    estimated_amount_clp: int | None = Field(default=None, ge=0, le=MAX_BIGINT)
    awarded_amount_clp: int | None = Field(default=None, ge=0, le=MAX_BIGINT)
    offers_count: int | None = Field(default=None, ge=0, le=1_000_000)
    awarded_supplier_tax_id: str = Field(default="", max_length=40)


class PurchaseOrderIn(BaseSchema):
    external_id: str = Field(min_length=1, max_length=100)
    supplier_tax_id: str = Field(default="", max_length=40)
    supplier_name: str = Field(default="", max_length=250)
    issue_date: date | None = None
    amount_clp: int = Field(gt=0, le=MAX_BIGINT)
    status: str = Field(default="unknown", min_length=1, max_length=80)
    procurement_external_id: str = Field(default="", max_length=100)
