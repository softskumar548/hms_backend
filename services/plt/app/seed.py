"""Seed two demo tenants with synthetic patients so isolation can be tried by hand.

Run: docker compose exec plt python -m app.seed

SYNTHETIC DATA ONLY — never seed real patient data into dev/staging.

Provisioning tenants is a platform-admin action (the app role hms_app has SELECT on
`tenant`, not INSERT — least privilege, PLT-002). Patient inserts run as hms_app so
RLS is exercised end-to-end, exactly like a real clinical write.
"""
from __future__ import annotations

import asyncio
import os

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from .db import SessionLocal

# Superuser URL, used only for the admin operation of creating tenants. In prod
# this becomes a dedicated platform-admin role, not the postgres superuser.
SEED_DATABASE_URL = os.environ.get(
    "SEED_DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres_change_me@postgres:5432/hms",
)


async def _provision_tenants() -> None:
    admin_engine = create_async_engine(SEED_DATABASE_URL)
    admin_session = async_sessionmaker(admin_engine, expire_on_commit=False)
    async with admin_session() as s:
        await s.execute(text(
            "INSERT INTO tenant (id, name) VALUES "
            "('apollo','Apollo Clinic (demo)'),('kims','KIMS Hospital (demo)') "
            "ON CONFLICT (id) DO NOTHING"
        ))
        await s.commit()
    await admin_engine.dispose()


async def _insert_demo_patients() -> None:
    people_by_tenant = {
        "apollo": [("Ravi", "Sharma"), ("Priya", "Gupta")],
        "kims":   [("Anil", "Khan"), ("Sana", "Iyer")],
    }
    async with SessionLocal() as s:  # hms_app role — RLS applies
        for tid, people in people_by_tenant.items():
            # SET LOCAL binds this transaction to the tenant so the WITH CHECK on
            # the patient RLS policy allows the insert.
            await s.execute(text("SET LOCAL app.tenant_id = :t").bindparams(t=tid))
            for given, family in people:
                await s.execute(text(
                    "INSERT INTO patient (tenant_id, given_name, family_name, created_by) "
                    "VALUES (:t, :g, :f, 'seed')"
                ).bindparams(t=tid, g=given, f=family))
        await s.commit()


async def seed() -> None:
    await _provision_tenants()
    await _insert_demo_patients()
    print("seeded tenants: apollo, kims (synthetic patients)")


if __name__ == "__main__":
    asyncio.run(seed())
