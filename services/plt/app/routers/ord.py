from __future__ import annotations

import json
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
from .ord_schemas import (
    AnalyteTrendItem,
    AnalyteTrendOut,
    ClinicianInboxItemOut,
    LabCatalogCreate,
    LabCatalogOut,
    LabOrderCreate,
    LabOrderOut,
    LabResultIngest,
    LabResultOut,
    LabUnmatchedResultOut,
    UnmatchedResultResolve
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/ord", tags=["ord"])


# --- Catalog Master Setup ---

@router.post("/catalog", response_model=LabCatalogOut, status_code=201)
async def create_catalog_item(
    body: LabCatalogCreate,
    ctx: RequestContext = Depends(auth),
    session: AsyncSession = Depends(get_session)
):
    ctx.require_role("admin")
    async with tenant_session(session, ctx) as s:
        row = (
            await s.execute(
                text(
                    "INSERT INTO lab_catalog (id, tenant_id, test_code, name, specimen_requirements, preparation_requirements) "
                    "VALUES (:id, :tid, :code, :name, :specimen, :prep) "
                    "RETURNING id, test_code, name, specimen_requirements, preparation_requirements"
                ).bindparams(
                    id=body.id, tid=ctx.tenant_id, code=body.test_code,
                    name=body.name, specimen=body.specimen_requirements, prep=body.preparation_requirements
                )
            )
        ).mappings().one()
        await s.commit()
    return LabCatalogOut(**row)


@router.get("/catalog", response_model=list[LabCatalogOut])
async def search_catalog(
    q: str = Query(..., min_length=1),
    ctx: RequestContext = Depends(auth),
    session: AsyncSession = Depends(get_session)
):
    async with tenant_session(session, ctx) as s:
        rows = (
            await s.execute(
                text(
                    "SELECT id, test_code, name, specimen_requirements, preparation_requirements FROM lab_catalog "
                    "WHERE name ILIKE :q OR test_code ILIKE :q"
                ).bindparams(q=f"%{q}%")
            )
        ).mappings().all()
        await s.commit()
    return [LabCatalogOut(**r) for r in rows]


# --- Core Laboratory Workflow ---

@router.post("/orders", response_model=LabOrderOut, status_code=201)
async def create_lab_order(
    body: LabOrderCreate,
    request: Request,
    ctx: RequestContext = Depends(auth),
    session: AsyncSession = Depends(get_session)
):
    """Order laboratory tests from a tenant catalog (ORD-001)."""
    ctx.require_role("admin", "physician", "nurse")
    async with tenant_session(session, ctx) as s:
        order_row = (
            await s.execute(
                text(
                    "INSERT INTO lab_order (tenant_id, patient_id, practitioner_id, encounter_id, priority, status) "
                    "VALUES (:tid, :patient_id, :practitioner_id, :encounter_id, :priority, 'ordered') "
                    "RETURNING id, patient_id, practitioner_id, encounter_id, status, priority, created_at"
                ).bindparams(
                    tid=ctx.tenant_id, patient_id=body.patient_id,
                    practitioner_id=ctx.user_id, encounter_id=body.encounter_id, priority=body.priority
                )
            )
        ).mappings().one()

        order_id = order_row["id"]

        for test_id in body.test_ids:
            await s.execute(
                text("INSERT INTO lab_order_item (tenant_id, order_id, test_id) VALUES (:tid, :order_id, :test_id)").bindparams(
                    tid=ctx.tenant_id, order_id=order_id, test_id=test_id
                )
            )

        await audit_record(
            s, ctx, action="create", resource_type="LabOrder",
            resource_id=str(order_id), patient_id=str(body.patient_id),
            source_ip=request.client.host if request.client else None
        )
        await s.commit()

    return LabOrderOut(**order_row)


@router.post("/orders/{order_id}/specimen", response_model=LabOrderOut)
async def collect_specimen(
    order_id: UUID,
    request: Request,
    ctx: RequestContext = Depends(auth),
    session: AsyncSession = Depends(get_session)
):
    """Progress status to specimen_collected (ORD-006)."""
    ctx.require_role("admin", "receptionist", "nurse")
    async with tenant_session(session, ctx) as s:
        order_row = (
            await s.execute(
                text("SELECT patient_id, status FROM lab_order WHERE id = :id").bindparams(id=order_id)
            )
        ).mappings().one_or_none()

        if not order_row:
            raise HTTPException(status_code=404, detail="Lab order not found")

        updated_row = (
            await s.execute(
                text(
                    "UPDATE lab_order SET status = 'specimen_collected', updated_at = now() WHERE id = :id "
                    "RETURNING id, patient_id, practitioner_id, encounter_id, status, priority, created_at"
                ).bindparams(id=order_id)
            )
        ).mappings().one()

        await audit_record(
            s, ctx, action="update", resource_type="LabOrder",
            resource_id=str(order_id), patient_id=str(updated_row["patient_id"]),
            source_ip=request.client.host if request.client else None,
            context_note="Specimen collected"
        )
        await s.commit()

    return LabOrderOut(**updated_row)


@router.post("/results/ingest", response_model=LabResultOut, status_code=201)
async def ingest_result(
    body: LabResultIngest,
    request: Request,
    ctx: RequestContext = Depends(auth),
    session: AsyncSession = Depends(get_session)
):
    """Ingest diagnostic report results (ORD-003). Auto-matches or routes to unmatched queue."""
    ctx.require_role("admin", "nurse")
    async with tenant_session(session, ctx) as s:
        patient_id = body.patient_id
        
        # Try finding patient if order is provided
        if body.order_id and not patient_id:
            order_row = (
                await s.execute(
                    text("SELECT patient_id FROM lab_order WHERE id = :id").bindparams(id=body.order_id)
                )
            ).mappings().one_or_none()
            if order_row:
                patient_id = order_row["patient_id"]

        # Route to unmatched queue if order or patient is missing
        if not body.order_id or not patient_id:
            payload = body.model_dump()
            if payload.get("order_id"):
                payload["order_id"] = str(payload["order_id"])
            if payload.get("patient_id"):
                payload["patient_id"] = str(payload["patient_id"])
                
            unmatched_row = (
                await s.execute(
                    text(
                        "INSERT INTO lab_unmatched_result (tenant_id, payload, status) "
                        "VALUES (:tid, :payload, 'pending') "
                        "RETURNING id"
                    ).bindparams(tid=ctx.tenant_id, payload=json.dumps(payload))
                )
            ).mappings().one()
            await s.commit()
            
            raise HTTPException(
                status_code=202,
                detail={
                    "message": "Result ingestion incomplete. Order/patient mismatch. Routed to unmatched resolution queue.",
                    "unmatched_id": unmatched_row["id"]
                }
            )

        # Insert result
        res_row = (
            await s.execute(
                text(
                    "INSERT INTO lab_result (tenant_id, order_id, patient_id, test_id, value, unit, reference_range, is_abnormal, is_critical) "
                    "VALUES (:tid, :order_id, :patient_id, :test_id, :val, :unit, :ref_range, :abn, :crit) "
                    "RETURNING id, order_id, patient_id, test_id, value, unit, reference_range, is_abnormal, is_critical, resulted_at"
                ).bindparams(
                    tid=ctx.tenant_id, order_id=body.order_id, patient_id=patient_id, test_id=body.test_id,
                    val=body.value, unit=body.unit, ref_range=body.reference_range, abn=body.is_abnormal, crit=body.is_critical
                )
            )
        ).mappings().one()

        # Update order status to resulted
        await s.execute(
            text("UPDATE lab_order SET status = 'resulted', updated_at = now() WHERE id = :id").bindparams(id=body.order_id)
        )

        # --- Referral Loop Closing Integration ---
        patient_row = (
            await s.execute(
                text("SELECT referred_by_id, referred_by_name FROM patient WHERE id = :pid").bindparams(pid=patient_id)
            )
        ).mappings().one_or_none()

        if patient_row and patient_row["referred_by_id"]:
            # Close loop to referrer
            await event_publish("referral.closed", {
                "tenant_id": ctx.tenant_id,
                "patient_id": str(patient_id),
                "referrer_id": patient_row["referred_by_id"],
                "referrer_name": patient_row["referred_by_name"],
                "order_id": str(body.order_id),
                "action": "lab_resulted"
            })

        await audit_record(
            s, ctx, action="create", resource_type="LabResult",
            resource_id=str(res_row["id"]), patient_id=str(patient_id),
            source_ip=request.client.host if request.client else None
        )
        await s.commit()

    return LabResultOut(**res_row)


@router.get("/results/unmatched", response_model=list[LabUnmatchedResultOut])
async def list_unmatched_results(
    ctx: RequestContext = Depends(auth),
    session: AsyncSession = Depends(get_session)
):
    ctx.require_role("admin", "nurse")
    async with tenant_session(session, ctx) as s:
        rows = (
            await s.execute(
                text("SELECT id, payload, status, resolved_by, resolved_at FROM lab_unmatched_result WHERE status = 'pending'")
            )
        ).mappings().all()
        await s.commit()
    return [LabUnmatchedResultOut(**r) for r in rows]


@router.post("/results/unmatched/{unmatched_id}/resolve", response_model=LabResultOut)
async def resolve_unmatched_result(
    unmatched_id: UUID,
    body: UnmatchedResultResolve,
    request: Request,
    ctx: RequestContext = Depends(auth),
    session: AsyncSession = Depends(get_session)
):
    """Manually resolve unmatched payloads (ORD-003)."""
    ctx.require_role("admin", "nurse")
    async with tenant_session(session, ctx) as s:
        # Fetch unmatched record
        unmatched = (
            await s.execute(
                text("SELECT payload, status FROM lab_unmatched_result WHERE id = :id").bindparams(id=unmatched_id)
            )
        ).mappings().one_or_none()

        if not unmatched or unmatched["status"] != "pending":
            raise HTTPException(status_code=404, detail="Unmatched result pending record not found")

        payload = unmatched["payload"]

        # Insert resolved result
        res_row = (
            await s.execute(
                text(
                    "INSERT INTO lab_result (tenant_id, order_id, patient_id, test_id, value, unit, reference_range, is_abnormal, is_critical) "
                    "VALUES (:tid, :order_id, :patient_id, :test_id, :val, :unit, :ref_range, :abn, :crit) "
                    "RETURNING id, order_id, patient_id, test_id, value, unit, reference_range, is_abnormal, is_critical, resulted_at"
                ).bindparams(
                    tid=ctx.tenant_id, order_id=body.order_id, patient_id=body.patient_id, test_id=payload["test_id"],
                    val=payload["value"], unit=payload["unit"], ref_range=payload.get("reference_range"),
                    abn=payload.get("is_abnormal", False), crit=payload.get("is_critical", False)
                )
            )
        ).mappings().one()

        # Update unmatched status
        await s.execute(
            text(
                "UPDATE lab_unmatched_result SET status = 'resolved', resolved_by = :sb, resolved_at = now() "
                "WHERE id = :id"
            ).bindparams(id=unmatched_id, sb=ctx.user_id)
        )

        # Update order status to resulted
        await s.execute(
            text("UPDATE lab_order SET status = 'resulted', updated_at = now() WHERE id = :id").bindparams(id=body.order_id)
        )

        await audit_record(
            s, ctx, action="update", resource_type="LabUnmatchedResult",
            resource_id=str(unmatched_id), patient_id=str(body.patient_id),
            source_ip=request.client.host if request.client else None,
            context_note="Manually matched unmatched result payload"
        )
        await s.commit()

    return LabResultOut(**res_row)


@router.get("/clinicians/inbox", response_model=list[ClinicianInboxItemOut])
async def get_clinician_inbox(
    ctx: RequestContext = Depends(auth),
    session: AsyncSession = Depends(get_session)
):
    """Deliver diagnostic results to clinician review inboxes (ORD-004)."""
    ctx.require_role("admin", "physician")
    async with tenant_session(session, ctx) as s:
        sql = """
            SELECT r.id as result_id, r.patient_id, p.given_name || ' ' || p.family_name as patient_name,
                   r.test_id, lc.name as test_name, r.value, r.unit, r.is_abnormal, r.is_critical, r.resulted_at
            FROM lab_result r
            JOIN patient p ON r.patient_id = p.id
            JOIN lab_catalog lc ON r.test_id = lc.id
            WHERE r.reviewed_at IS NULL
            ORDER BY r.is_critical DESC, r.resulted_at DESC
        """
        rows = (await s.execute(text(sql))).mappings().all()
        await s.commit()
    return [ClinicianInboxItemOut(**r) for r in rows]


@router.post("/results/{result_id}/acknowledge")
async def acknowledge_result(
    result_id: UUID,
    request: Request,
    ctx: RequestContext = Depends(auth),
    session: AsyncSession = Depends(get_session)
):
    """Records clinician acknowledgment/signing of result (ORD-004)."""
    ctx.require_role("admin", "physician")
    async with tenant_session(session, ctx) as s:
        row = (
            await s.execute(
                text("SELECT patient_id FROM lab_result WHERE id = :id").bindparams(id=result_id)
            )
        ).mappings().one_or_none()

        if not row:
            raise HTTPException(status_code=404, detail="Result not found")

        await s.execute(
            text(
                "UPDATE lab_result SET reviewed_at = now(), reviewed_by = :sb "
                "WHERE id = :id"
            ).bindparams(id=result_id, sb=ctx.user_id)
        )

        await audit_record(
            s, ctx, action="update", resource_type="LabResult",
            resource_id=str(result_id), patient_id=str(row["patient_id"]),
            source_ip=request.client.host if request.client else None,
            context_note="Result acknowledged/signed off by clinician"
        )
        await s.commit()

    return {"status": "success", "result_id": result_id}


@router.get("/patients/{patient_id}/analytes", response_model=list[AnalyteTrendOut])
async def get_analyte_trends(
    patient_id: UUID,
    ctx: RequestContext = Depends(auth),
    session: AsyncSession = Depends(get_session)
):
    """Retrieve analyte trends across time for patient graphing (ORD-005)."""
    async with tenant_session(session, ctx) as s:
        sql = """
            SELECT r.test_id, lc.name as test_name, lc.test_code, r.value, r.unit, r.resulted_at
            FROM lab_result r
            JOIN lab_catalog lc ON r.test_id = lc.id
            WHERE r.patient_id = :pid
            ORDER BY r.test_id, r.resulted_at ASC
        """
        rows = (await s.execute(text(sql).bindparams(pid=patient_id))).mappings().all()
        await s.commit()

    # Group analytes
    trends: dict[str, AnalyteTrendOut] = {}
    for r in rows:
        t_id = r["test_id"]
        if t_id not in trends:
            trends[t_id] = AnalyteTrendOut(
                test_id=t_id,
                test_name=r["test_name"],
                test_code=r["test_code"],
                history=[]
            )
        trends[t_id].history.append(
            AnalyteTrendItem(resulted_at=r["resulted_at"], value=r["value"], unit=r["unit"])
        )

    return list(trends.values())
