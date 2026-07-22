"""Async SQLAlchemy engine + session factory.

The app connects as the non-superuser role `hms_app` so Row-Level Security is
enforced (superusers bypass RLS). Connection string comes from DATABASE_URL.
"""
from __future__ import annotations

import os
import logging
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

logger = logging.getLogger(__name__)

def _load_env_file() -> None:
    import os
    from pathlib import Path
    cur = Path(__file__).resolve().parent
    for parent in [cur] + list(cur.parents):
        env_path = parent / ".env"
        if env_path.is_file():
            try:
                with open(env_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#"):
                            continue
                        if "=" in line:
                            k, v = line.split("=", 1)
                            os.environ.setdefault(k.strip(), v.strip())
            except Exception:
                pass
            break

_load_env_file()

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://hms_app:app_password_change_me@postgres:5432/hms",
)

engine = create_async_engine(DATABASE_URL, echo=False, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


# NOTE (PLT-002 / integration gate): there is deliberately NO in-memory mock
# session fallback here. Silently degrading to a mock when Postgres is down
# bypasses Row-Level Security (no real tenant isolation) — a patient-data leak
# class of bug. The app must fail loud instead: `verify_database_safety()` in
# db_guard.py refuses startup without a verified RLS-enforcing database, and
# get_session() below never swallows connection errors. Unit tests inject their
# own AsyncMock via FastAPI dependency_overrides, so no mock session type needs
# to exist in this runtime module.


async def get_session() -> AsyncSession:
    """FastAPI dependency: yield a real RLS-enforcing session.

    Fail loud (PLT-002): if Postgres is unreachable this raises and the request
    fails, rather than silently degrading to a no-isolation mock. Database
    availability and RLS enforcement are verified once at startup by
    db_guard.verify_database_safety(); per-request we just hand out a session.
    Unit tests override this dependency with an AsyncMock.
    """
    async with SessionLocal() as session:
        yield session

