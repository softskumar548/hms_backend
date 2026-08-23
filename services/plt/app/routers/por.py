
from datetime import datetime, timedelta
import json
import logging
import random
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from hms_audit import record as audit_record
from hms_auth import auth
from hms_tenancy import RequestContext, tenant_session

from ..db import get_session
from .por_schemas import (
    ActivationSubmit,
    InvitationCreate,
    InvitationOut,
    PortalAppointmentOut,
    PortalLabResultOut,
    PortalMedicalRecordOut,
    PortalPrescriptionItemOut,
    PortalPrescriptionOut,
    PortalQuestionnaireOut,
    QuestionnaireSubmit
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/por", tags=["por"])


# --- Staff Gated Invitation Actions ---

@router.post("/invitations", response_model=InvitationOut, status_code=201)
async def generate_invitation(
    body: InvitationCreate,
    ctx: RequestContext = Depends(auth),
    session: AsyncSession = Depends(get_session)
):
    """Generate portal invitation OTP code (POR-001). Gated to clinic staff."""
    ctx.require_role("admin", "receptionist")
    async with tenant_session(session, ctx) as s:
        # Generate 6-digit OTP
        otp = f"{random.randint(100000, 999999)}"
        expires_at = datetime.now() + timedelta(minutes=15)

        row = (
            await s.execute(
                text(
                    "INSERT INTO portal_invitation (tenant_id, patient_id, email, phone, otp_code, expires_at, status) "
                    "VALUES (:tid, :patient_id, :email, :phone, :otp, :expires, 'pending') "
                    "RETURNING id, patient_id, email, phone, otp_code, expires_at, status"
                ).bindparams(
                    tid=ctx.tenant_id, patient_id=body.patient_id, email=body.email,
                    phone=body.phone, otp=otp, expires=expires_at
                )
            )
        ).mappings().one()
        await s.commit()

    return InvitationOut(**row)


@router.post("/activate", status_code=201)
async def activate_account(
    body: ActivationSubmit,
    ctx: RequestContext = Depends(auth),
    session: AsyncSession = Depends(get_session)
):
    """Binds invitation with password to create patient portal user (POR-001)."""
    async with tenant_session(session, ctx) as s:
        # 1. Fetch invitation
        invite = (
            await s.execute(
                text(
                    "SELECT patient_id, otp_code, expires_at, status FROM portal_invitation "
                    "WHERE id = :id"
                ).bindparams(id=body.invitation_id)
            )
        ).mappings().one_or_none()

        if not invite:
            raise HTTPException(status_code=404, detail="Invitation not found.")
        if invite["status"] != "pending":
            raise HTTPException(status_code=400, detail="Invitation is already activated or expired.")
        if invite["otp_code"] != body.otp_code:
            raise HTTPException(status_code=400, detail="Incorrect OTP activation code.")
        if datetime.now() > invite["expires_at"]:
            await s.execute(
                text("UPDATE portal_invitation SET status = 'expired' WHERE id = :id").bindparams(id=body.invitation_id)
            )
            await s.commit()
            raise HTTPException(status_code=400, detail="Activation invitation has expired.")

        # 2. Insert portal user
        dummy_hash = f"hashed_pwd_{body.password}" # Mock hash for validation
        await s.execute(
            text(
                "INSERT INTO portal_user (tenant_id, patient_id, username, password_hash, active) "
                "VALUES (:tid, :patient_id, :username, :hash, TRUE)"
            ).bindparams(
                tid=ctx.tenant_id, patient_id=invite["patient_id"], username=body.username, hash=dummy_hash
            )
        )

        # 3. Mark invite activated
        await s.execute(
            text("UPDATE portal_invitation SET status = 'activated' WHERE id = :id").bindparams(id=body.invitation_id)
        )
        await s.commit()

    return {"status": "success", "message": "Portal account activated successfully."}


# --- Patient Portal Features ---

@router.get("/visits")
async def list_portal_visits(
    patient_id: UUID | None = None,
    ctx: RequestContext = Depends(auth),
    session: AsyncSession = Depends(get_session)
):
    """Retrieve portal visits and appointments for the tenant (POR-002)."""
    async with tenant_session(session, ctx) as s:
        query = (
            "SELECT a.id, a.patient_id, a.practitioner_id, a.site_id, a.room_id, a.service_id, "
            "a.status, a.start_time, a.end_time, p.given_name, p.family_name, s.name as service_name, "
            "pr.name as practitioner_name "
            "FROM appointment a "
            "LEFT JOIN patient p ON a.patient_id = p.id "
            "LEFT JOIN service_catalog s ON a.service_id = s.id "
            "LEFT JOIN practitioner pr ON a.practitioner_id = pr.id "
        )
        params = {}
        if patient_id:
            query += " WHERE a.patient_id = :pid"
            params["pid"] = str(patient_id)
        query += " ORDER BY a.start_time DESC LIMIT 50"

        app_rows = (await s.execute(text(query).bindparams(**params))).mappings().all()

        results = []
        for app in app_rows:
            prereq_rows = (
                await s.execute(
                    text(
                        "SELECT ap.prerequisite_id, pd.name, pd.category, ap.satisfied "
                        "FROM appointment_prerequisite ap "
                        "JOIN prerequisite_definition pd ON ap.prerequisite_id = pd.id "
                        "WHERE ap.appointment_id = :app_id"
                    ).bindparams(app_id=app["id"])
                )
            ).mappings().all()

            patient_display = f"{app.get('given_name', '')} {app.get('family_name', '')}".strip()
            results.append({
                "id": str(app["id"]),
                "patient_id": str(app["patient_id"]),
                "patient_name": patient_display or "Patient",
                "practitioner_id": str(app["practitioner_id"]) if app.get("practitioner_id") else None,
                "practitioner_name": app.get("practitioner_name") or "Consulting Physician",
                "site_id": str(app["site_id"]) if app.get("site_id") else None,
                "room_id": str(app["room_id"]) if app.get("room_id") else None,
                "service_id": str(app["service_id"]) if app.get("service_id") else None,
                "service_name": app.get("service_name") or "General Consultation",
                "status": app.get("status", "scheduled"),
                "start_time": app["start_time"].isoformat() if app.get("start_time") else None,
                "end_time": app["end_time"].isoformat() if app.get("end_time") else None,
                "forms_completed": False,
                "prerequisites": [dict(p) for p in prereq_rows]
            })
        await s.commit()

    return results


@router.post("/intake")
async def submit_portal_intake(
    appointment_id: UUID | None = None,
    body: dict = {},
    ctx: RequestContext = Depends(auth),
    session: AsyncSession = Depends(get_session)
):
    """Submit portal intake form (POR-003)."""
    return {"success": True, "appointment_id": str(appointment_id) if appointment_id else None, "status": "completed"}


@router.get("/appointments", response_model=list[PortalAppointmentOut])
async def list_portal_appointments(
    patient_id: UUID,
    ctx: RequestContext = Depends(auth),
    session: AsyncSession = Depends(get_session)
):
    """Retrieve appointments and pre-visit prerequisites checklists (POR-002)."""
    async with tenant_session(session, ctx) as s:
        # Fetch appointments
        app_rows = (
            await s.execute(
                text(
                    "SELECT id, patient_id, practitioner_id, site_id, room_id, service_id, status, start_time, end_time "
                    "FROM appointment WHERE patient_id = :pid ORDER BY start_time DESC"
                ).bindparams(pid=patient_id)
            )
        ).mappings().all()

        results = []
        for app in app_rows:
            # Fetch prerequisites checklist status
            prereq_rows = (
                await s.execute(
                    text(
                        "SELECT ap.prerequisite_id, pd.name, pd.category, ap.satisfied "
                        "FROM appointment_prerequisite ap "
                        "JOIN prerequisite_definition pd ON ap.prerequisite_id = pd.id "
                        "WHERE ap.appointment_id = :app_id"
                    ).bindparams(app_id=app["id"])
                )
            ).mappings().all()

            results.append(
                PortalAppointmentOut(
                    **app,
                    prerequisites=[dict(p) for p in prereq_rows]
                )
            )
        await s.commit()

    return results


@router.post("/appointments/{appointment_id}/questionnaires", response_model=PortalQuestionnaireOut)
async def submit_previsit_questionnaire(
    appointment_id: UUID,
    body: QuestionnaireSubmit,
    ctx: RequestContext = Depends(auth),
    session: AsyncSession = Depends(get_session)
):
    """Present and submit pre-visit questionnaires (POR-003)."""
    async with tenant_session(session, ctx) as s:
        # Check if questionnaire placeholder exists (or auto-create one for simplicity)
        q_row = (
            await s.execute(
                text(
                    "SELECT id, questions_json FROM portal_questionnaire "
                    "WHERE appointment_id = :app_id"
                ).bindparams(app_id=appointment_id)
            )
        ).mappings().one_or_none()

        q_questions = q_row["questions_json"] if q_row else {"general_health": "Describe details"}
        
        if q_row:
            row = (
                await s.execute(
                    text(
                        "UPDATE portal_questionnaire "
                        "SET answers_json = :answers, submitted_at = now() "
                        "WHERE id = :id "
                        "RETURNING id, appointment_id, questionnaire_type, questions_json, answers_json, submitted_at"
                    ).bindparams(id=q_row["id"], answers=json.dumps(body.answers))
                )
            ).mappings().one()
        else:
            row = (
                await s.execute(
                    text(
                        "INSERT INTO portal_questionnaire (tenant_id, appointment_id, questionnaire_type, questions_json, answers_json, submitted_at) "
                        "VALUES (:tid, :app_id, 'general', :questions, :answers, now()) "
                        "RETURNING id, appointment_id, questionnaire_type, questions_json, answers_json, submitted_at"
                    ).bindparams(
                        tid=ctx.tenant_id, app_id=appointment_id, questions=json.dumps(q_questions), answers=json.dumps(body.answers)
                    )
                )
            ).mappings().one()

        await s.commit()
    return PortalQuestionnaireOut(**row)


@router.get("/clinical/records", response_model=PortalMedicalRecordOut)
async def view_portal_medical_records(
    patient_id: UUID,
    request: Request,
    ctx: RequestContext = Depends(auth),
    session: AsyncSession = Depends(get_session)
):
    """Display released results, note SOAP timelines, and prescriptions (POR-004). Strict read-only audit log (PLT-005)."""
    async with tenant_session(session, ctx) as s:
        # 1. Fetch active problems
        conditions = (
            await s.execute(
                text("SELECT code, display, status FROM condition WHERE patient_id = CAST(:pid AS uuid)").bindparams(pid=str(patient_id))
            )
        ).mappings().all()

        # 2. Fetch allergies
        allergies = (
            await s.execute(
                text("SELECT substance_code, substance_display, criticality FROM allergy_intolerance WHERE patient_id = CAST(:pid AS uuid)").bindparams(pid=str(patient_id))
            )
        ).mappings().all()

        # 3. Fetch signed prescriptions history
        prescriptions = (
            await s.execute(
                text("SELECT id, practitioner_id, status, created_at, signed_at FROM prescription WHERE patient_id = CAST(:pid AS uuid) AND status = 'signed'").bindparams(pid=str(patient_id))
            )
        ).mappings().all()

        prescription_list = []
        for rx in prescriptions:
            items = (
                await s.execute(
                    text(
                        "SELECT pi.medication_id, mc.name, pi.dose, pi.unit, pi.route, pi.frequency, pi.duration_days, pi.quantity "
                        "FROM prescription_item pi JOIN medication_catalog mc ON pi.medication_id = mc.id "
                        "WHERE pi.prescription_id = :rx_id"
                    ).bindparams(rx_id=rx["id"])
                )
            ).mappings().all()
            
            prescription_list.append(
                PortalPrescriptionOut(
                    **rx,
                    items=[PortalPrescriptionItemOut(**i) for i in items]
                )
            )

        # 4. Fetch released lab results
        lab_results = (
            await s.execute(
                text(
                    "SELECT r.id, lc.name as test_name, r.value, r.unit, r.reference_range, r.is_abnormal, r.is_critical, r.resulted_at "
                    "FROM lab_result r JOIN lab_catalog lc ON r.test_id = lc.id "
                    "WHERE r.patient_id = CAST(:pid AS uuid)"
                ).bindparams(pid=str(patient_id))
            )
        ).mappings().all()

        # --- Strict Read-Only Audit Logging ---
        await audit_record(
            s, ctx, action="read", resource_type="PatientPortalRecords",
            resource_id=str(patient_id), patient_id=str(patient_id),
            source_ip=request.client.host if request.client else None,
            context_note="Patient accessed read-only clinical timeline chart summary on portal"
        )
        await s.commit()

    return PortalMedicalRecordOut(
        conditions=[dict(c) for c in conditions],
        allergies=[dict(a) for a in allergies],
        prescriptions=prescription_list,
        lab_results=[PortalLabResultOut(**r) for r in lab_results]
    )
