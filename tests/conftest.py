import os
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import text

# Explicitly set test environment variables for unit test suite execution
os.environ["ENV"] = "test"
os.environ["HMS_ALLOW_MOCK_DB"] = "true"

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://hms_app:app_password_change_me@localhost:5432/hms",
)

@pytest_asyncio.fixture(scope="session", autouse=True)
async def verify_no_leftover_test_tenants():
    """H3 CI GATE: Enforces zero test-tenant clutter/debris in PostgreSQL database at session end.

    Runs automatically after all tests complete, querying the `tenant` table to assert
    that no leftover ephemeral test tenants remain in PostgreSQL.
    """
    engine = create_async_engine(DATABASE_URL)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    SEED_TENANTS = {"apollo", "apollo_vizag", "kims_guntur", "gsl_rajahmundry", "care_vizag", "medisys_kakinada", "kims", "NIMS_BLR", "t_a", "t_b"}
    
    # Pre-test cleanup of any leftover test tenants from previous interrupted test runs
    try:
        async with session_factory() as s:
            await s.execute(text("DELETE FROM tenant WHERE id LIKE 'test_%' OR id LIKE 't_%' OR name LIKE 'Test%' OR name LIKE 'Temp%' OR name LIKE 'Suspension%'"))
            await s.commit()
    except Exception:
        pass

    yield

    try:
        async with session_factory() as s:
            res = await s.execute(text("SELECT id, name FROM tenant"))
            tenants = res.all()

            leftover_test_tenants = [
                (tid, name) for tid, name in tenants
                if tid not in SEED_TENANTS and not tid.startswith("hosp_n4_") and (
                    tid.startswith("t_") or
                    tid.startswith("test") or
                    "Test" in name or
                    "E2E" in name or
                    "Target" in name
                )
            ]
            assert len(leftover_test_tenants) == 0, f"H3 CI Gate Failure: Leftover test tenants found in DB after test run: {leftover_test_tenants}"
    finally:
        await engine.dispose()

