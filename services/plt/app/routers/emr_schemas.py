
from datetime import date, datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class EncounterCreate(BaseModel):
    appointment_id: Optional[UUID] = None
    patient_id: UUID
    practitioner_id: str = Field(min_length=1)
    site_id: str = Field(min_length=1)


class EncounterOut(BaseModel):
    id: UUID
    appointment_id: Optional[UUID] = None
    patient_id: UUID
    practitioner_id: str
    site_id: str
    status: str
    created_at: datetime
    updated_at: datetime
    signed_at: Optional[datetime] = None
    signed_by: Optional[str] = None


class ClinicalNoteSave(BaseModel):
    template_type: str = Field(min_length=1)
    structured_content: Optional[dict[str, Any]] = None
    rich_text_content: Optional[str] = None


class ClinicalNoteOut(BaseModel):
    id: UUID
    encounter_id: UUID
    template_type: str
    structured_content: Optional[dict[str, Any]] = None
    rich_text_content: Optional[str] = None
    version: int


class ClinicalNoteAddendumCreate(BaseModel):
    content: str = Field(min_length=1)


class ClinicalNoteAddendumOut(BaseModel):
    id: UUID
    encounter_id: UUID
    author_id: str
    content: str
    created_at: datetime


class AllergyIntoleranceCreate(BaseModel):
    substance_code: Optional[str] = None
    substance_display: Optional[str] = None
    reaction: Optional[str] = None
    severity: Optional[str] = None  # 'mild', 'moderate', 'severe'
    criticality: Optional[str] = None  # 'low', 'high', 'unable-to-assess'
    is_no_known: bool = False


class AllergyIntoleranceOut(BaseModel):
    id: UUID
    patient_id: UUID
    substance_code: Optional[str] = None
    substance_display: Optional[str] = None
    reaction: Optional[str] = None
    severity: Optional[str] = None
    criticality: Optional[str] = None
    is_no_known: bool
    asserted_at: datetime
    asserted_by: str


class ConditionCreate(BaseModel):
    clinical_status: str = "active"  # 'active', 'inactive', 'resolved'
    code: str = Field(min_length=1)  # ICD-10 or SNOMED
    display: str = Field(min_length=1)
    onset_date: Optional[date] = None
    resolution_date: Optional[date] = None


class ConditionOut(BaseModel):
    id: UUID
    patient_id: UUID
    clinical_status: str
    code: str
    display: str
    onset_date: Optional[date] = None
    resolution_date: Optional[date] = None
    asserted_at: datetime


class MedicationStatementCreate(BaseModel):
    status: str = "active"
    medication_code: str = Field(min_length=1)
    medication_display: str = Field(min_length=1)
    sig: Optional[str] = None


class MedicationStatementOut(BaseModel):
    id: UUID
    patient_id: UUID
    status: str
    medication_code: str
    medication_display: str
    sig: Optional[str] = None
    asserted_at: datetime


class VitalSignCreate(BaseModel):
    type: str = Field(min_length=1)  # 'height', 'weight', 'bp_systolic', 'bp_diastolic', 'heart_rate', 'temperature', 'spo2'
    value: float
    unit: str = Field(min_length=1)


class VitalSignOut(BaseModel):
    id: UUID
    encounter_id: UUID
    patient_id: UUID
    type: str
    value: float
    unit: str
    recorded_at: datetime


class PatientSummaryOut(BaseModel):
    demographics: dict[str, Any]
    allergies: list[AllergyIntoleranceOut]
    problems: list[ConditionOut]
    medications: list[MedicationStatementOut]
    recent_vitals: list[VitalSignOut]
    encounters: list[EncounterOut]
