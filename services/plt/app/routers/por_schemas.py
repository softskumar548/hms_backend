
from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class InvitationCreate(BaseModel):
    patient_id: UUID
    email: str = Field(min_length=1)
    phone: str = Field(min_length=1)


class InvitationOut(BaseModel):
    id: UUID
    patient_id: UUID
    email: str
    phone: str
    otp_code: str
    expires_at: datetime
    status: str


class ActivationSubmit(BaseModel):
    invitation_id: UUID
    otp_code: str = Field(min_length=1)
    username: str = Field(min_length=1)
    password: str = Field(min_length=6)


class AppointmentSelfBook(BaseModel):
    site_id: str = Field(min_length=1)
    room_id: str = Field(min_length=1)
    practitioner_id: str = Field(min_length=1)
    service_id: str = Field(min_length=1)
    start_time: datetime
    end_time: datetime


class QuestionnaireSubmit(BaseModel):
    answers: dict[str, Any] = Field(min_length=1)


class PortalQuestionnaireOut(BaseModel):
    id: UUID
    appointment_id: UUID
    questionnaire_type: str
    questions_json: dict[str, Any]
    answers_json: Optional[dict[str, Any]] = None
    submitted_at: Optional[datetime] = None


class PortalAppointmentOut(BaseModel):
    id: UUID
    patient_id: UUID
    practitioner_id: str
    site_id: str
    room_id: str
    service_id: str
    status: str
    start_time: datetime
    end_time: datetime
    prerequisites: list[dict[str, Any]] = []


class PortalPrescriptionItemOut(BaseModel):
    medication_id: str
    name: str
    dose: float
    unit: str
    route: str
    frequency: str
    duration_days: int
    quantity: int


class PortalPrescriptionOut(BaseModel):
    id: UUID
    practitioner_id: str
    status: str
    created_at: datetime
    signed_at: Optional[datetime] = None
    items: list[PortalPrescriptionItemOut] = []


class PortalLabResultOut(BaseModel):
    id: UUID
    test_name: str
    value: float
    unit: str
    reference_range: Optional[str] = None
    is_abnormal: bool
    is_critical: bool
    resulted_at: datetime


class PortalMedicalRecordOut(BaseModel):
    conditions: list[dict[str, Any]] = []
    allergies: list[dict[str, Any]] = []
    prescriptions: list[PortalPrescriptionOut] = []
    lab_results: list[PortalLabResultOut] = []


class PortalMessageCreate(BaseModel):
    message_text: str = Field(min_length=1)


class PortalMessageOut(BaseModel):
    id: UUID
    patient_id: UUID
    direction: str
    message_text: str
    read_at: Optional[datetime] = None
    created_at: datetime
