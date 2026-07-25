"""hms_config — per-tenant feature flags and config.

Config over customization (CLAUDE.md §6): per-tenant behaviour is a flag or
config value, never a per-customer code branch. Flags live in `tenant.features`
JSONB and are read here through a single accessor so the read path is auditable
and cache-able in one place.

Example:
    if await feature(session, ctx.tenant_id, "ref_commission"):
        ...   # regionally-gated capability

Absent / unset flags return the caller-supplied `default`, defaulting to False
(fail closed for licensable capabilities).
"""

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def feature(
    session: AsyncSession,
    tenant_id: str,
    name: str,
    *,
    default: bool = False,
) -> bool:
    """Return the boolean feature flag `name` for `tenant_id`.

    Reads `tenant.features->>name`. Missing keys / non-boolean values → `default`.
    Caller must pass a session that can read `tenant` (hms_app has SELECT).
    """
    row = (
        await session.execute(
            text("SELECT features FROM tenant WHERE id = :tid").bindparams(tid=tenant_id)
        )
    ).mappings().one_or_none()
    if row is None:
        return default
    features: dict[str, Any] = row["features"] or {}
    val = features.get(name)
    return bool(val) if isinstance(val, bool) else default


async def config_value(
    session: AsyncSession,
    tenant_id: str,
    name: str,
    *,
    default: Any = None,
) -> Any:
    """Return an arbitrary per-tenant config value from `tenant.features`.

    Same store as `feature()` — the JSONB doubles as flags + config. Callers are
    responsible for type-checking the return.
    """
    row = (
        await session.execute(
            text("SELECT features FROM tenant WHERE id = :tid").bindparams(tid=tenant_id)
        )
    ).mappings().one_or_none()
    if row is None:
        return default
    features: dict[str, Any] = row["features"] or {}
    return features.get(name, default)
