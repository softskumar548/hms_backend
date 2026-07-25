
from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class LabCatalogCreate(BaseModel):
    id: str = Field(min_length=1)
    test_code: str = Field(min_length=1)  # LOINC
    name: str = Field(min_length=1)
    specimen_requirements: Optional[str] = None
    preparation_requirements: Optional[str] = None


class LabCatalogOut(BaseModel):
    id: str
    test_code: str
    name: str
    specimen_requirements: Optional[str] = None
    preparation_requirements: Optional[str] = None


class LabOrderCreate(BaseModel):
    patient_id: UUID
    encounter_id: UUID
    priority: str = "routine"  # 'routine', 'urgent'
    test_ids: list[str] = Field(min_length=1)


class LabOrderOut(BaseModel):
    id: UUID
    patient_id: UUID
    practitioner_id: str
    encounter_id: UUID
    status: str
    priority: str
    created_at: datetime


class LabOrderDetailOut(BaseModel):
    id: UUID
    patient_id: UUID
    practitioner_id: str
    encounter_id: UUID
    status: str
    priority: str
    created_at: datetime
    tests: list[LabCatalogOut] = []


class LabResultIngest(BaseModel):
    order_id: Optional[UUID] = None
    patient_id: Optional[UUID] = None
    test_id: str = Field(min_length=1)
    value: float
    unit: str = Field(min_length=1)
    reference_range: Optional[str] = None
    is_abnormal: bool = False
    is_critical: bool = False


class LabResultOut(BaseModel):
    id: UUID
    order_id: Optional[UUID] = None
    patient_id: Optional[UUID] = None
    test_id: str
    value: float
    unit: str
    reference_range: Optional[str] = None
    is_abnormal: bool
    is_critical: bool
    resulted_at: datetime
    reviewed_at: Optional[datetime] = None
    reviewed_by: Optional[str] = None


class LabUnmatchedResultOut(BaseModel):
    id: UUID
    payload: dict[str, Any]
    status: str
    resolved_by: Optional[str] = None
    resolved_at: Optional[datetime] = None


class UnmatchedResultResolve(BaseModel):
    order_id: UUID
    patient_id: UUID


class ClinicianInboxItemOut(BaseModel):
    result_id: UUID
    patient_id: UUID
    patient_name: str
    test_id: str
    test_name: str
    value: float
    unit: str
    is_abnormal: bool
    is_critical: bool
    resulted_at: datetime


class AnalyteTrendItem(BaseModel):
    resulted_at: datetime
    value: float
    unit: str


class AnalyteTrendOut(BaseModel):
    test_id: str
    test_name: str
    test_code: str
    history: list[AnalyteTrendItem] = []
