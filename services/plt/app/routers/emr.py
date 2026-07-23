from __future__ import annotations

from datetime import date
import json
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from hms_audit import record as audit_record
from hms_auth import auth
from hms_events import publish as event_publish
from hms_tenancy import RequestContext, tenant_session

from ..db import get_session
from .emr_schemas import (
    AllergyIntoleranceCreate,
    AllergyIntoleranceOut,
    ClinicalNoteAddendumCreate,
    ClinicalNoteAddendumOut,
    ClinicalNoteOut,
    ClinicalNoteSave,
    ConditionCreate,
    ConditionOut,
    EncounterCreate,
    EncounterOut,
    MedicationStatementCreate,
    MedicationStatementOut,
    PatientSummaryOut,
    VitalSignCreate,
    VitalSignOut
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/emr", tags=["emr"])


@router.post("/encounters", response_model=EncounterOut, status_code=201)
async def create_encounter(
    body: EncounterCreate,
    request: Request,
    ctx: RequestContext = Depends(auth),
    session: AsyncSession = Depends(get_session)
):
    """Create a new clinical encounter from a checked-in appointment (EMR-001)."""
    ctx.require_role("admin", "physician", "nurse")
    async with tenant_session(session, ctx) as s:
        # Create encounter record
        row = (
            await s.execute(
                text(
                    "INSERT INTO encounter (tenant_id, appointment_id, patient_id, practitioner_id, site_id, status) "
                    "VALUES (:tid, :app_id, :patient_id, :practitioner_id, :site_id, 'open') "
                    "RETURNING id, appointment_id, patient_id, practitioner_id, site_id, status, created_at, updated_at, signed_at, signed_by"
                ).bindparams(
                    tid=ctx.tenant_id, app_id=body.appointment_id, patient_id=body.patient_id,
                    practitioner_id=body.practitioner_id, site_id=body.site_id
                )
            )
        ).mappings().one()

        encounter_id = row["id"]

        # Audit check (PLT-005)
        await audit_record(
            s, ctx, action="create", resource_type="Encounter",
            resource_id=str(encounter_id), patient_id=str(body.patient_id),
            source_ip=request.client.host if request.client else None
        )
        await s.commit()

    return EncounterOut(**row)


@router.put("/encounters/{encounter_id}/notes", response_model=ClinicalNoteOut)
async def save_clinical_note(
    encounter_id: UUID,
    body: ClinicalNoteSave,
    request: Request,
    ctx: RequestContext = Depends(auth),
    session: AsyncSession = Depends(get_session)
):
    """Save/autosave note drafts (EMR-002), blocking edits if signed (EMR-003)."""
    ctx.require_role("admin", "physician", "nurse")
    async with tenant_session(session, ctx) as s:
        # Fetch encounter details
        enc = (
            await s.execute(
                text("SELECT patient_id, status FROM encounter WHERE id = :id").bindparams(id=encounter_id)
            )
        ).mappings().one_or_none()

        if not enc:
            raise HTTPException(status_code=404, detail="Encounter not found")
        if enc["status"] in ("signed", "amended"):
            raise HTTPException(status_code=400, detail="Encounter is finalized/signed and notes are immutable.")

        # Check existing note
        note = (
            await s.execute(
                text("SELECT id, version FROM clinical_note WHERE encounter_id = :enc_id").bindparams(enc_id=encounter_id)
            )
        ).mappings().one_or_none()

        if not note:
            # Insert note
            row = (
                await s.execute(
                    text(
                        "INSERT INTO clinical_note (tenant_id, encounter_id, template_type, structured_content, rich_text_content, version) "
                        "VALUES (:tid, :enc_id, :template, :structured, :rich, 1) "
                        "RETURNING id, encounter_id, template_type, structured_content, rich_text_content, version"
                    ).bindparams(
                        tid=ctx.tenant_id, enc_id=encounter_id, template=body.template_type,
                        structured=json.dumps(body.structured_content) if body.structured_content else None,
                        rich=body.rich_text_content
                    )
                )
            ).mappings().one()
        else:
            # Update note and increment version
            new_ver = note["version"] + 1
            row = (
                await s.execute(
                    text(
                        "UPDATE clinical_note SET template_type = :template, structured_content = :structured, "
                        "rich_text_content = :rich, version = :ver "
                        "WHERE id = :id "
                        "RETURNING id, encounter_id, template_type, structured_content, rich_text_content, version"
                    ).bindparams(
                        template=body.template_type,
                        structured=json.dumps(body.structured_content) if body.structured_content else None,
                        rich=body.rich_text_content, ver=new_ver, id=note["id"]
                    )
                )
            ).mappings().one()

        await audit_record(
            s, ctx, action="update", resource_type="ClinicalNote",
            resource_id=str(row["id"]), patient_id=str(enc["patient_id"]),
            source_ip=request.client.host if request.client else None
        )
        await s.commit()

    return ClinicalNoteOut(**row)


@router.post("/encounters/{encounter_id}/sign-off", response_model=EncounterOut)
async def sign_off_encounter(
    encounter_id: UUID,
    request: Request,
    ctx: RequestContext = Depends(auth),
    session: AsyncSession = Depends(get_session)
):
    """Finalize EMR records, enforcing allergy and diagnosis rules (EMR-003, EMR-005, EMR-008)."""
    ctx.require_role("admin", "physician")
    async with tenant_session(session, ctx) as s:
        # Fetch encounter details
        enc = (
            await s.execute(
                text("SELECT patient_id, status FROM encounter WHERE id = :id").bindparams(id=encounter_id)
            )
        ).mappings().one_or_none()

        if not enc:
            raise HTTPException(status_code=404, detail="Encounter not found")
        if enc["status"] in ("signed", "amended"):
            raise HTTPException(status_code=400, detail="Encounter is already finalized.")

        patient_id = enc["patient_id"]

        # 1. Enforce Allergy Assertion check (EMR-005)
        allergy_count = (
            await s.execute(
                text("SELECT count(*) as val FROM allergy_intolerance WHERE patient_id = :pid").bindparams(pid=patient_id)
            )
        ).mappings().one()["val"]

        if allergy_count == 0:
            raise HTTPException(
                status_code=400,
                detail="Allergy status must be explicitly asserted (active list or 'no known allergies') before signing off."
            )

        # 2. Enforce Coded Diagnosis check (EMR-008)
        diagnosis_count = (
            await s.execute(
                text(
                    "SELECT count(*) as val FROM condition WHERE patient_id = :pid "
                    "AND clinical_status = 'active'"
                ).bindparams(pid=patient_id)
            )
        ).mappings().one()["val"]

        if diagnosis_count == 0:
            raise HTTPException(
                status_code=400,
                detail="At least one active coded diagnosis (ICD-10) is required before final sign-off."
            )

        # Update status to signed
        row = (
            await s.execute(
                text(
                    "UPDATE encounter SET status = 'signed', signed_at = now(), signed_by = :sb, updated_at = now() "
                    "WHERE id = :id "
                    "RETURNING id, appointment_id, patient_id, practitioner_id, site_id, status, created_at, updated_at, signed_at, signed_by"
                ).bindparams(id=encounter_id, sb=ctx.user_id)
            )
        ).mappings().one()

        await audit_record(
            s, ctx, action="update", resource_type="Encounter",
            resource_id=str(encounter_id), patient_id=str(patient_id),
            source_ip=request.client.host if request.client else None,
            context_note="Encounter signed off"
        )
        await s.commit()

    # Emit event (EMR-012)
    await event_publish("encounter.signed", {
        "id": str(encounter_id),
        "tenant_id": ctx.tenant_id,
        "patient_id": str(patient_id),
        "signed_by": ctx.user_id
    })

    return EncounterOut(**row)


@router.post("/encounters/{encounter_id}/addenda", response_model=ClinicalNoteAddendumOut, status_code=201)
async def add_encounter_addendum(
    encounter_id: UUID,
    body: ClinicalNoteAddendumCreate,
    request: Request,
    ctx: RequestContext = Depends(auth),
    session: AsyncSession = Depends(get_session)
):
    """Appends comments/addenda to signed clinical notes (EMR-003)."""
    ctx.require_role("admin", "physician")
    async with tenant_session(session, ctx) as s:
        # Fetch encounter status
        enc = (
            await s.execute(
                text("SELECT patient_id, status FROM encounter WHERE id = :id").bindparams(id=encounter_id)
            )
        ).mappings().one_or_none()

        if not enc:
            raise HTTPException(status_code=404, detail="Encounter not found")
        if enc["status"] not in ("signed", "amended"):
            raise HTTPException(status_code=400, detail="Addenda can only be appended to signed/finalized encounters.")

        # Insert addendum
        row = (
            await s.execute(
                text(
                    "INSERT INTO clinical_note_addendum (tenant_id, encounter_id, author_id, content) "
                    "VALUES (:tid, :enc_id, :auth, :content) "
                    "RETURNING id, encounter_id, author_id, content, created_at"
                ).bindparams(tid=ctx.tenant_id, enc_id=encounter_id, auth=ctx.user_id, content=body.content)
            )
        ).mappings().one()

        # Mark status as amended if not already
        if enc["status"] != "amended":
            await s.execute(
                text("UPDATE encounter SET status = 'amended', updated_at = now() WHERE id = :id").bindparams(id=encounter_id)
            )

        await audit_record(
            s, ctx, action="create", resource_type="ClinicalNoteAddendum",
            resource_id=str(row["id"]), patient_id=str(enc["patient_id"]),
            source_ip=request.client.host if request.client else None
        )
        await s.commit()

    return ClinicalNoteAddendumOut(**row)


# --- Patient Reconciliation Masters ---

@router.post("/patients/{patient_id}/allergies", response_model=AllergyIntoleranceOut, status_code=201)
async def assert_allergy(
    patient_id: UUID,
    body: AllergyIntoleranceCreate,
    request: Request,
    ctx: RequestContext = Depends(auth),
    session: AsyncSession = Depends(get_session)
):
    """Enables clinical allergy tracking and 'no known allergies' status assertion (EMR-005)."""
    ctx.require_role("admin", "physician", "nurse")
    async with tenant_session(session, ctx) as s:
        row = (
            await s.execute(
                text(
                    "INSERT INTO allergy_intolerance (tenant_id, patient_id, substance_code, substance_display, reaction, severity, criticality, is_no_known, asserted_by) "
                    "VALUES (:tid, :pid, :code, :display, :reaction, :severity, :criticality, :no_known, :sb) "
                    "RETURNING id, patient_id, substance_code, substance_display, reaction, severity, criticality, is_no_known, asserted_at, asserted_by"
                ).bindparams(
                    tid=ctx.tenant_id, pid=patient_id, code=body.substance_code, display=body.substance_display,
                    reaction=body.reaction, severity=body.severity, criticality=body.criticality, no_known=body.is_no_known, sb=ctx.user_id
                )
            )
        ).mappings().one()

        await audit_record(
            s, ctx, action="create", resource_type="AllergyIntolerance",
            resource_id=str(row["id"]), patient_id=str(patient_id),
            source_ip=request.client.host if request.client else None
        )
        await s.commit()

    return AllergyIntoleranceOut(**row)


@router.post("/patients/{patient_id}/problems", response_model=ConditionOut, status_code=201)
async def create_problem(
    patient_id: UUID,
    body: ConditionCreate,
    request: Request,
    ctx: RequestContext = Depends(auth),
    session: AsyncSession = Depends(get_session)
):
    """Maintain active/resolved condition histories (EMR-004)."""
    ctx.require_role("admin", "physician")
    async with tenant_session(session, ctx) as s:
        row = (
            await s.execute(
                text(
                    "INSERT INTO condition (tenant_id, patient_id, clinical_status, code, display, onset_date, resolution_date) "
                    "VALUES (:tid, :pid, :status, :code, :display, :onset, :resolution) "
                    "RETURNING id, patient_id, clinical_status, code, display, onset_date, resolution_date, asserted_at"
                ).bindparams(
                    tid=ctx.tenant_id, pid=patient_id, status=body.clinical_status, code=body.code,
                    display=body.display, onset=body.onset_date, resolution=body.resolution_date
                )
            )
        ).mappings().one()

        await audit_record(
            s, ctx, action="create", resource_type="Condition",
            resource_id=str(row["id"]), patient_id=str(patient_id),
            source_ip=request.client.host if request.client else None
        )
        await s.commit()

    return ConditionOut(**row)


@router.post("/patients/{patient_id}/medications", response_model=MedicationStatementOut, status_code=201)
async def create_medication_statement(
    patient_id: UUID,
    body: MedicationStatementCreate,
    request: Request,
    ctx: RequestContext = Depends(auth),
    session: AsyncSession = Depends(get_session)
):
    """Maintain current reconciled medication list (EMR-006)."""
    ctx.require_role("admin", "physician", "nurse")
    async with tenant_session(session, ctx) as s:
        row = (
            await s.execute(
                text(
                    "INSERT INTO medication_statement (tenant_id, patient_id, status, medication_code, medication_display, sig) "
                    "VALUES (:tid, :pid, :status, :code, :display, :sig) "
                    "RETURNING id, patient_id, status, medication_code, medication_display, sig, asserted_at"
                ).bindparams(
                    tid=ctx.tenant_id, pid=patient_id, status=body.status, code=body.medication_code,
                    display=body.medication_display, sig=body.sig
                )
            )
        ).mappings().one()

        await audit_record(
            s, ctx, action="create", resource_type="MedicationStatement",
            resource_id=str(row["id"]), patient_id=str(patient_id),
            source_ip=request.client.host if request.client else None
        )
        await s.commit()

    return MedicationStatementOut(**row)


# --- Encounter Vitals Capture ---

@router.post("/encounters/{encounter_id}/vitals", response_model=VitalSignOut, status_code=201)
async def record_vital_sign(
    encounter_id: UUID,
    body: VitalSignCreate,
    request: Request,
    ctx: RequestContext = Depends(auth),
    session: AsyncSession = Depends(get_session)
):
    """Capture vital signs with basic unit checks (EMR-007)."""
    ctx.require_role("admin", "physician", "nurse")
    async with tenant_session(session, ctx) as s:
        # Fetch encounter patient
        enc = (
            await s.execute(
                text("SELECT patient_id, status FROM encounter WHERE id = :id").bindparams(id=encounter_id)
            )
        ).mappings().one_or_none()

        if not enc:
            raise HTTPException(status_code=404, detail="Encounter not found")
        if enc["status"] in ("signed", "amended"):
            raise HTTPException(status_code=400, detail="Encounter is signed and vitals are immutable.")

        patient_id = enc["patient_id"]

        # Basic range validation boundaries
        val = body.value
        t = body.type.lower()
        if t == "spo2" and (val < 0 or val > 100):
            raise HTTPException(status_code=422, detail="SpO2 percentage must be between 0 and 100.")
        elif t == "temperature" and (val < 25 or val > 45):
            raise HTTPException(status_code=422, detail="Body temperature in Celsius must be within reasonable physiological limits (25-45).")

        row = (
            await s.execute(
                text(
                    "INSERT INTO vital_sign (tenant_id, encounter_id, patient_id, type, value, unit) "
                    "VALUES (:tid, :enc_id, :pid, :type, :value, :unit) "
                    "RETURNING id, encounter_id, patient_id, type, value, unit, recorded_at"
                ).bindparams(
                    tid=ctx.tenant_id, enc_id=encounter_id, pid=patient_id,
                    type=t, value=val, unit=body.unit
                )
            )
        ).mappings().one()

        await audit_record(
            s, ctx, action="create", resource_type="VitalSign",
            resource_id=str(row["id"]), patient_id=str(patient_id),
            source_ip=request.client.host if request.client else None
        )
        await s.commit()

    return VitalSignOut(**row)


# --- Patient EMR Summary Timeline ---

@router.get("/patients/{patient_id}/summary", response_model=PatientSummaryOut)
async def get_patient_summary(
    patient_id: UUID,
    request: Request,
    ctx: RequestContext = Depends(auth),
    session: AsyncSession = Depends(get_session)
):
    """Retrieve demographics, allergies, problems, medications and vitals timeline summaries (EMR-010)."""
    async with tenant_session(session, ctx) as s:
        # 1. Fetch Patient Demographics
        demo = (
            await s.execute(
                text(
                    "SELECT id, given_name, family_name, dob, phone, gender, email, preferred_language, address "
                    "FROM patient WHERE id = CAST(:id AS uuid)"
                ).bindparams(id=patient_id)
            )
        ).mappings().one_or_none()

        if not demo:
            raise HTTPException(status_code=404, detail="Patient not found")

        # 2. Fetch active Allergies
        allergies = (
            await s.execute(
                text(
                    "SELECT id, patient_id, substance_code, substance_display, reaction, severity, criticality, is_no_known, asserted_at, asserted_by "
                    "FROM allergy_intolerance WHERE patient_id = CAST(:pid AS uuid) ORDER BY asserted_at DESC"
                ).bindparams(pid=patient_id)
            )
        ).mappings().all()

        # 3. Fetch active Problems
        problems = (
            await s.execute(
                text(
                    "SELECT id, patient_id, clinical_status, code, display, onset_date, resolution_date, asserted_at "
                    "FROM condition WHERE patient_id = CAST(:pid AS uuid) ORDER BY asserted_at DESC"
                ).bindparams(pid=patient_id)
            )
        ).mappings().all()

        # 4. Fetch Medications List
        meds = (
            await s.execute(
                text(
                    "SELECT id, patient_id, status, medication_code, medication_display, sig, asserted_at "
                    "FROM medication_statement WHERE patient_id = CAST(:pid AS uuid) ORDER BY asserted_at DESC"
                ).bindparams(pid=patient_id)
            )
        ).mappings().all()

        # 5. Fetch Vitals History
        vitals = (
            await s.execute(
                text(
                    "SELECT id, encounter_id, patient_id, type, value, unit, recorded_at "
                    "FROM vital_sign WHERE patient_id = CAST(:pid AS uuid) ORDER BY recorded_at DESC LIMIT 50"
                ).bindparams(pid=patient_id)
            )
        ).mappings().all()

        # 6. Fetch Encounters
        encs = (
            await s.execute(
                text(
                    "SELECT id, appointment_id, patient_id, practitioner_id, site_id, status, created_at, updated_at, signed_at, signed_by "
                    "FROM encounter WHERE patient_id = CAST(:pid AS uuid) ORDER BY created_at DESC"
                ).bindparams(pid=patient_id)
            )
        ).mappings().all()

        # EMR export/summary audit record
        await audit_record(
            s, ctx, action="read", resource_type="PatientSummary",
            patient_id=str(patient_id),
            source_ip=request.client.host if request.client else None,
            context_note="Patient EMR Summary view"
        )
        await s.commit()

    # Convert date values to strings or models
    demo_dict = dict(demo)
    if isinstance(demo_dict.get("dob"), date):
        demo_dict["dob"] = demo_dict["dob"].isoformat()

    return PatientSummaryOut(
        demographics=demo_dict,
        allergies=[AllergyIntoleranceOut(**a) for a in allergies],
        problems=[ConditionOut(**p) for p in problems],
        medications=[MedicationStatementOut(**m) for m in meds],
        recent_vitals=[VitalSignOut(**v) for v in vitals],
        encounters=[EncounterOut(**e) for e in encs]
    )
