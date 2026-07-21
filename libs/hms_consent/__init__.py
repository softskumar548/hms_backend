"""hms_consent — patient consent enforcement (PLT-010).

Non-negotiable rule (CLAUDE.md §3.3): check consent before sharing patient data
externally or sending non-essential communications. **Absent consent → suppress
or skip, never proceed.**

Consent is per-patient, per-purpose, and revocable. Grants are append-only
(revocation is an UPDATE that sets `revoked_at`; the original grant row is not
deleted, so we retain the audit trail).

The purpose taxonomy (e.g. "share:abdm", "comms:appointment_reminder",
"comms:marketing", "share:referrer_result") is a **product/clinical decision**
that must be signed off before enabling any purpose in production. This module
only mechanically enforces whatever the caller passes.
"""
from __future__ import annotations

from hms_tenancy import RequestContext
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def has_consent(
    session: AsyncSession,
    ctx: RequestContext,
    *,
    patient_id: str,
    purpose: str,
) -> bool:
    """True iff there is an unrevoked consent grant for (patient, purpose) in the
    current tenant. Session must be already bound to a tenant via
    `hms_tenancy.tenant_session` so RLS scopes the read."""
    row = (
        await session.execute(
            text(
                "SELECT 1 FROM patient_consent "
                "WHERE patient_id = :pid AND purpose = :purpose "
                "AND revoked_at IS NULL "
                "LIMIT 1"
            ).bindparams(pid=patient_id, purpose=purpose)
        )
    ).one_or_none()
    return row is not None


async def require_consent(
    session: AsyncSession,
    ctx: RequestContext,
    *,
    patient_id: str,
    purpose: str,
) -> None:
    """Raise ConsentRequired if the patient has not granted consent for `purpose`.

    Use at the point of external sharing / non-essential comms. For the
    suppression pattern (silently skip when absent), call `has_consent` directly.
    """
    if not await has_consent(session, ctx, patient_id=patient_id, purpose=purpose):
        raise ConsentRequired(patient_id=patient_id, purpose=purpose)


class ConsentRequired(Exception):
    """Raised when a required consent is missing. The API layer should map this
    to a 4xx (typically 451 or 403) and never fall through to the action."""

    def __init__(self, *, patient_id: str, purpose: str) -> None:
        super().__init__(f"consent required: patient={patient_id} purpose={purpose}")
        self.patient_id = patient_id
        self.purpose = purpose
