"""Request/response models. Pydantic v2. These mirror a slice of the FHIR Patient
resource; the full FHIR mapping lives in the hms_fhir library (flag F2)."""

from datetime import date
import re
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class PatientCreate(BaseModel):
    given_name: str = Field(min_length=1)
    family_name: str = Field(min_length=1)
    dob: Optional[date] = None
    national_id: Optional[str] = None
    phone: Optional[str] = None
    
    # India specifics & expanded demographics
    abha_number: Optional[str] = None
    abha_address: Optional[str] = None
    aarogyasri_id: Optional[str] = None
    pmjay_id: Optional[str] = None
    aadhaar_last_four: Optional[str] = None
    
    referred_by_type: Optional[str] = None  # e.g., 'clinic', 'clinician'
    referred_by_name: Optional[str] = None
    referred_by_id: Optional[str] = None
    
    gender: Optional[str] = None
    email: Optional[str] = None
    preferred_language: Optional[str] = None
    address: Optional[dict] = None
    next_of_kin: Optional[dict] = None

    # Newborn (Neonate) Specifics
    is_newborn: Optional[bool] = False
    mother_patient_id: Optional[str] = None
    birth_time: Optional[str] = None  # e.g., "14:35" or "14:35:00"
    birth_weight_grams: Optional[int] = None  # e.g. 2950
    gestational_age_weeks: Optional[int] = None  # e.g. 38
    multiple_birth_order: Optional[int] = 1  # 1 for Single / Twin 1, 2 for Twin 2
    delivery_type: Optional[str] = None  # 'normal_vaginal', 'cesarean_lscs', 'assisted_vacuum'
    apgar_score_1min: Optional[int] = None
    apgar_score_5min: Optional[int] = None

    @field_validator("abha_number")
    @classmethod
    def validate_abha_number(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            cleaned = "".join(filter(str.isdigit, v))
            if len(cleaned) != 14:
                raise ValueError("ABHA number must be exactly 14 digits")
            return cleaned
        return v

    @field_validator("aadhaar_last_four")
    @classmethod
    def validate_aadhaar_last_four(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            cleaned = "".join(filter(str.isdigit, v))
            if len(cleaned) != 4:
                raise ValueError("Aadhaar last four digits must be exactly 4 digits")
            return cleaned
        return v

    @field_validator("phone")
    @classmethod
    def validate_phone_number(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            # Must start with optional + and have 10-15 digits
            if not re.match(r"^\+?[1-9]\d{9,14}$", v):
                raise ValueError("Phone number must be a valid format (e.g. +919999999999 or 9999999999)")
        return v

    @field_validator("email")
    @classmethod
    def validate_email_address(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            if not re.match(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$", v):
                raise ValueError("Email must be a valid email format")
        return v

    @model_validator(mode="after")
    def validate_referred_by(self) -> "PatientCreate":
        if self.referred_by_type and not self.referred_by_name:
            raise ValueError("referred_by_name is required if referred_by_type is set")
        if self.referred_by_name and not self.referred_by_type:
            raise ValueError("referred_by_type is required if referred_by_name is set")
        return self


class PatientOut(BaseModel):
    id: str
    given_name: str
    family_name: str
    dob: Optional[date] = None
    national_id: Optional[str] = None
    phone: Optional[str] = None
    
    # India specifics & expanded demographics
    abha_number: Optional[str] = None
    abha_address: Optional[str] = None
    aarogyasri_id: Optional[str] = None
    pmjay_id: Optional[str] = None
    aadhaar_last_four: Optional[str] = None
    
    referred_by_type: Optional[str] = None
    referred_by_name: Optional[str] = None
    referred_by_id: Optional[str] = None
    
    gender: Optional[str] = None
    email: Optional[str] = None
    preferred_language: Optional[str] = None
    address: Optional[dict] = None
    next_of_kin: Optional[dict] = None

    # Newborn (Neonate) Specifics
    is_newborn: Optional[bool] = False
    mother_patient_id: Optional[str] = None
    birth_time: Optional[str] = None
    birth_weight_grams: Optional[int] = None
    gestational_age_weeks: Optional[int] = None
    multiple_birth_order: Optional[int] = 1
    delivery_type: Optional[str] = None
    apgar_score_1min: Optional[int] = None
    apgar_score_5min: Optional[int] = None
    
    # Validated FHIR payload representation
    fhir_resource: Optional[dict] = None


