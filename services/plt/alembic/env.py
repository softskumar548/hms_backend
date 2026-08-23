"""Alembic env — async engine, URL from ALEMBIC_DATABASE_URL (falls back to
SEED_DATABASE_URL, then alembic.ini). Migrations run privileged DDL so the URL
must be an admin role, not the runtime hms_app role.
"""
from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

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
            except (IOError, OSError):
                pass
            break

_load_env_file()

_url = (
    os.environ.get("ALEMBIC_DATABASE_URL")
    or os.environ.get("SEED_DATABASE_URL")
    or config.get_main_option("sqlalchemy.url")
)
config.set_main_option("sqlalchemy.url", _url)

# No SQLAlchemy models yet — schema lives in raw SQL migrations for now, matching
# how init.sql expresses RLS/policies/roles. When models arrive, wire target_metadata.
target_metadata = None


def run_migrations_offline() -> None:
    context.configure(
        url=_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
