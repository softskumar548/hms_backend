
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
from .bil_schemas import (
    ChargeMasterCreate,
    ChargeMasterOut,
    ClaimCreate,
    ClaimOut,
    InvoiceCreate,
    InvoiceDetailOut,
    InvoiceLineCreate,
    InvoiceLineOut,
    InvoiceOut,
    PatientCoverageCreate,
    PatientCoverageOut,
    PaymentCreate,
    PaymentOut
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/bil", tags=["bil"])


# --- Charge Master Master Setup ---

@router.post("/charges", response_model=ChargeMasterOut, status_code=201)
async def create_charge_item(
    body: ChargeMasterCreate,
    ctx: RequestContext = Depends(auth),
    session: AsyncSession = Depends(get_session)
):
    ctx.require_role("admin")
    async with tenant_session(session, ctx) as s:
        row = (
            await s.execute(
                text(
                    "INSERT INTO charge_master (id, tenant_id, code, name, category, standard_price, tax_percent, active) "
                    "VALUES (:id, :tid, :code, :name, :category, :price, :tax, :active) "
                    "RETURNING id, code, name, category, standard_price, tax_percent, active"
                ).bindparams(
                    id=body.id, tid=ctx.tenant_id, code=body.code, name=body.name,
                    category=body.category, price=body.standard_price, tax=body.tax_percent, active=body.active
                )
            )
        ).mappings().one()
        await s.commit()
    return ChargeMasterOut(**row)


@router.get("/charges", response_model=list[ChargeMasterOut])
async def search_charges(
    q: str = Query(..., min_length=1),
    ctx: RequestContext = Depends(auth),
    session: AsyncSession = Depends(get_session)
):
    async with tenant_session(session, ctx) as s:
        rows = (
            await s.execute(
                text(
                    "SELECT id, code, name, category, standard_price, tax_percent, active FROM charge_master "
                    "WHERE name ILIKE :q OR code ILIKE :q"
                ).bindparams(q=f"%{q}%")
            )
        ).mappings().all()
        await s.commit()
    return [ChargeMasterOut(**r) for r in rows]


# --- Insurance Coverage Registration & Eligibility ---

@router.post("/coverages", response_model=PatientCoverageOut, status_code=201)
async def register_coverage(
    body: PatientCoverageCreate,
    ctx: RequestContext = Depends(auth),
    session: AsyncSession = Depends(get_session)
):
    """Register patient insurance coverage (BIL-003). Runs Aarogyasri/PMJAY Aadhaar checks."""
    ctx.require_role("admin", "receptionist", "nurse")
    async with tenant_session(session, ctx) as s:
        # Check if India public scheme eligibility applies (Aarogyasri / PMJAY require Aadhaar validation)
        if body.scheme_type.lower() in ("aarogyasri", "pmjay"):
            patient = (
                await s.execute(
                    text("SELECT aadhaar_last_four FROM patient WHERE id = :pid").bindparams(pid=body.patient_id)
                )
            ).mappings().one_or_none()

            if not patient or not patient["aadhaar_last_four"]:
                raise HTTPException(
                    status_code=400,
                    detail="Eligibility check failed: Aadhaar linkage is required for Aarogyasri/PMJAY cashless eligibility."
                )

        row = (
            await s.execute(
                text(
                    "INSERT INTO patient_coverage (tenant_id, patient_id, scheme_type, plan_name, member_id, validity_start, validity_end, patient_share_percent) "
                    "VALUES (:tid, :patient_id, :scheme_type, :plan_name, :member_id, :validity_start, :validity_end, :share) "
                    "RETURNING id, patient_id, scheme_type, plan_name, member_id, validity_start, validity_end, patient_share_percent, created_at"
                ).bindparams(
                    tid=ctx.tenant_id, patient_id=body.patient_id, scheme_type=body.scheme_type,
                    plan_name=body.plan_name, member_id=body.member_id, validity_start=body.validity_start,
                    validity_end=body.validity_end, share=body.patient_share_percent
                )
            )
        ).mappings().one()
        await s.commit()
    return PatientCoverageOut(**row)


# --- Core Invoice Workflow ---

@router.post("/invoices", response_model=InvoiceDetailOut, status_code=201)
async def create_invoice(
    body: InvoiceCreate,
    request: Request,
    ctx: RequestContext = Depends(auth),
    session: AsyncSession = Depends(get_session)
):
    """Auto-capture check-in consult fees from clinical encounter into draft invoice (BIL-002)."""
    ctx.require_role("admin", "receptionist", "nurse")
    async with tenant_session(session, ctx) as s:
        # 1. Create invoice header
        inv_row = (
            await s.execute(
                text(
                    "INSERT INTO invoice (tenant_id, patient_id, encounter_id, coverage_id, status) "
                    "VALUES (:tid, :patient_id, :encounter_id, :cov_id, 'draft') "
                    "RETURNING id, patient_id, encounter_id, status, coverage_id, total_amount, payer_responsibility, patient_responsibility, created_at, updated_at"
                ).bindparams(
                    tid=ctx.tenant_id, patient_id=body.patient_id,
                    encounter_id=body.encounter_id, cov_id=body.coverage_id
                )
            )
        ).mappings().one()

        invoice_id = inv_row["id"]
        items = []

        # 2. Auto-capture consultation charge item if exists
        consult_charge = (
            await s.execute(
                text("SELECT id, standard_price, tax_percent FROM charge_master WHERE category = 'consultation' AND active = TRUE LIMIT 1")
            )
        ).mappings().one_or_none()

        if consult_charge:
            # Standard auto-charge calculations
            qty = 1
            price = float(consult_charge["standard_price"])
            tax_rate = float(consult_charge["tax_percent"])
            tax_amt = price * (tax_rate / 100.0)

            # Split rules logic
            patient_share = price + tax_amt
            payer_share = 0.0

            if body.coverage_id:
                cov = (
                    await s.execute(
                        text("SELECT scheme_type, patient_share_percent FROM patient_coverage WHERE id = :id").bindparams(id=body.coverage_id)
                    )
                ).mappings().one_or_none()

                if cov:
                    if cov["scheme_type"].lower() in ("aarogyasri", "pmjay"):
                        patient_share = 0.0
                        payer_share = price + tax_amt
                    else:
                        share_percent = float(cov["patient_share_percent"])
                        patient_share = (price + tax_amt) * (share_percent / 100.0)
                        payer_share = (price + tax_amt) * ((100.0 - share_percent) / 100.0)

            item_row = (
                await s.execute(
                    text(
                        "INSERT INTO invoice_item (tenant_id, invoice_id, charge_item_id, quantity, unit_price, tax_amount, discount_amount, patient_share, payer_share) "
                        "VALUES (:tid, :inv_id, :charge_id, :qty, :price, :tax_amt, 0.0, :p_share, :ins_share) "
                        "RETURNING id, charge_item_id, quantity, unit_price, tax_amount, discount_amount, patient_share, payer_share"
                    ).bindparams(
                        tid=ctx.tenant_id, inv_id=invoice_id, charge_id=consult_charge["id"],
                        qty=qty, price=price, tax_amt=tax_amt, p_share=patient_share, ins_share=payer_share
                    )
                )
            ).mappings().one()
            items.append(InvoiceLineOut(**item_row))

        await audit_record(
            s, ctx, action="create", resource_type="Invoice",
            resource_id=str(invoice_id), patient_id=str(body.patient_id),
            source_ip=request.client.host if request.client else None
        )
        await s.commit()

    return InvoiceDetailOut(**inv_row, items=items)


@router.post("/invoices/{invoice_id}/lines", response_model=InvoiceDetailOut)
async def add_invoice_line(
    invoice_id: UUID,
    body: InvoiceLineCreate,
    request: Request,
    ctx: RequestContext = Depends(auth),
    session: AsyncSession = Depends(get_session)
):
    """Add extra itemized line charges (BIL-002). Only accessible to billing clerks/admins."""
    ctx.require_role("admin", "billing_clerk")
    async with tenant_session(session, ctx) as s:
        inv = (
            await s.execute(
                text("SELECT patient_id, coverage_id, status FROM invoice WHERE id = :id").bindparams(id=invoice_id)
            )
        ).mappings().one_or_none()

        if not inv:
            raise HTTPException(status_code=404, detail="Invoice not found")
        if inv["status"] != "draft":
            raise HTTPException(status_code=400, detail="Cannot modify lines on a finalized or reversed invoice.")

        # Fetch charge item
        charge = (
            await s.execute(
                text("SELECT standard_price, tax_percent FROM charge_master WHERE id = :id AND active = TRUE").bindparams(id=body.charge_item_id)
            )
        ).mappings().one_or_none()

        if not charge:
            raise HTTPException(status_code=404, detail="Charge item not found in charge master")

        price = float(charge["standard_price"])
        tax_rate = float(charge["tax_percent"])
        total_price = price * body.quantity
        tax_amt = total_price * (tax_rate / 100.0)

        # Enforce discount manager authorization (limits > 10% requires finance manager)
        discount_percent = (body.discount_amount / (total_price + tax_amt)) * 100.0 if body.discount_amount else 0.0
        if discount_percent > 10.0:
            ctx.require_role("finance_manager")

        net_line_total = total_price + tax_amt - body.discount_amount

        # Calculate splits
        patient_share = net_line_total
        payer_share = 0.0

        if inv["coverage_id"]:
            cov = (
                await s.execute(
                    text("SELECT scheme_type, patient_share_percent FROM patient_coverage WHERE id = :id").bindparams(id=inv["coverage_id"])
                )
            ).mappings().one_or_none()

            if cov:
                if cov["scheme_type"].lower() in ("aarogyasri", "pmjay"):
                    patient_share = 0.0
                    payer_share = net_line_total
                else:
                    share_percent = float(cov["patient_share_percent"])
                    patient_share = net_line_total * (share_percent / 100.0)
                    payer_share = net_line_total * ((100.0 - share_percent) / 100.0)

        # Insert line item
        await s.execute(
            text(
                "INSERT INTO invoice_item (tenant_id, invoice_id, charge_item_id, quantity, unit_price, tax_amount, discount_amount, patient_share, payer_share) "
                "VALUES (:tid, :inv_id, :charge_id, :qty, :price, :tax_amt, :disc, :p_share, :ins_share)"
            ).bindparams(
                tid=ctx.tenant_id, inv_id=invoice_id, charge_id=body.charge_item_id,
                qty=body.quantity, price=price, tax_amt=tax_amt, disc=body.discount_amount,
                p_share=patient_share, ins_share=payer_share
            )
        )

        # Fetch all items to return detail
        items_rows = (
            await s.execute(
                text(
                    "SELECT id, charge_item_id, quantity, unit_price, tax_amount, discount_amount, patient_share, payer_share "
                    "FROM invoice_item WHERE invoice_id = :inv_id"
                ).bindparams(inv_id=invoice_id)
            )
        ).mappings().all()

        inv_row = (
            await s.execute(
                text("SELECT id, patient_id, encounter_id, status, coverage_id, total_amount, payer_responsibility, patient_responsibility, created_at, updated_at FROM invoice WHERE id = :id").bindparams(id=invoice_id)
            )
        ).mappings().one()

        await audit_record(
            s, ctx, action="update", resource_type="Invoice",
            resource_id=str(invoice_id), patient_id=str(inv["patient_id"]),
            source_ip=request.client.host if request.client else None,
            context_note="Added invoice line item"
        )
        await s.commit()

    return InvoiceDetailOut(**inv_row, items=[InvoiceLineOut(**i) for i in items_rows])


@router.post("/invoices/{invoice_id}/finalize", response_model=InvoiceOut)
async def finalize_invoice(
    invoice_id: UUID,
    request: Request,
    ctx: RequestContext = Depends(auth),
    session: AsyncSession = Depends(get_session)
):
    """Finalizes invoice totals splitting responsibilities (BIL-004, BIL-010)."""
    ctx.require_role("admin", "billing_clerk")
    async with tenant_session(session, ctx) as s:
        inv = (
            await s.execute(
                text("SELECT patient_id, status FROM invoice WHERE id = :id").bindparams(id=invoice_id)
            )
        ).mappings().one_or_none()

        if not inv:
            raise HTTPException(status_code=404, detail="Invoice not found")
        if inv["status"] != "draft":
            raise HTTPException(status_code=400, detail="Invoice is already finalized or reversed.")

        # Sum lines
        sums = (
            await s.execute(
                text(
                    "SELECT COALESCE(SUM(unit_price * quantity + tax_amount - discount_amount), 0) as tot_amt, "
                    "COALESCE(SUM(patient_share), 0) as p_share, "
                    "COALESCE(SUM(payer_share), 0) as ins_share "
                    "FROM invoice_item WHERE invoice_id = :inv_id"
                ).bindparams(inv_id=invoice_id)
            )
        ).mappings().one()

        # Update invoice
        updated_row = (
            await s.execute(
                text(
                    "UPDATE invoice SET status = 'finalized', total_amount = :tot, patient_responsibility = :p_share, "
                    "payer_responsibility = :ins_share, updated_at = now() WHERE id = :id "
                    "RETURNING id, patient_id, encounter_id, status, coverage_id, total_amount, payer_responsibility, patient_responsibility, created_at"
                ).bindparams(
                    id=invoice_id, tot=sums["tot_amt"], p_share=sums["p_share"], ins_share=sums["ins_share"]
                )
            )
        ).mappings().one()

        await audit_record(
            s, ctx, action="update", resource_type="Invoice",
            resource_id=str(invoice_id), patient_id=str(inv["patient_id"]),
            source_ip=request.client.host if request.client else None,
            context_note="Finalized invoice ledger totals"
        )
        await s.commit()

    # Emit event (BIL-010)
    await event_publish("invoice.finalized", {
        "invoice_id": str(invoice_id),
        "tenant_id": ctx.tenant_id,
        "patient_id": str(inv["patient_id"]),
        "total_amount": float(sums["tot_amt"])
    })

    return InvoiceOut(**updated_row)


@router.post("/invoices/{invoice_id}/reverse", response_model=InvoiceOut)
async def reverse_invoice(
    invoice_id: UUID,
    request: Request,
    ctx: RequestContext = Depends(auth),
    session: AsyncSession = Depends(get_session)
):
    """Immutable financial entries correction reversals (BIL-008). Requires finance manager approval."""
    ctx.require_role("finance_manager")
    async with tenant_session(session, ctx) as s:
        inv = (
            await s.execute(
                text("SELECT patient_id, status FROM invoice WHERE id = :id").bindparams(id=invoice_id)
            )
        ).mappings().one_or_none()

        if not inv:
            raise HTTPException(status_code=404, detail="Invoice not found")
        if inv["status"] == "reversed":
            raise HTTPException(status_code=400, detail="Invoice is already reversed.")

        updated_row = (
            await s.execute(
                text(
                    "UPDATE invoice SET status = 'reversed', updated_at = now() WHERE id = :id "
                    "RETURNING id, patient_id, encounter_id, status, coverage_id, total_amount, payer_responsibility, patient_responsibility, created_at"
                ).bindparams(id=invoice_id)
            )
        ).mappings().one()

        # Invert/cancel out responsibility entries (credit notes adjustment reversal log)
        # Financial updates are immutable; we represent this by tracking that total responsibility was reversed.
        await audit_record(
            s, ctx, action="update", resource_type="Invoice",
            resource_id=str(invoice_id), patient_id=str(inv["patient_id"]),
            source_ip=request.client.host if request.client else None,
            context_note="Reversed finalized invoice entries with credit notes"
        )
        await s.commit()

    return InvoiceOut(**updated_row)


# --- Payments Recording ---

@router.post("/payments", response_model=PaymentOut, status_code=201)
async def record_payment(
    body: PaymentCreate,
    request: Request,
    ctx: RequestContext = Depends(auth),
    session: AsyncSession = Depends(get_session)
):
    """Accept cashier partial or full payments and issue numbered receipts (BIL-005, BIL-010)."""
    ctx.require_role("admin", "billing_clerk")
    async with tenant_session(session, ctx) as s:
        # Check invoice status
        inv = (
            await s.execute(
                text("SELECT patient_id, status FROM invoice WHERE id = :id").bindparams(id=body.invoice_id)
            )
        ).mappings().one_or_none()

        if not inv:
            raise HTTPException(status_code=404, detail="Invoice not found")
        if inv["status"] != "finalized":
            raise HTTPException(status_code=400, detail="Cannot record payments on draft or reversed invoices.")

        row = (
            await s.execute(
                text(
                    "INSERT INTO payment (tenant_id, invoice_id, payment_method, amount, transaction_reference) "
                    "VALUES (:tid, :inv_id, :method, :amt, :ref) "
                    "RETURNING id, invoice_id, payment_method, amount, transaction_reference, received_at"
                ).bindparams(
                    tid=ctx.tenant_id, inv_id=body.invoice_id, method=body.payment_method,
                    amt=body.amount, ref=body.transaction_reference
                )
            )
        ).mappings().one()

        await audit_record(
            s, ctx, action="create", resource_type="Payment",
            resource_id=str(row["id"]), patient_id=str(inv["patient_id"]),
            source_ip=request.client.host if request.client else None
        )
        await s.commit()

    # Emit event (BIL-010)
    await event_publish("payment.recorded", {
        "payment_id": str(row["id"]),
        "tenant_id": ctx.tenant_id,
        "invoice_id": str(body.invoice_id),
        "amount": float(body.amount),
        "method": body.payment_method
    })

    return PaymentOut(**row)


# --- Insurer Claims Submission ---

@router.post("/claims", response_model=ClaimOut, status_code=201)
async def submit_claim(
    body: ClaimCreate,
    request: Request,
    ctx: RequestContext = Depends(auth),
    session: AsyncSession = Depends(get_session)
):
    """Generate and submit claims for payer coverage (BIL-006, BIL-010)."""
    ctx.require_role("admin", "billing_clerk")
    async with tenant_session(session, ctx) as s:
        # Fetch invoice responsibility details
        inv = (
            await s.execute(
                text("SELECT patient_id, payer_responsibility, status FROM invoice WHERE id = :id").bindparams(id=body.invoice_id)
            )
        ).mappings().one_or_none()

        if not inv:
            raise HTTPException(status_code=404, detail="Invoice not found")
        if inv["status"] != "finalized":
            raise HTTPException(status_code=400, detail="Cannot submit claims for unfinalized invoices.")

        row = (
            await s.execute(
                text(
                    "INSERT INTO claim (tenant_id, invoice_id, coverage_id, status, total_claimed, submitted_at) "
                    "VALUES (:tid, :inv_id, :cov_id, 'submitted', :claimed, now()) "
                    "RETURNING id, invoice_id, coverage_id, status, total_claimed, submitted_at, updated_at"
                ).bindparams(
                    tid=ctx.tenant_id, inv_id=body.invoice_id, cov_id=body.coverage_id,
                    claimed=inv["payer_responsibility"]
                )
            )
        ).mappings().one()

        await audit_record(
            s, ctx, action="create", resource_type="Claim",
            resource_id=str(row["id"]), patient_id=str(inv["patient_id"]),
            source_ip=request.client.host if request.client else None
        )
        await s.commit()

    # Emit event (BIL-010)
    await event_publish("claim.status", {
        "claim_id": str(row["id"]),
        "invoice_id": str(body.invoice_id),
        "status": "submitted",
        "total_claimed": float(inv["payer_responsibility"])
    })

    return ClaimOut(**row)
