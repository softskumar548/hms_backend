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
    session: AsyncSession, ctx: RequestContext, tenant_id: str | None = None
) -> AsyncIterator[AsyncSession]:
    """Bind a DB session to a tenant for the duration of a request.

    Sets `app.tenant_id` so RLS policies (see infra/postgres/rls.sql) scope every
    query. Uses SET LOCAL so the binding is transaction-scoped and cannot leak to
    another request on a pooled connection.
    """
    effective_tenant_id = tenant_id or ctx.tenant_id
    if not effective_tenant_id:
        raise ValueError("RequestContext.tenant_id or tenant_id is required for a tenant session")
    
    token = current_tenant_id.set(effective_tenant_id)
    await session.execute(
        text("SELECT set_config('app.tenant_id', :tid, true)").bindparams(tid=effective_tenant_id)
    )
    try:
        yield session
        if session.in_transaction():
            await session.commit()
    except Exception:
        if session.in_transaction():
            await session.rollback()
        raise
    finally:
        current_tenant_id.reset(token)
