"""hms_tenancy — request-scoped tenant context and Row-Level Security enforcement.

The single source of truth for "which tenant is this request acting as". Every
DB session opened for a request MUST go through `tenant_session`, which sets the
Postgres session variable `app.tenant_id` that the RLS policies read. If you open
a raw session without it, RLS denies everything — that is intentional.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Context variable to hold request-scoped or context-scoped tenant ID.
current_tenant_id: ContextVar[str] = ContextVar("current_tenant_id", default="")


@dataclass(frozen=True)
class RequestContext:
    """Who this request is acting as. Built by the auth layer (IAM), never trusted
    from client-supplied data such as a header or query param."""
    tenant_id: str
    user_id: str
    role: str

    def require_role(self, *roles: str) -> None:
        if self.role not in roles:
            raise PermissionError(
                f"role '{self.role}' not permitted; requires one of {roles}"
            )


@asynccontextmanager
async def tenant_session(
    session: AsyncSession, ctx: RequestContext
) -> AsyncIterator[AsyncSession]:
    """Bind a DB session to a tenant for the duration of a request.

    Sets `app.tenant_id` so RLS policies (see infra/postgres/rls.sql) scope every
    query. Uses SET LOCAL so the binding is transaction-scoped and cannot leak to
    another request on a pooled connection.
    """
    if not ctx.tenant_id:
        raise ValueError("RequestContext.tenant_id is required for a tenant session")
    
    # Track the active tenant ID in the contextvar for event publishing safety
    token = current_tenant_id.set(ctx.tenant_id)
    # SET LOCAL is transaction-scoped; parameter bound safely to avoid injection.
    await session.execute(
        text("SET LOCAL app.tenant_id = :tid").bindparams(tid=ctx.tenant_id)
    )
    try:
        yield session
    finally:
        current_tenant_id.reset(token)
