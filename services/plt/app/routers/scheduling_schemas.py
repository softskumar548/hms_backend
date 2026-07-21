from __future__ import annotations

from datetime import datetime, time
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


class SiteCreate(BaseModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    address: Optional[str] = None


class SiteOut(BaseModel):
    id: str
    name: str
    address: Optional[str] = None


class RoomCreate(BaseModel):
    id: str = Field(min_length=1)
    site_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    type: Optional[str] = None


class RoomOut(BaseModel):
    id: str
    site_id: str
    name: str
    type: Optional[str] = None


class ServiceCreate(BaseModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    duration_minutes: int = Field(gt=0)


class ServiceOut(BaseModel):
    id: str
    name: str
    duration_minutes: int


class PractitionerCreate(BaseModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    specialism: Optional[str] = None


class PractitionerOut(BaseModel):
    id: str
    name: str
    specialism: Optional[str] = None


class AvailabilityCreate(BaseModel):
    practitioner_id: str = Field(min_length=1)
    site_id: str = Field(min_length=1)
    day_of_week: int = Field(ge=0, le=6)  # 0: Sunday, 6: Saturday
    start_time: time
    end_time: time


class AvailabilityOut(BaseModel):
    id: UUID
    practitioner_id: str
    site_id: str
    day_of_week: int
    start_time: time
    end_time: time


class PrerequisiteDefinitionCreate(BaseModel):
    id: str = Field(min_length=1)
    code: str = Field(min_length=1)
    description: str = Field(min_length=1)
    enforcement_type: str = Field(default="advisory")  # 'hard-stop' or 'advisory'


class PrerequisiteDefinitionOut(BaseModel):
    id: str
    code: str
    description: str
    enforcement_type: str


class AppointmentCreate(BaseModel):
    patient_id: UUID
    practitioner_id: str = Field(min_length=1)
    site_id: str = Field(min_length=1)
    room_id: str = Field(min_length=1)
    service_id: str = Field(min_length=1)
    start_time: datetime
    end_time: datetime
    referred_by_id: Optional[str] = None
    referred_by_name: Optional[str] = None
    prerequisites: Optional[list[str]] = None  # List of prerequisite IDs to bind


class AppointmentPrerequisiteOut(BaseModel):
    prerequisite_id: str
    satisfied: bool
    satisfied_at: Optional[datetime] = None
    satisfied_by: Optional[str] = None
    code: str
    description: str
    enforcement_type: str


class AppointmentOut(BaseModel):
    id: UUID
    patient_id: UUID
    practitioner_id: str
    site_id: str
    room_id: str
    service_id: str
    status: str
    start_time: datetime
    end_time: datetime
    referred_by_id: Optional[str] = None
    referred_by_name: Optional[str] = None
    dues_warning: Optional[str] = None


class AppointmentDetailOut(BaseModel):
    id: UUID
    patient_id: UUID
    patient_name: str
    practitioner_id: str
    practitioner_name: str
    site_id: str
    site_name: str
    room_id: str
    room_name: str
    service_id: str
    service_name: str
    status: str
    start_time: datetime
    end_time: datetime
    referred_by_id: Optional[str] = None
    referred_by_name: Optional[str] = None
    prerequisites: list[AppointmentPrerequisiteOut] = []
    dues_warning: Optional[str] = None


class QueueItemOut(BaseModel):
    appointment_id: UUID
    patient_id: UUID
    patient_name: str
    status: str
    start_time: datetime
    service_name: str
    practitioner_name: str
    site_name: str
