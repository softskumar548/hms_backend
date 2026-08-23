"""test_startup_guard.py — drop into tests/.

Asserts the fail-loud contract: with an unreachable database, the safety guard
raises and the app must not serve. This is the permanent CI defense against the
MockAsyncSession class of bug (silent degradation to no-isolation).
Run in CI alongside the isolation test.
"""
from __future__ import annotations

import importlib
import os

import pytest


@pytest.mark.asyncio
async def test_refuses_to_start_without_database(monkeypatch):
    # Point at a port where nothing listens; ensure no mock escape hatch.
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://hms_app:wrong@127.0.0.1:59999/hms",
    )
    monkeypatch.delenv("HMS_ALLOW_MOCK_DB", raising=False)

    # Re-import db so the engine picks up the bad URL.
    import app.db as db
    importlib.reload(db)
    from app.db_guard import DatabaseSafetyError, verify_database_safety

    try:
        with pytest.raises(DatabaseSafetyError):
            await verify_database_safety()
    finally:
        monkeypatch.undo()
        importlib.reload(db)


@pytest.mark.asyncio
async def test_mock_db_flag_rejected_outside_test_env(monkeypatch):
    monkeypatch.setenv("HMS_ALLOW_MOCK_DB", "true")
    monkeypatch.setenv("ENV", "production")
    from app.db_guard import DatabaseSafetyError, verify_database_safety

    try:
        with pytest.raises(DatabaseSafetyError):
            await verify_database_safety()
    finally:
        monkeypatch.undo()


def test_mock_session_not_importable_from_runtime():
    """MockAsyncSession must live under tests/, not in app runtime modules."""
    import pkgutil
    import app as app_pkg

    offenders = []
    for mod in pkgutil.walk_packages(app_pkg.__path__, prefix="app."):
        try:
            m = importlib.import_module(mod.name)
        except Exception:
            continue
        if hasattr(m, "MockAsyncSession"):
            offenders.append(mod.name)
    assert not offenders, (
        f"MockAsyncSession found in runtime modules: {offenders}. "
        "Move it under tests/ — it must not be reachable from app code."
    )
