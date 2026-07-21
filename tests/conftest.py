"""Shared test fixtures.

The isolation test needs two tenants provisioned before it can prove RLS scoping.
Provisioning is an admin action (the app role hms_app has SELECT on `tenant`,
not INSERT — least privilege, PLT-002), so it runs here as the superuser once
per session. The tests themselves connect as hms_app so RLS actually applies.
"""
from __future__ import annotations

import os

import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

ADMIN_DATABASE_URL = os.environ.get(
    "SEED_DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres_change_me@localhost:5432/hms",
)


@pytest_asyncio.fixture(scope="function", autouse=True)
async def _provision_test_tenants():
    """Ensure the two test tenants exist. Idempotent so it's safe to re-run."""
    engine = create_async_engine(ADMIN_DATABASE_URL)
    sessionmaker_ = async_sessionmaker(engine, expire_on_commit=False)
    async with sessionmaker_() as s:
        await s.execute(text(
            "INSERT INTO tenant (id, name) VALUES "
            "('t_a','Tenant A'),('t_b','Tenant B') "
            "ON CONFLICT (id) DO NOTHING"
        ))
        await s.commit()
    await engine.dispose()
    yield
