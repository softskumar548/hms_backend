"""Async SQLAlchemy engine + session factory.

The app connects as the non-superuser role `hms_app` so Row-Level Security is
enforced (superusers bypass RLS). Connection string comes from DATABASE_URL.
"""

import os
import logging
import uuid
from datetime import datetime, UTC
from sqlalchemy import text
from sqlalchemy.engine import RowMapping
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
            except (IOError, OSError) as e:
                logger.debug("Could not load .env file: %s", e)
            break

_load_env_file()

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://hms_app:app_password_change_me@localhost:5432/hms"
    if os.getenv("ENV") == "test"
    else "postgresql+asyncpg://hms_app:app_password_change_me@postgres:5432/hms",
)

from sqlalchemy.pool import NullPool

if os.getenv("ENV") == "test":
    engine = create_async_engine(DATABASE_URL, echo=False, poolclass=NullPool)
else:
    engine = create_async_engine(DATABASE_URL, echo=False, pool_pre_ping=True)

SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


# ---------------------------------------------------------------------------
async def verify_postgres_rls_startup() -> None:
    """Startup check: connect to Postgres, verify 'patient' table RLS (relrowsecurity), refuse to serve otherwise."""
    env_mode = os.getenv("ENV", "development").lower()
    allow_mock = os.getenv("HMS_ALLOW_MOCK_DB", "false").lower() == "true"

    try:
        async with SessionLocal() as session:
            res = (await session.execute(
                text("SELECT relrowsecurity FROM pg_class WHERE relname = 'patient'")
            )).scalar()

            if res is not True:
                raise RuntimeError(
                    "POSTGRES RLS CHECK FAILED: 'patient' table relrowsecurity is False or missing. Refusing to serve requests without active Row-Level Security."
                )
            logger.info("✓ PostgreSQL startup check passed: 'patient' table relrowsecurity is ACTIVE.")
    except Exception as e:
        if env_mode == "test" and allow_mock:
            logger.warning(
                f"⚠️ CRITICAL SECURITY WARNING: Postgres RLS startup check failed ({e}). Mock DB allowed in ENV=test with HMS_ALLOW_MOCK_DB=true."
            )
            return
        logger.error(
            f"FATAL: PostgreSQL RLS startup check failed: {e}. Refusing to start service."
        )
        raise RuntimeError(
            f"PostgreSQL RLS startup check failed: {e}. Refusing to start service without verified RLS database."
        )


async def get_session() -> AsyncSession:
    """FastAPI dependency: yields a session."""
    async with SessionLocal() as session:
        yield session

