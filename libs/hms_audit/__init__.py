"""hms_audit — append-only audit trail (PLT-005).

Every create/read/update/export of patient-identifiable data must be recorded.
Events are insert-only; there is no update or delete path by design. Retention
(default 7 years) is enforced by a scheduled job, not by mutating rows.
"""
from __future__ import annotations

from datetime import UTC, datetime, timezone
from typing import Optional

from hms_tenancy import RequestContext, current_tenant_id
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def record(
    session: AsyncSession,
    ctx: RequestContext,
    *,
    action: str,          # e.g. "read", "create", "update", "export"
    resource_type: str,   # e.g. "Patient", "Encounter"
    resource_id: str | None = None,
    patient_id: str | None = None,
    source_ip: str | None = None,
    context_note: str | None = None,
) -> None:
    """Write one immutable audit event. Call inside the same transaction as the
    action being audited so the two commit or roll back together."""
    effective_tid = current_tenant_id.get() or ctx.tenant_id
    await session.execute(
        text(
            """
            INSERT INTO audit_event
                (tenant_id, actor_user_id, actor_role, action,
                 resource_type, resource_id, patient_id, source_ip,
                 context_note, occurred_at)
            VALUES
                (:tenant_id, :actor_user_id, :actor_role, :action,
                 :resource_type, :resource_id, :patient_id, :source_ip,
                 :context_note, :occurred_at)
            """
        ).bindparams(
            tenant_id=effective_tid,
            actor_user_id=ctx.user_id,
            actor_role=ctx.role,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            patient_id=patient_id,
            source_ip=source_ip,
            context_note=context_note,
            occurred_at=datetime.now(UTC),
        )
    )
