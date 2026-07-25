
import hmac
import hashlib
import logging
import random
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from hms_auth import auth
from hms_tenancy import RequestContext, tenant_session

from ..db import get_session
from .int_schemas import (
    HL7MessagePayload,
    HL7MessageResponse,
    MockChargeRequest,
    MockChargeResponse,
    WebhookSubscriptionCreate,
    WebhookSubscriptionOut
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/int", tags=["int"])


# --- Webhooks Subscription Manager ---

@router.post("/webhooks/subscriptions", response_model=WebhookSubscriptionOut, status_code=201)
async def create_webhook_subscription(
    body: WebhookSubscriptionCreate,
    ctx: RequestContext = Depends(auth),
    session: AsyncSession = Depends(get_session)
):
    """Register tenant webhook endpoints (INT-006)."""
    ctx.require_role("admin")
    async with tenant_session(session, ctx) as s:
        row = (
            await s.execute(
                text(
                    "INSERT INTO webhook_subscription (tenant_id, event_type, url, secret_key, active) "
                    "VALUES (:tid, :event, :url, :secret, TRUE) "
                    "RETURNING id, event_type, url, secret_key, active"
                ).bindparams(
                    tid=ctx.tenant_id, event=body.event_type, url=body.url, secret=body.secret_key
                )
            )
        ).mappings().one()
        await s.commit()

    return WebhookSubscriptionOut(**row)


@router.get("/webhooks/subscriptions", response_model=list[WebhookSubscriptionOut])
async def list_webhook_subscriptions(
    ctx: RequestContext = Depends(auth),
    session: AsyncSession = Depends(get_session)
):
    ctx.require_role("admin")
    async with tenant_session(session, ctx) as s:
        rows = (
            await s.execute(
                text("SELECT id, event_type, url, secret_key, active FROM webhook_subscription WHERE active = TRUE")
            )
        ).mappings().all()
        await s.commit()

    return [WebhookSubscriptionOut(**r) for r in rows]


# --- HMAC SHA256 Payload Signature Tool (INT-006) ---

def generate_webhook_signature(payload: str, secret: str, timestamp: str) -> str:
    """Computes HMAC SHA256 signature combining webhook payload with timestamp to guard replay attacks."""
    message = f"{timestamp}.{payload}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


# --- HL7 v2 Translators Engine (INT-001, INT-002) ---

@router.post("/hl7/inbound", response_model=HL7MessageResponse)
async def ingest_inbound_hl7_oru(
    body: HL7MessagePayload,
    ctx: RequestContext = Depends(auth),
    session: AsyncSession = Depends(get_session)
):
    """Translates inbound HL7 ORU result message and routes internally to lab observation structures (INT-002)."""
    ctx.require_role("admin", "operator")
    async with tenant_session(session, ctx) as s:
        segments = [line.strip() for line in body.message_text.strip().split("\n") if line.strip()]
        
        msh = None
        pid = None
        obx = None

        for seg in segments:
            if seg.startswith("MSH|"):
                msh = seg
            elif seg.startswith("PID|"):
                pid = seg
            elif seg.startswith("OBR|"):
                pass
            elif seg.startswith("OBX|"):
                obx = seg

        if not msh or not pid or not obx:
            raise HTTPException(status_code=400, detail="Incomplete HL7 ORU segments. MSH, PID, and OBX are required.")

        # Simulating extraction of properties
        # PID|1||PID12345||LastName^FirstName
        pid_parts = pid.split("|")
        pid_parts[3] if len(pid_parts) > 3 else "unknown"

        # OBX|1|NM|883-9^Hemoglobin^LN||14.5|g/dL
        obx_parts = obx.split("|")
        obx_parts[3].split("^")[0] if len(obx_parts) > 3 else "unknown"
        float(obx_parts[5]) if len(obx_parts) > 5 else 0.0
        obx_parts[6] if len(obx_parts) > 6 else ""

        # Map to internal DB models if matching order is found
        # (This handles the translate binding skeleton mock)
        (
            await s.execute(
                text(
                    "INSERT INTO integration_log (tenant_id, direction, message_type, status, payload) "
                    "VALUES (:tid, 'inbound', 'HL7_ORU', 'success', :payload) "
                    "RETURNING id"
                ).bindparams(tid=ctx.tenant_id, payload=body.message_text)
            )
        ).mappings().one()
        await s.commit()

    return HL7MessageResponse(
        status="success",
        parsed_segments=[s.split("|")[0] for s in segments]
    )


@router.post("/hl7/outbound/order")
async def generate_outbound_hl7_orm(
    order_id: UUID,
    ctx: RequestContext = Depends(auth),
    session: AsyncSession = Depends(get_session)
):
    """Maps and constructs outbound HL7 ORM segments from laboratory order details (INT-001)."""
    ctx.require_role("admin", "operator")
    async with tenant_session(session, ctx) as s:
        # Fetch lab order details
        order = (
            await s.execute(
                text(
                    "SELECT lo.id, lo.patient_id, p.family_name, p.given_name "
                    "FROM lab_order lo JOIN patient p ON lo.patient_id = p.id "
                    "WHERE lo.id = :id"
                ).bindparams(id=order_id)
            )
        ).mappings().one_or_none()

        if not order:
            raise HTTPException(status_code=404, detail="Laboratory order not found.")

        # Construct MSH, PID, and ORC segments
        msh = "MSH|^~\\&|HMS_PLT|FACILITY|LIS|LAB_RECEIVER|202607202300||ORM^O01|MSG0001|P|2.3"
        pid = f"PID|1||{order['patient_id']}||{order['family_name']}^{order['given_name']}||||"
        orc = f"ORC|NW|{order['id']}|||||1^once||||||"

        hl7_message = f"{msh}\n{pid}\n{orc}"

        # Write integration log
        await s.execute(
            text(
                "INSERT INTO integration_log (tenant_id, direction, message_type, status, payload) "
                "VALUES (:tid, 'outbound', 'HL7_ORM', 'success', :payload)"
            ).bindparams(tid=ctx.tenant_id, payload=hl7_message)
        )
        await s.commit()

    return {"status": "success", "hl7_message": hl7_message}


# --- Mock Payments Gateway Connector (INT-004) ---

@router.post("/payments/gateway/charge", response_model=MockChargeResponse)
async def gateway_charge(
    body: MockChargeRequest,
    ctx: RequestContext = Depends(auth)
):
    """Reference payment gateway charge authorization/capture mockup (INT-004)."""
    ctx.require_role("admin", "billing", "billing_clerk")
    
    # Simulate payment processor success/failure
    # E.g. decline if method starts with 'decline'
    if body.payment_method_id.startswith("decline"):
        return MockChargeResponse(
            id=uuid4(),
            amount=body.amount,
            status="failed",
            transaction_reference="decline_gateway_err_402"
        )

    return MockChargeResponse(
        id=uuid4(),
        amount=body.amount,
        status="captured",
        transaction_reference=f"txn_{random.randint(100000, 999999)}"
    )
