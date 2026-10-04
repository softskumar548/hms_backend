
from datetime import date, datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


class ChargeMasterCreate(BaseModel):
    id: str = Field(min_length=1)
    code: str = Field(min_length=1)
    name: str = Field(min_length=1)
    category: str = Field(min_length=1)
    standard_price: float = Field(ge=0)
    tax_percent: float = Field(ge=0, default=0.0)
    active: bool = True


class ChargeMasterOut(BaseModel):
    id: str
    code: str
    name: str
    category: str
    standard_price: float
    tax_percent: float
    active: bool


class PatientCoverageCreate(BaseModel):
    patient_id: UUID | str
    scheme_type: str = Field(min_length=1)  # 'aarogyasri', 'pmjay', 'private'
    plan_name: str = Field(min_length=1)
    member_id: str = Field(min_length=1)
    validity_start: date
    validity_end: date
    patient_share_percent: float = Field(ge=0, le=100)


class PatientCoverageOut(BaseModel):
    id: UUID | str
    patient_id: UUID | str
    scheme_type: str
    plan_name: str
    member_id: str
    validity_start: date
    validity_end: date
    patient_share_percent: float
    created_at: datetime


class InvoiceCreate(BaseModel):
    patient_id: UUID | str
    encounter_id: Optional[UUID | str] = None
    coverage_id: Optional[UUID | str] = None


class InvoiceLineCreate(BaseModel):
    charge_item_id: str = Field(min_length=1)
    quantity: int = Field(gt=0, default=1)
    discount_amount: float = Field(ge=0, default=0.0)


class InvoiceLineOut(BaseModel):
    id: UUID | str
    charge_item_id: str
    quantity: int
    unit_price: float
    tax_amount: float
    discount_amount: float
    patient_share: float
    payer_share: float


class InvoiceOut(BaseModel):
    id: UUID | str
    patient_id: UUID | str
    encounter_id: Optional[UUID | str] = None
    status: str
    coverage_id: Optional[UUID | str] = None
    total_amount: float
    payer_responsibility: float
    patient_responsibility: float
    created_at: datetime
    updated_at: Optional[datetime] = None


class InvoiceDetailOut(BaseModel):
    id: UUID | str
    patient_id: UUID | str
    encounter_id: Optional[UUID | str] = None
    status: str
    coverage_id: Optional[UUID | str] = None
    total_amount: float
    payer_responsibility: float
    patient_responsibility: float
    items: list[InvoiceLineOut] = []
    created_at: datetime
    updated_at: Optional[datetime] = None


class PaymentCreate(BaseModel):
    invoice_id: UUID | str
    payment_method: str = Field(min_length=1)  # 'cash', 'card', 'insurance_remittance'
    amount: float = Field(gt=0)
    transaction_reference: Optional[str] = None


class PaymentOut(BaseModel):
    id: UUID | str
    invoice_id: UUID | str
    payment_method: str
    amount: float
    transaction_reference: Optional[str] = None
    received_at: datetime


class ClaimCreate(BaseModel):
    invoice_id: UUID | str
    coverage_id: UUID | str


class ClaimOut(BaseModel):
    id: UUID | str
    invoice_id: UUID | str
    coverage_id: UUID | str
    status: str
    total_claimed: float
    submitted_at: Optional[datetime] = None
    updated_at: datetime
