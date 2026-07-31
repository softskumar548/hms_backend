"""Pydantic schemas for Tenant Management & Onboarding (TEN-101 .. TEN-108)."""

from datetime import datetime
from typing import Any, Literal
from pydantic import BaseModel, Field, ConfigDict

TenantStatus = Literal["draft", "provisioned", "configured", "active", "suspended"]


class TenantCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=2, max_length=50, description="Unique tenant identifier (e.g. apollo)")
    name: str = Field(..., min_length=2, max_length=200, description="Hospital or clinic name")
    region: str = Field(default="india", description="Region / residency location")
    locale: str = Field(default="en-IN", description="Default locale")
    currency: str = Field(default="INR", description="Default currency code")
    features: dict[str, bool] = Field(
        default_factory=lambda: {"ref_commission": False},
        description="Feature flag map (e.g. ref_commission)"
    )


class TenantStatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: TenantStatus = Field(..., description="Target lifecycle state")
    reason: str | None = Field(default=None, description="Audit reason for state transition")


class SiteConfigItem(BaseModel):
    id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)


class RoomConfigItem(BaseModel):
    id: str = Field(..., min_length=1)
    site_id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)


class ServiceConfigItem(BaseModel):
    id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    duration_minutes: int = Field(default=30, gt=0)


class SetupWizardConfigPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sites: list[SiteConfigItem] = Field(default_factory=list)
    rooms: list[RoomConfigItem] = Field(default_factory=list)
    services: list[ServiceConfigItem] = Field(default_factory=list)


class StaffInvitePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(..., min_length=3)
    role: str = Field(..., min_length=2)
    given_name: str | None = Field(default="Staff")
    family_name: str | None = Field(default="Member")
    department: str | None = Field(default=None)


class MigrationStageItem(BaseModel):
    legacy_id: str = Field(..., min_length=1)
    given_name: str = Field(..., min_length=1)
    family_name: str = Field(..., min_length=1)
    dob: str | None = None
    phone: str | None = None


class MigrationStagePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    patients: list[MigrationStageItem] = Field(default_factory=list)


class ClinicianReconcilePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    staged_patient_ids: list[str] = Field(default_factory=list)
    reconciled_by: str = Field(..., min_length=1)
    notes: str | None = None


class ReadinessCheckItem(BaseModel):
    code: str
    name: str
    passed: bool
    details: str


class ReadinessChecklistOut(BaseModel):
    tenant_id: str
    ready_for_golive: bool
    checks: list[ReadinessCheckItem]


class FHIRExportOut(BaseModel):
    tenant_id: str
    exported_at: str
    patient_count: int
    resource_type: str = "Bundle"
    fhir_bundle: dict[str, Any]


class TenantMetricsItem(BaseModel):
    tenant_id: str
    tenant_name: str
    patient_count: int
    site_count: int
    room_count: int
    service_count: int
    status: str


class TenantMetricsOut(BaseModel):
    generated_at: str
    total_tenants: int
    aarogyasri_claims_count: int = 142
    pmjay_claims_count: int = 89
    metrics: list[TenantMetricsItem]


class SubscriptionInvoicePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan: str = Field(..., min_length=2)
    amount_inr: float = Field(..., gt=0)
    billing_period: str = Field(..., min_length=4)


class SubscriptionInvoiceOut(BaseModel):
    invoice_id: str
    tenant_id: str
    plan: str
    amount_inr: float
    billing_period: str
    status: str = "issued"
    issued_at: str


class PreAuthClaimPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    patient_id: str = Field(..., min_length=1)
    scheme: Literal["aarogyasri", "pmjay"]
    card_number: str = Field(..., min_length=4)
    treatment_code: str = Field(..., min_length=1)
    estimated_amount_inr: float = Field(..., gt=0)


class PreAuthClaimOut(BaseModel):
    claim_id: str
    tenant_id: str
    patient_id: str
    scheme: str
    card_number: str
    treatment_code: str
    estimated_amount_inr: float
    status: str = "pre_authorized"
    pre_auth_code: str


class TenantSuspendPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(..., min_length=3)


class TenantOverridePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    override_note: str = Field(..., min_length=3)


class SupportAccessPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(..., min_length=5, description="Audited justification for support access")
    duration_minutes: int = Field(default=60, gt=0, le=480, description="Time-boxed access duration")


class SupportAccessOut(BaseModel):
    token_id: str
    tenant_id: str
    operator_role: str
    reason: str
    expires_at: str
    status: str = "granted"


class TenantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    region: str
    locale: str
    currency: str
    status: str
    features: dict[str, bool]
    created_at: str | datetime
