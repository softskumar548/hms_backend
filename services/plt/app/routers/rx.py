from __future__ import annotations

from datetime import datetime, time
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
from .rx_schemas import (
    MedicationCatalogCreate,
    MedicationCatalogOut,
    PrescriptionCreate,
    PrescriptionDetailOut,
    PrescriptionItemOut,
    PrescriptionOut,
    PrescriptionSign
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/rx", tags=["rx"])


# --- Masters Setup ---

@router.post("/drugs", response_model=MedicationCatalogOut, status_code=201)
async def create_drug(
    body: MedicationCatalogCreate,
    ctx: RequestContext = Depends(auth),
    session: AsyncSession = Depends(get_session)
):
    ctx.require_role("admin")
    async with tenant_session(session, ctx) as s:
        row = (
            await s.execute(
                text(
                    "INSERT INTO medication_catalog (id, tenant_id, name, generic_name, form, strength) "
                    "VALUES (:id, :tid, :name, :generic_name, :form, :strength) "
                    "RETURNING id, name, generic_name, form, strength"
                ).bindparams(
                    id=body.id, tid=ctx.tenant_id, name=body.name,
                    generic_name=body.generic_name, form=body.form, strength=body.strength
                )
            )
        ).mappings().one()
        await s.commit()
    return MedicationCatalogOut(**row)


@router.get("/drugs", response_model=list[MedicationCatalogOut])
async def search_drugs(
    q: str = Query(..., min_length=1),
    ctx: RequestContext = Depends(auth),
    session: AsyncSession = Depends(get_session)
):
    """ med catalog search (RX-001)."""
    async with tenant_session(session, ctx) as s:
        rows = (
            await s.execute(
                text(
                    "SELECT id, name, generic_name, form, strength FROM medication_catalog "
                    "WHERE name ILIKE :q OR generic_name ILIKE :q"
                ).bindparams(q=f"%{q}%")
            )
        ).mappings().all()
        await s.commit()
    return [MedicationCatalogOut(**r) for r in rows]


# --- Core Prescribing Workflows ---

@router.post("/prescriptions", response_model=PrescriptionDetailOut, status_code=201)
async def create_prescription(
    body: PrescriptionCreate,
    request: Request,
    ctx: RequestContext = Depends(auth),
    session: AsyncSession = Depends(get_session)
):
    """Composes a draft prescription holding SIG properties (RX-002)."""
    ctx.require_role("admin", "physician")
    async with tenant_session(session, ctx) as s:
        # 1. Create prescription header record (Initial state: draft)
        rx_row = (
            await s.execute(
                text(
                    "INSERT INTO prescription (tenant_id, patient_id, practitioner_id, encounter_id, status) "
                    "VALUES (:tid, :patient_id, :practitioner_id, :encounter_id, 'draft') "
                    "RETURNING id, patient_id, practitioner_id, encounter_id, status, created_at, updated_at, signed_at, signed_by"
                ).bindparams(
                    tid=ctx.tenant_id, patient_id=body.patient_id,
                    practitioner_id=ctx.user_id, encounter_id=body.encounter_id
                )
            )
        ).mappings().one()

        prescription_id = rx_row["id"]
        items = []

        # 2. Insert items
        for item in body.items:
            # Enforce SIG completeness (RX-002) - validates route/frequency
            if not item.dose or not item.unit or not item.route or not item.frequency or not item.duration_days or not item.quantity:
                raise HTTPException(status_code=400, detail="Prescription SIG components cannot be incomplete.")
                
            item_row = (
                await s.execute(
                    text(
                        "INSERT INTO prescription_item "
                        "(tenant_id, prescription_id, medication_id, dose, unit, route, frequency, duration_days, prn, quantity, refills, free_text_sig) "
                        "VALUES (:tid, :rx_id, :med_id, :dose, :unit, :route, :frequency, :duration, :prn, :qty, :refills, :sig) "
                        "RETURNING id, medication_id, dose, unit, route, frequency, duration_days, prn, quantity, refills, free_text_sig"
                    ).bindparams(
                        tid=ctx.tenant_id, rx_id=prescription_id, med_id=item.medication_id,
                        dose=item.dose, unit=item.unit, route=item.route, frequency=item.frequency,
                        duration=item.duration_days, prn=item.prn, qty=item.quantity, refills=item.refills, sig=item.free_text_sig
                    )
                )
            ).mappings().one()
            items.append(PrescriptionItemOut(**item_row))

        await audit_record(
            s, ctx, action="create", resource_type="Prescription",
            resource_id=str(prescription_id), patient_id=str(body.patient_id),
            source_ip=request.client.host if request.client else None
        )
        await s.commit()

    return PrescriptionDetailOut(**rx_row, items=items)


@router.post("/prescriptions/{prescription_id}/sign", response_model=PrescriptionOut)
async def sign_prescription(
    prescription_id: UUID,
    body: PrescriptionSign,
    request: Request,
    ctx: RequestContext = Depends(auth),
    session: AsyncSession = Depends(get_session)
):
    """Signs prescription, runs drug-allergy interactions, and bindings follow-up draft (Flag F1) (RX-003, RX-009)."""
    ctx.require_role("admin", "physician")
    async with tenant_session(session, ctx) as s:
        # Fetch prescription details
        rx = (
            await s.execute(
                text("SELECT patient_id, encounter_id, status FROM prescription WHERE id = :id").bindparams(id=prescription_id)
            )
        ).mappings().one_or_none()

        if not rx:
            raise HTTPException(status_code=404, detail="Prescription not found")
        if rx["status"] == "signed":
            raise HTTPException(status_code=400, detail="Prescription is already signed.")

        patient_id = rx["patient_id"]

        # Fetch prescribed drugs generic names
        items = (
            await s.execute(
                text(
                    "SELECT pi.medication_id, mc.generic_name FROM prescription_item pi "
                    "JOIN medication_catalog mc ON pi.medication_id = mc.id "
                    "WHERE pi.prescription_id = :rx_id"
                ).bindparams(rx_id=prescription_id)
            )
        ).mappings().all()

        # Run Allergy checking checks (RX-003)
        # Query patient's active allergies
        allergies = (
            await s.execute(
                text(
                    "SELECT substance_display FROM allergy_intolerance "
                    "WHERE patient_id = :pid AND is_no_known = FALSE"
                ).bindparams(pid=patient_id)
            )
        ).mappings().all()

        allergy_displays = {a["substance_display"].lower() for a in allergies if a["substance_display"]}
        
        triggered_alert = None
        for item in items:
            g_name = item["generic_name"].lower()
            if g_name in allergy_displays:
                triggered_alert = {
                    "alert_type": "drug-allergy",
                    "severity": "high",
                    "medication_id": item["medication_id"],
                    "generic_name": item["generic_name"]
                }
                break

        # Block signing if alert triggered and override reason not supplied
        if triggered_alert and not body.override_reason:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "Drug-allergy interaction alert. Severe risk identified.",
                    "alert": triggered_alert
                }
            )

        # Record override if reason supplied
        if triggered_alert and body.override_reason:
            await s.execute(
                text(
                    "INSERT INTO prescription_override (tenant_id, prescription_id, alert_type, severity, reason) "
                    "VALUES (:tid, :rx_id, :type, :sev, :reason)"
                ).bindparams(
                    tid=ctx.tenant_id, rx_id=prescription_id, type=triggered_alert["alert_type"],
                    sev=triggered_alert["severity"], reason=body.override_reason
                )
            )

        # Transition status to signed
        row = (
            await s.execute(
                text(
                    "UPDATE prescription SET status = 'signed', signed_at = now(), signed_by = :sb, updated_at = now() "
                    "WHERE id = :id "
                    "RETURNING id, patient_id, practitioner_id, encounter_id, status, created_at, updated_at, signed_at, signed_by"
                ).bindparams(id=prescription_id, sb=ctx.user_id)
            )
        ).mappings().one()

        # --- Prescription-driven Follow-up (Flag F1: DRAFT, not auto-booked) ---
        if body.follow_up_date and body.follow_up_service_id and body.follow_up_site_id:
            # 1. Fetch site to assert room exists (dummy logic or just select first room for simplicity)
            room_row = (
                await s.execute(
                    text("SELECT id FROM room WHERE site_id = :site_id LIMIT 1").bindparams(site_id=body.follow_up_site_id)
                )
            ).mappings().one_or_none()
            room_id = room_row["id"] if room_row else "room-default"

            # Create DRAFT appointment
            start_time = datetime.combine(body.follow_up_date, time(9, 0)) # Default follow-up start
            end_time = datetime.combine(body.follow_up_date, time(9, 30))

            draft_app_row = (
                await s.execute(
                    text(
                        "INSERT INTO appointment "
                        "(tenant_id, patient_id, practitioner_id, site_id, room_id, service_id, status, start_time, end_time) "
                        "VALUES (:tid, :patient_id, :practitioner_id, :site_id, :room_id, :service_id, 'DRAFT', :start, :end) "
                        "RETURNING id"
                    ).bindparams(
                        tid=ctx.tenant_id, patient_id=patient_id, practitioner_id=ctx.user_id,
                        site_id=body.follow_up_site_id, room_id=room_id, service_id=body.follow_up_service_id,
                        start=start_time, end=end_time
                    )
                )
            ).mappings().one()

            # Bind any follow-up prerequisites to DRAFT appointment
            if body.follow_up_prerequisites:
                for pre_id in body.follow_up_prerequisites:
                    await s.execute(
                        text(
                            "INSERT INTO appointment_prerequisite (appointment_id, prerequisite_id, tenant_id, satisfied) "
                            "VALUES (:app_id, :pre_id, :tid, FALSE)"
                        ).bindparams(app_id=draft_app_row["id"], pre_id=pre_id, tid=ctx.tenant_id)
                    )

        await audit_record(
            s, ctx, action="update", resource_type="Prescription",
            resource_id=str(prescription_id), patient_id=str(patient_id),
            source_ip=request.client.host if request.client else None,
            context_note="Signed prescription"
        )
        await s.commit()

    # Emit event (RX-008)
    await event_publish("medicationrequest.signed", {
        "id": str(prescription_id),
        "tenant_id": ctx.tenant_id,
        "patient_id": str(patient_id),
        "signed_by": ctx.user_id
    })

    return PrescriptionOut(**row)


@router.post("/prescriptions/{prescription_id}/renew", response_model=PrescriptionDetailOut, status_code=201)
async def renew_prescription(
    prescription_id: UUID,
    request: Request,
    ctx: RequestContext = Depends(auth),
    session: AsyncSession = Depends(get_session)
):
    """Clones prior signed prescriptions to draft state (RX-006)."""
    ctx.require_role("admin", "physician")
    async with tenant_session(session, ctx) as s:
        # Fetch target prescription
        rx = (
            await s.execute(
                text("SELECT patient_id, encounter_id, status FROM prescription WHERE id = :id").bindparams(id=prescription_id)
            )
        ).mappings().one_or_none()

        if not rx:
            raise HTTPException(status_code=404, detail="Prescription not found")

        # Create new draft prescription
        rx_row = (
            await s.execute(
                text(
                    "INSERT INTO prescription (tenant_id, patient_id, practitioner_id, encounter_id, status) "
                    "VALUES (:tid, :patient_id, :practitioner_id, :encounter_id, 'draft') "
                    "RETURNING id, patient_id, practitioner_id, encounter_id, status, created_at, updated_at"
                ).bindparams(
                    tid=ctx.tenant_id, patient_id=rx["patient_id"],
                    practitioner_id=ctx.user_id, encounter_id=rx["encounter_id"]
                )
            )
        ).mappings().one()

        new_rx_id = rx_row["id"]

        # Fetch prior items
        prior_items = (
            await s.execute(
                text(
                    "SELECT medication_id, dose, unit, route, frequency, duration_days, prn, quantity, refills, free_text_sig "
                    "FROM prescription_item WHERE prescription_id = :rx_id"
                ).bindparams(rx_id=prescription_id)
            )
        ).mappings().all()

        items = []
        for item in prior_items:
            item_row = (
                await s.execute(
                    text(
                        "INSERT INTO prescription_item "
                        "(tenant_id, prescription_id, medication_id, dose, unit, route, frequency, duration_days, prn, quantity, refills, free_text_sig) "
                        "VALUES (:tid, :rx_id, :med_id, :dose, :unit, :route, :frequency, :duration, :prn, :qty, :refills, :sig) "
                        "RETURNING id, medication_id, dose, unit, route, frequency, duration_days, prn, quantity, refills, free_text_sig"
                    ).bindparams(
                        tid=ctx.tenant_id, rx_id=new_rx_id, med_id=item["medication_id"],
                        dose=item["dose"], unit=item["unit"], route=item["route"], frequency=item["frequency"],
                        duration=item["duration_days"], prn=item["prn"], qty=item["quantity"], refills=item["refills"], sig=item["free_text_sig"]
                    )
                )
            ).mappings().one()
            items.append(PrescriptionItemOut(**item_row))

        await audit_record(
            s, ctx, action="create", resource_type="Prescription",
            resource_id=str(new_rx_id), patient_id=str(rx["patient_id"]),
            source_ip=request.client.host if request.client else None,
            context_note=f"Renewed from prescription {prescription_id}"
        )
        await s.commit()

    return PrescriptionDetailOut(**rx_row, items=items)
