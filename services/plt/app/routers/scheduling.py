from __future__ import annotations

from datetime import date, datetime
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from hms_audit import record as audit_record
from hms_auth import auth
from hms_events import publish as event_publish
from hms_tenancy import RequestContext, tenant_session

from ..db import get_session
from ..scheduling_service import (
    check_booking_conflicts,
    find_available_slots,
    verify_and_enforce_prerequisites
)
from .scheduling_schemas import (
    AppointmentCreate,
    AppointmentDetailOut,
    AppointmentOut,
    AppointmentPrerequisiteOut,
    AvailabilityCreate,
    AvailabilityOut,
    PrerequisiteDefinitionCreate,
    PrerequisiteDefinitionOut,
    PractitionerCreate,
    PractitionerOut,
    QueueItemOut,
    RoomCreate,
    RoomOut,
    ServiceCreate,
    ServiceOut,
    SiteCreate,
    SiteOut
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/scheduling", tags=["scheduling"])


# --- Masters Setup ---

@router.post("/sites", response_model=SiteOut, status_code=201)
async def create_site(
    body: SiteCreate,
    ctx: RequestContext = Depends(auth),
    session: AsyncSession = Depends(get_session)
):
    ctx.require_role("admin")
    async with tenant_session(session, ctx) as s:
        row = (
            await s.execute(
                text(
                    "INSERT INTO site (id, tenant_id, name, address) "
                    "VALUES (:id, :tid, :name, :address) "
                    "RETURNING id, name, address"
                ).bindparams(id=body.id, tid=ctx.tenant_id, name=body.name, address=body.address)
            )
        ).mappings().one()
        await s.commit()
    return SiteOut(**row)


@router.post("/rooms", response_model=RoomOut, status_code=201)
async def create_room(
    body: RoomCreate,
    ctx: RequestContext = Depends(auth),
    session: AsyncSession = Depends(get_session)
):
    ctx.require_role("admin")
    async with tenant_session(session, ctx) as s:
        row = (
            await s.execute(
                text(
                    "INSERT INTO room (id, site_id, tenant_id, name, type) "
                    "VALUES (:id, :site_id, :tid, :name, :type) "
                    "RETURNING id, site_id, name, type"
                ).bindparams(id=body.id, site_id=body.site_id, tid=ctx.tenant_id, name=body.name, type=body.type)
            )
        ).mappings().one()
        await s.commit()
    return RoomOut(**row)


@router.post("/services", response_model=ServiceOut, status_code=201)
async def create_service(
    body: ServiceCreate,
    ctx: RequestContext = Depends(auth),
    session: AsyncSession = Depends(get_session)
):
    ctx.require_role("admin")
    async with tenant_session(session, ctx) as s:
        row = (
            await s.execute(
                text(
                    "INSERT INTO service (id, tenant_id, name, duration_minutes) "
                    "VALUES (:id, :tid, :name, :duration) "
                    "RETURNING id, name, duration_minutes"
                ).bindparams(id=body.id, tid=ctx.tenant_id, name=body.name, duration=body.duration_minutes)
            )
        ).mappings().one()
        await s.commit()
    return ServiceOut(**row)


@router.post("/practitioners", response_model=PractitionerOut, status_code=201)
async def create_practitioner(
    body: PractitionerCreate,
    ctx: RequestContext = Depends(auth),
    session: AsyncSession = Depends(get_session)
):
    ctx.require_role("admin")
    async with tenant_session(session, ctx) as s:
        row = (
            await s.execute(
                text(
                    "INSERT INTO practitioner (id, tenant_id, name, specialism) "
                    "VALUES (:id, :tid, :name, :specialism) "
                    "RETURNING id, name, specialism"
                ).bindparams(id=body.id, tid=ctx.tenant_id, name=body.name, specialism=body.specialism)
            )
        ).mappings().one()
        await s.commit()
    return PractitionerOut(**row)


@router.post("/availabilities", response_model=AvailabilityOut, status_code=201)
async def create_availability(
    body: AvailabilityCreate,
    ctx: RequestContext = Depends(auth),
    session: AsyncSession = Depends(get_session)
):
    ctx.require_role("admin")
    async with tenant_session(session, ctx) as s:
        row = (
            await s.execute(
                text(
                    "INSERT INTO practitioner_availability (practitioner_id, site_id, tenant_id, day_of_week, start_time, end_time) "
                    "VALUES (:practitioner_id, :site_id, :tid, :day, :start, :end) "
                    "RETURNING id, practitioner_id, site_id, day_of_week, start_time, end_time"
                ).bindparams(
                    practitioner_id=body.practitioner_id, site_id=body.site_id,
                    tid=ctx.tenant_id, day=body.day_of_week, start=body.start_time, end=body.end_time
                )
            )
        ).mappings().one()
        await s.commit()
    return AvailabilityOut(**row)


@router.post("/prerequisites", response_model=PrerequisiteDefinitionOut, status_code=201)
async def create_prerequisite_definition(
    body: PrerequisiteDefinitionCreate,
    ctx: RequestContext = Depends(auth),
    session: AsyncSession = Depends(get_session)
):
    ctx.require_role("admin")
    async with tenant_session(session, ctx) as s:
        row = (
            await s.execute(
                text(
                    "INSERT INTO prerequisite_definition (id, tenant_id, code, description, enforcement_type) "
                    "VALUES (:id, :tid, :code, :description, :type) "
                    "RETURNING id, code, description, enforcement_type"
                ).bindparams(
                    id=body.id, tid=ctx.tenant_id, code=body.code,
                    description=body.description, type=body.enforcement_type
                )
            )
        ).mappings().one()
        await s.commit()
    return PrerequisiteDefinitionOut(**row)


# --- Core Scheduling Workflows ---

@router.get("/slots")
async def get_slots(
    practitioner_id: str,
    service_id: str,
    target_date: date,
    ctx: RequestContext = Depends(auth),
    session: AsyncSession = Depends(get_session)
):
    """Enpoints to find available clinical slots (SCH-004)."""
    async with tenant_session(session, ctx) as s:
        slots = await find_available_slots(s, practitioner_id, service_id, target_date)
        await s.commit()
    return slots


@router.post("/appointments", response_model=AppointmentOut, status_code=201)
async def book_appointment(
    body: AppointmentCreate,
    request: Request,
    ctx: RequestContext = Depends(auth),
    session: AsyncSession = Depends(get_session)
):
    """Book a new clinical appointment (SCH-002), executing conflict checks."""
    ctx.require_role("admin", "receptionist")
    logger.error(f"BOOKING BODY: {body.model_dump()}")
    async with tenant_session(session, ctx) as s:
        # Check Patient/Practitioner/Room overlaps
        await check_booking_conflicts(
            s, body.practitioner_id, body.room_id, body.patient_id, body.start_time, body.end_time
        )
        
        # Ensure practitioner exists if specified
        if body.practitioner_id:
            prac = (await s.execute(text("SELECT id FROM practitioner WHERE id = :id").bindparams(id=body.practitioner_id))).mappings().one_or_none()
            if not prac:
                raise HTTPException(status_code=404, detail=f"Practitioner '{body.practitioner_id}' not found")

        # Ensure patient exists
        pat = (await s.execute(text("SELECT id FROM patient WHERE id = CAST(:id AS uuid)").bindparams(id=str(body.patient_id)))).mappings().one_or_none()
        if not pat:
            raise HTTPException(status_code=404, detail=f"Patient '{body.patient_id}' not found")

        # Save appointment record (Initial state: BOOKED, or DRAFT if flagged)
        status = "BOOKED"
        
        row = (
            await s.execute(
                text(
                    "INSERT INTO appointment "
                    "(tenant_id, patient_id, practitioner_id, site_id, room_id, service_id, status, start_time, end_time, referred_by_id, referred_by_name) "
                    "VALUES (:tid, CAST(:patient_id AS uuid), :practitioner_id, :site_id, :room_id, :service_id, :status, :start, :end, :ref_id, :ref_name) "
                    "RETURNING id, patient_id, practitioner_id, site_id, room_id, service_id, status, start_time, end_time, referred_by_id, referred_by_name"
                ).bindparams(
                    tid=ctx.tenant_id, patient_id=str(body.patient_id), practitioner_id=body.practitioner_id,
                    site_id=body.site_id, room_id=body.room_id, service_id=body.service_id, status=status,
                    start=body.start_time, end=body.end_time, ref_id=body.referred_by_id, ref_name=body.referred_by_name
                )
            )
        ).mappings().one()

        appointment_id = row["id"]

        # Bind any prerequisites to this appointment
        if body.prerequisites:
            for p_id in body.prerequisites:
                await s.execute(
                    text(
                        "INSERT INTO appointment_prerequisite (appointment_id, prerequisite_id, tenant_id, satisfied) "
                        "VALUES (:app_id, :pre_id, :tid, FALSE)"
                    ).bindparams(app_id=appointment_id, pre_id=p_id, tid=ctx.tenant_id)
                )

        await audit_record(
            s, ctx, action="create", resource_type="Appointment",
            resource_id=str(appointment_id), patient_id=str(body.patient_id),
            source_ip=request.client.host if request.client else None
        )
        await s.commit()

    # Emit event (SCH-010)
    await event_publish("appointment.created", {
        "id": str(appointment_id),
        "tenant_id": ctx.tenant_id,
        "patient_id": str(body.patient_id),
        "start_time": body.start_time.isoformat()
    })

    return AppointmentOut(**dict(row))


@router.get("/appointments", response_model=list[AppointmentOut])
async def list_appointments(
    status: Optional[str] = None,
    patient_id: Optional[UUID] = None,
    ctx: RequestContext = Depends(auth),
    session: AsyncSession = Depends(get_session)
):
    """List appointments for tenant (SCH-002)."""
    async with tenant_session(session, ctx) as s:
        sql = (
            "SELECT a.id, a.patient_id, CONCAT(p.given_name, ' ', p.family_name) AS patient_name, "
            "a.practitioner_id, a.site_id, a.room_id, a.service_id, a.status, a.start_time, a.end_time, "
            "a.referred_by_id, a.referred_by_name FROM appointment a "
            "LEFT JOIN patient p ON a.patient_id = p.id WHERE 1=1"
        )
        params = {}
        if patient_id:
            sql += " AND a.patient_id = CAST(:pid AS uuid)"
            params["pid"] = str(patient_id)
        if status:
            sql += " AND a.status = :status"
            params["status"] = status
        sql += " ORDER BY a.start_time DESC"
        rows = (await s.execute(text(sql).bindparams(**params))).mappings().all()
        await s.commit()
    return [AppointmentOut(**r) for r in rows]


@router.get("/appointments/{appointment_id}", response_model=AppointmentDetailOut)
async def get_appointment_details(
    appointment_id: UUID,
    request: Request,
    ctx: RequestContext = Depends(auth),
    session: AsyncSession = Depends(get_session)
):
    async with tenant_session(session, ctx) as s:
        # Query appointment with details of other entities
        sql = """
            SELECT a.id, a.patient_id, p.given_name || ' ' || p.family_name as patient_name,
                   a.practitioner_id, doc.name as practitioner_name,
                   a.site_id, st.name as site_name,
                   a.room_id, rm.name as room_name,
                   a.service_id, sv.name as service_name,
                   a.status, a.start_time, a.end_time, a.referred_by_id, a.referred_by_name
            FROM appointment a
            JOIN patient p ON a.patient_id = p.id
            JOIN practitioner doc ON a.practitioner_id = doc.id
            JOIN site st ON a.site_id = st.id
            JOIN room rm ON a.room_id = rm.id
            JOIN service sv ON a.service_id = sv.id
            WHERE a.id = :app_id
        """
        row = (await s.execute(text(sql).bindparams(app_id=str(appointment_id)))).mappings().one_or_none()
        if not row:
            raise HTTPException(status_code=404, detail="Appointment not found")

        # Fetch prerequisite checklist status
        pre_sql = """
            SELECT ap.prerequisite_id, ap.satisfied,
                   pd.code, pd.description, pd.enforcement_type
            FROM appointment_prerequisite ap
            JOIN prerequisite_definition pd ON ap.prerequisite_id = pd.id
            WHERE ap.appointment_id = :app_id
        """
        pre_rows = (await s.execute(text(pre_sql).bindparams(app_id=str(appointment_id)))).mappings().all()

        await audit_record(
            s, ctx, action="read", resource_type="Appointment",
            resource_id=str(appointment_id), patient_id=str(row["patient_id"]),
            source_ip=request.client.host if request.client else None
        )
        await s.commit()

    prereqs = [AppointmentPrerequisiteOut(**p) for p in pre_rows]
    return AppointmentDetailOut(**row, prerequisites=prereqs)


@router.post("/appointments/{appointment_id}/check-in", response_model=AppointmentOut)
async def check_in_appointment(
    appointment_id: UUID,
    request: Request,
    ctx: RequestContext = Depends(auth),
    session: AsyncSession = Depends(get_session)
):
    """Executes check-in workflow. Enforces prerequisites checking and status updates (SCH-005)."""
    ctx.require_role("admin", "receptionist")
    async with tenant_session(session, ctx) as s:
        # Get existing details
        app_row = (
            await s.execute(
                text("SELECT patient_id, status FROM appointment WHERE id = :id").bindparams(id=str(appointment_id))
            )
        ).mappings().one_or_none()
        
        if not app_row:
            raise HTTPException(status_code=404, detail="Appointment not found")

        # Validate prerequisite checklist (Enforces SCH/REF)
        await verify_and_enforce_prerequisites(s, appointment_id)

        # Transition status to ARRIVED
        row = (
            await s.execute(
                text(
                    "UPDATE appointment SET status = 'ARRIVED', updated_at = now() "
                    "WHERE id = :id "
                    "RETURNING id, patient_id, practitioner_id, site_id, room_id, service_id, status, start_time, end_time, referred_by_id, referred_by_name"
                ).bindparams(id=str(appointment_id))
            )
        ).mappings().one()

        # Calculate outstanding dues warning
        resp_sum = (
            await s.execute(
                text(
                    "SELECT COALESCE(SUM(patient_responsibility), 0) FROM invoice "
                    "WHERE patient_id = :pid AND status = 'finalized'"
                ).bindparams(pid=app_row["patient_id"])
            )
        ).scalar() or 0.0

        paid_sum = (
            await s.execute(
                text(
                    "SELECT COALESCE(SUM(p.amount), 0) FROM payment p "
                    "JOIN invoice i ON p.invoice_id = i.id "
                    "WHERE i.patient_id = :pid AND i.status = 'finalized'"
                ).bindparams(pid=app_row["patient_id"])
            )
        ).scalar() or 0.0

        outstanding = resp_sum - paid_sum
        dues_warning = f"Patient has unpaid invoice dues of {outstanding:.2f} INR" if outstanding > 0 else None

        await audit_record(
            s, ctx, action="update", resource_type="Appointment",
            resource_id=str(appointment_id), patient_id=str(row["patient_id"]),
            source_ip=request.client.host if request.client else None,
            context_note="Checked-in patient"
        )
        await s.commit()

    # Emit event (SCH-010)
    await event_publish("appointment.updated", {
        "id": str(appointment_id),
        "tenant_id": ctx.tenant_id,
        "action": "check-in",
        "status": "ARRIVED"
    })

    ret_dict = dict(row)
    ret_dict["dues_warning"] = dues_warning
    return AppointmentOut(**ret_dict)


@router.post("/appointments/{appointment_id}/status", response_model=AppointmentOut)
async def update_appointment_status(
    appointment_id: UUID,
    request: Request,
    status: str = Query(..., description="Target appointment status"),
    ctx: RequestContext = Depends(auth),
    session: AsyncSession = Depends(get_session)
):
    """Enforce queue status progressions (e.g. ARRIVED -> WAITING -> IN_CONSULTATION -> COMPLETED) (SCH-005)."""
    ctx.require_role("admin", "receptionist", "physician", "nurse")
    async with tenant_session(session, ctx) as s:
        app_row = (
            await s.execute(
                text("SELECT patient_id FROM appointment WHERE id = :id").bindparams(id=str(appointment_id))
            )
        ).mappings().one_or_none()
        
        if not app_row:
            raise HTTPException(status_code=404, detail="Appointment not found")

        row = (
            await s.execute(
                text(
                    "UPDATE appointment SET status = :status, updated_at = now() "
                    "WHERE id = :id "
                    "RETURNING id, patient_id, practitioner_id, site_id, room_id, service_id, status, start_time, end_time, referred_by_id, referred_by_name"
                ).bindparams(id=str(appointment_id), status=status)
            )
        ).mappings().one()

        await audit_record(
            s, ctx, action="update", resource_type="Appointment",
            resource_id=str(appointment_id), patient_id=str(row["patient_id"]),
            source_ip=request.client.host if request.client else None,
            context_note=f"Status update to {status}"
        )
        await s.commit()

    # Emit event
    await event_publish("appointment.updated", {
        "id": str(appointment_id),
        "tenant_id": ctx.tenant_id,
        "action": "status_update",
        "status": status
    })

    return AppointmentOut(**dict(row))


@router.post("/appointments/{appointment_id}/prerequisites/{prereq_id}/satisfy")
async def satisfy_prerequisite(
    appointment_id: UUID,
    prereq_id: str,
    satisfied: bool = Query(default=True),
    ctx: RequestContext = Depends(auth),
    session: AsyncSession = Depends(get_session)
):
    """Marks a prerequisite check as satisfied for the patient's upcoming encounter."""
    ctx.require_role("admin", "receptionist", "nurse")
    async with tenant_session(session, ctx) as s:
        res = await s.execute(
            text(
                "UPDATE appointment_prerequisite SET "
                "satisfied = :sat "
                "WHERE appointment_id = :app_id AND prerequisite_id = :pre_id"
            ).bindparams(
                sat=satisfied, app_id=str(appointment_id), pre_id=prereq_id
            )
        )
        if res.rowcount == 0:
            raise HTTPException(status_code=404, detail="Appointment prerequisite mapping not found")
        await s.commit()
    return {"status": "success", "satisfied": satisfied}


@router.get("/queue", response_model=list[QueueItemOut])
async def get_clinic_queue(
    site_id: str = Query(default="site_apollo_main"),
    ctx: RequestContext = Depends(auth),
    session: AsyncSession = Depends(get_session)
):
    """Retrieves list of active check-in patient progressions for the queue display (SCH-005)."""
    async with tenant_session(session, ctx) as s:
        # Select active queue states (ARRIVED, WAITING, IN_CONSULTATION)
        sql = """
            SELECT a.id as appointment_id, a.patient_id, p.given_name || ' ' || p.family_name as patient_name,
                   a.status, a.start_time, sv.name as service_name, doc.name as practitioner_name, st.name as site_name
            FROM appointment a
            JOIN patient p ON a.patient_id = p.id
            JOIN service sv ON a.service_id = sv.id
            JOIN practitioner doc ON a.practitioner_id = doc.id
            JOIN site st ON a.site_id = st.id
            WHERE a.site_id = :site_id AND a.status IN ('ARRIVED', 'WAITING', 'IN_CONSULTATION')
            ORDER BY a.start_time ASC
        """
        rows = (await s.execute(text(sql).bindparams(site_id=site_id))).mappings().all()
        await s.commit()
    return [QueueItemOut(**r) for r in rows]
