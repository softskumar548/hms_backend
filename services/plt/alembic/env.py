"""Alembic env — async engine, URL from ALEMBIC_DATABASE_URL (falls back to
SEED_DATABASE_URL, then alembic.ini). Migrations run privileged DDL so the URL
must be an admin role, not the runtime hms_app role.
"""
from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool
from sqlalchemy.engine import Connection

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
            except Exception:
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


def run_migrations_online() -> None:
    # Run migrations over the SYNC psycopg2 driver, not asyncpg. These migrations
    # use multi-statement op.execute() DDL blocks; asyncpg prepares every
    # statement and Postgres rejects multiple commands in a prepared statement
    # ("cannot insert multiple commands into a prepared statement"). psycopg2
    # applies the SQL with the simple query protocol (like psql), so the same
    # DDL runs cleanly. The app itself still connects via asyncpg at runtime.
    sync_url = _url.replace("+asyncpg", "+psycopg2")
    connectable = create_engine(sync_url, poolclass=pool.NullPool)
    with connectable.connect() as connection:
        do_run_migrations(connection)
    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
