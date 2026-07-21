from __future__ import annotations

from datetime import date, datetime, timedelta
import logging
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def check_booking_conflicts(
    session: AsyncSession,
    practitioner_id: str,
    room_id: str,
    patient_id: UUID,
    start_time: datetime,
    end_time: datetime,
    exclude_appointment_id: UUID | None = None
) -> None:
    """Enforces hard conflict detection on Practitioner, Room, and Patient (SCH-002).
    
    Verifies that no active bookings overlap with the requested time window.
    """
    binds: dict[str, Any] = {
        "start": start_time,
        "end": end_time,
        "practitioner_id": practitioner_id,
        "room_id": room_id,
        "patient_id": patient_id
    }
    
    exclude_clause = ""
    if exclude_appointment_id:
        exclude_clause = "AND id != :exclude_id"
        binds["exclude_id"] = exclude_appointment_id

    # Conflict check query: Checks if any active (non-cancelled, non-draft, non-no-show) booking overlaps
    sql = f"""
        SELECT id, practitioner_id, room_id, patient_id 
        FROM appointment 
        WHERE status NOT IN ('CANCELLED', 'NO_SHOW', 'DRAFT')
          AND start_time < :end 
          AND end_time > :start
          AND (practitioner_id = :practitioner_id OR room_id = :room_id OR patient_id = :patient_id)
          {exclude_clause}
    """
    
    rows = (await session.execute(text(sql).bindparams(**binds))).mappings().all()
    
    for r in rows:
        if r["practitioner_id"] == practitioner_id:
            raise HTTPException(
                status_code=409,
                detail=f"Practitioner {practitioner_id} is already booked during this time."
            )
        if r["room_id"] == room_id:
            raise HTTPException(
                status_code=409,
                detail=f"Room {room_id} is already booked during this time."
            )
        if str(r["patient_id"]) == str(patient_id):
            raise HTTPException(
                status_code=409,
                detail=f"Patient {patient_id} has another appointment during this time."
            )


async def find_available_slots(
    session: AsyncSession,
    practitioner_id: str,
    service_id: str,
    target_date: date
) -> list[dict[str, Any]]:
    """Calculates available booking slots based on availability templates and current bookings (SCH-004)."""
    # 1. Fetch Service duration
    service_row = (
        await session.execute(
            text("SELECT duration_minutes FROM service WHERE id = :sid").bindparams(sid=service_id)
        )
    ).mappings().one_or_none()
    
    if not service_row:
        raise HTTPException(status_code=404, detail=f"Service {service_id} not found")
        
    duration = timedelta(minutes=service_row["duration_minutes"])

    # 2. Get practitioner availability for the target day of week
    # Sunday=0, Monday=1, ..., Saturday=6
    day_of_week = (target_date.weekday() + 1) % 7
    
    avail_rows = (
        await session.execute(
            text(
                "SELECT site_id, start_time, end_time FROM practitioner_availability "
                "WHERE practitioner_id = :pid AND day_of_week = :day"
            ).bindparams(pid=practitioner_id, day=day_of_week)
        )
    ).mappings().all()

    # 3. Get existing bookings for the practitioner on that date
    booking_rows = (
        await session.execute(
            text(
                "SELECT start_time, end_time FROM appointment "
                "WHERE practitioner_id = :pid AND status NOT IN ('CANCELLED', 'NO_SHOW') "
                "AND DATE(start_time) = :date"
            ).bindparams(pid=practitioner_id, date=target_date)
        )
    ).mappings().all()

    # 4. Generate slots
    slots = []
    for avail in avail_rows:
        site_id = avail["site_id"]
        # Convert start/end time to datetime objects for calculation
        start_dt = datetime.combine(target_date, avail["start_time"])
        end_dt = datetime.combine(target_date, avail["end_time"])

        curr = start_dt
        while curr + duration <= end_dt:
            slot_start = curr
            slot_end = curr + duration
            
            # Check overlap against all booked appointments
            has_conflict = False
            for b in booking_rows:
                # b["start_time"] is offset-aware usually; let's strip timezone for comparison
                b_start = b["start_time"].replace(tzinfo=None)
                b_end = b["end_time"].replace(tzinfo=None)
                if slot_start < b_end and slot_end > b_start:
                    has_conflict = True
                    break
                    
            if not has_conflict:
                slots.append({
                    "start_time": slot_start,
                    "end_time": slot_end,
                    "site_id": site_id,
                    "practitioner_id": practitioner_id,
                    "service_id": service_id
                })
            curr += duration

    return slots


async def verify_and_enforce_prerequisites(
    session: AsyncSession,
    appointment_id: UUID
) -> dict[str, Any]:
    """Inspects prerequisite completion during check-in, blocking on hard-stops (SCH/REF)."""
    # Fetch prerequisites associated with the appointment
    sql = """
        SELECT ap.prerequisite_id, ap.satisfied, pd.code, pd.description, pd.enforcement_type
        FROM appointment_prerequisite ap
        JOIN prerequisite_definition pd ON ap.prerequisite_id = pd.id
        WHERE ap.appointment_id = :app_id
    """
    rows = (await session.execute(text(sql).bindparams(app_id=appointment_id))).mappings().all()

    hard_stops = []
    advisories = []

    for r in rows:
        if not r["satisfied"]:
            prereq_info = {
                "id": r["prerequisite_id"],
                "code": r["code"],
                "description": r["description"]
            }
            if r["enforcement_type"] == "hard-stop":
                hard_stops.append(prereq_info)
            else:
                advisories.append(prereq_info)

    if hard_stops:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Check-in blocked. Hard-stop prerequisites are unsatisfied.",
                "unsatisfied_hard_stops": hard_stops,
                "unsatisfied_advisories": advisories
            }
        )

    return {
        "status": "allowed",
        "warnings": advisories
    }
