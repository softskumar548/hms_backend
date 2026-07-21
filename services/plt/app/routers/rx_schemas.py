from __future__ import annotations

from datetime import date, datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


class MedicationCatalogCreate(BaseModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    generic_name: str = Field(min_length=1)
    form: str = Field(min_length=1)
    strength: str = Field(min_length=1)


class MedicationCatalogOut(BaseModel):
    id: str
    name: str
    generic_name: str
    form: str
    strength: str


class TenantFormularyCreate(BaseModel):
    medication_id: str = Field(min_length=1)
    active: bool = True


class TenantFormularyOut(BaseModel):
    id: UUID
    medication_id: str
    active: bool
    created_at: datetime


class PrescriptionItemCreate(BaseModel):
    medication_id: str = Field(min_length=1)
    dose: float = Field(gt=0)
    unit: str = Field(min_length=1)
    route: str = Field(min_length=1)
    frequency: str = Field(min_length=1)
    duration_days: int = Field(gt=0)
    prn: bool = False
    quantity: int = Field(gt=0)
    refills: int = Field(ge=0, default=0)
    free_text_sig: Optional[str] = None


class PrescriptionItemOut(BaseModel):
    id: UUID
    medication_id: str
    dose: float
    unit: str
    route: str
    frequency: str
    duration_days: int
    prn: bool
    quantity: int
    refills: int
    free_text_sig: Optional[str] = None


class PrescriptionCreate(BaseModel):
    patient_id: UUID
    encounter_id: UUID
    items: list[PrescriptionItemCreate]


class PrescriptionOut(BaseModel):
    id: UUID
    patient_id: UUID
    practitioner_id: str
    encounter_id: UUID
    status: str
    created_at: datetime
    updated_at: datetime
    signed_at: Optional[datetime] = None
    signed_by: Optional[str] = None


class PrescriptionDetailOut(BaseModel):
    id: UUID
    patient_id: UUID
    practitioner_id: str
    encounter_id: UUID
    status: str
    items: list[PrescriptionItemOut]
    created_at: datetime
    updated_at: datetime
    signed_at: Optional[datetime] = None
    signed_by: Optional[str] = None


class PrescriptionOverrideCreate(BaseModel):
    alert_type: str = Field(min_length=1)
    severity: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class PrescriptionOverrideOut(BaseModel):
    id: UUID
    prescription_id: UUID
    alert_type: str
    severity: str
    reason: str
    created_at: datetime


class PrescriptionSign(BaseModel):
    override_reason: Optional[str] = None
    follow_up_date: Optional[date] = None
    follow_up_service_id: Optional[str] = None
    follow_up_site_id: Optional[str] = None
    follow_up_prerequisites: Optional[list[str]] = None  # Prerequisite IDs bound to follow-up draft (Flag F1)


class FavoriteCreate(BaseModel):
    medication_id: str = Field(min_length=1)
    dose: Optional[float] = None
    unit: Optional[str] = None
    route: Optional[str] = None
    frequency: Optional[str] = None


class FavoriteOut(BaseModel):
    id: UUID
    practitioner_id: str
    medication_id: str
    dose: Optional[float] = None
    unit: Optional[str] = None
    route: Optional[str] = None
    frequency: Optional[str] = None
