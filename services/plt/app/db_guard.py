"""db_guard.py — fail-loud startup verification (PLT-002 / ADR-0003).

Purpose: the app must REFUSE to serve if the real database, RLS, or the
non-superuser role are not in place. This permanently closes the
MockAsyncSession class of failure (silent degradation to no-isolation).

Wire-up in main.py:

    from contextlib import asynccontextmanager
    from .db_guard import verify_database_safety

    @asynccontextmanager
    async def lifespan(app):
        await verify_database_safety()   # raises -> app never starts
        yield

    app = FastAPI(..., lifespan=lifespan)

The MockAsyncSession runtime fallback has been removed from db.py; the unit
suites inject their own AsyncMock via FastAPI dependency_overrides, so nothing
in app runtime code degrades to a mock session any more.
"""
from __future__ import annotations

import logging
import os

from sqlalchemy import text

log = logging.getLogger("hms.db_guard")

# Every tenant-scoped table MUST appear here. Add to this list in the same PR
# that adds a new tenant-scoped table — the guard turns forgetting into a
# startup failure instead of a silent isolation hole. Kept in sync with the
# ENABLE+FORCE ROW LEVEL SECURITY statements in infra/postgres/init.sql and the
# Alembic migrations (the `tenant` registry table is global and intentionally
# excluded — it is not tenant-scoped).
RLS_PROTECTED_TABLES: list[str] = [
    "allergy_intolerance",
    "appointment",
    "appointment_prerequisite",
    "audit_event",
    "charge_master",
    "claim",
    "clinical_note",
    "clinical_note_addendum",
    "condition",
    "encounter",
    "encounter_document",
    "integration_log",
    "invoice",
    "invoice_item",
    "lab_catalog",
    "lab_order",
    "lab_order_item",
    "lab_result",
    "lab_unmatched_result",
    "medication_catalog",
    "medication_statement",
    "patient",
    "patient_consent",
    "patient_coverage",
    "payment",
    "portal_invitation",
    "portal_message",
    "portal_proxy",
    "portal_questionnaire",
    "portal_user",
    "practitioner",
    "practitioner_availability",
    "prerequisite_definition",
    "prescription",
    "prescription_favorite",
    "prescription_item",
    "prescription_override",
    "room",
    "service",
    "site",
    "tenant_formulary",
    "vital_sign",
    "webhook_delivery_log",
    "webhook_subscription",
]


class DatabaseSafetyError(RuntimeError):
    """Raised when the runtime database does not meet safety requirements."""


async def verify_database_safety() -> None:
    """Connect to the real DB and verify the safety invariants. Raise on any
    failure so the process exits (or health stays red) instead of serving."""
    # Import here so a broken engine config fails inside the guard, loudly.
    from .db import engine

    if os.environ.get("HMS_ALLOW_MOCK_DB") == "true":
        # Only ever legal in pure unit-test runs.
        if os.environ.get("ENV") != "test":
            raise DatabaseSafetyError(
                "HMS_ALLOW_MOCK_DB=true outside ENV=test — refusing to start."
            )
        log.warning("MOCK DB ALLOWED (ENV=test). Never valid outside unit tests.")
        return

    try:
        async with engine.connect() as conn:
            # 1) Real database reachable.
            await conn.execute(text("SELECT 1"))

            # 2) We are NOT a superuser/bypass role (superusers bypass RLS).
            row = (await conn.execute(text(
                "SELECT rolsuper OR rolbypassrls AS bypass "
                "FROM pg_roles WHERE rolname = current_user"))).one()
            if row.bypass:
                raise DatabaseSafetyError(
                    f"App connected as '{await _current_user(conn)}' which bypasses "
                    "RLS. Connect as the restricted app role (hms_app)."
                )

            # 3) RLS enabled AND forced on every protected table.
            res = (await conn.execute(text(
                "SELECT relname, relrowsecurity, relforcerowsecurity "
                "FROM pg_class WHERE relname = ANY(:tables)"
            ).bindparams(tables=RLS_PROTECTED_TABLES))).mappings().all()
            found = {r["relname"]: r for r in res}
            problems = []
            for t in RLS_PROTECTED_TABLES:
                r = found.get(t)
                if r is None:
                    problems.append(f"table '{t}' missing")
                elif not (r["relrowsecurity"] and r["relforcerowsecurity"]):
                    problems.append(f"table '{t}' lacks ENABLE+FORCE ROW LEVEL SECURITY")
            if problems:
                raise DatabaseSafetyError("RLS verification failed: " + "; ".join(problems))

            # 4) Fail-closed probe: with no tenant context, protected tables
            #    must return zero rows.
            for t in ("patient",):
                count = (await conn.execute(text(f"SELECT count(*) FROM {t}"))).scalar()
                if count and count > 0:
                    raise DatabaseSafetyError(
                        f"Fail-closed violated: '{t}' returned rows with no tenant "
                        "context. RLS policy is wrong or app role is privileged."
                    )
    except DatabaseSafetyError:
        raise
    except Exception as exc:  # connection refused, auth failure, etc.
        raise DatabaseSafetyError(
            f"Database unreachable or unverifiable at startup: {exc!r}. "
            "Refusing to serve without the real database."
        ) from exc

    log.info("db_guard: database safety verified (RLS enforced, fail-closed OK)")


async def _current_user(conn) -> str:
    return (await conn.execute(text("SELECT current_user"))).scalar()
