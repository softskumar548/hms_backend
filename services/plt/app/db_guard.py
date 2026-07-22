"""db_guard.py — fail-loud startup verification (services/plt/app/db_guard.py).

Purpose: the app must REFUSE to serve if the real database, RLS, or the
non-superuser role are not in place. This permanently closes the
MockAsyncSession class of failure (silent degradation to no-isolation).
"""
from __future__ import annotations

import logging
import os

from sqlalchemy import text

log = logging.getLogger("hms.db_guard")

# Every tenant-scoped table MUST appear here (44 tenant tables covered).
RLS_PROTECTED_TABLES: list[str] = [
    "patient",
    "audit_event",
    "site",
    "room",
    "service",
    "clinical_service",
    "practitioner",
    "tenant_config",
    "tenant_invitation",
    "migration_staging",
    "readiness_checklist",
    "subscription_invoice",
    "cashless_claim",
    "appointment",
    "appointment_prerequisite",
    "encounter",
    "clinical_note",
    "vital_sign",
    "problem",
    "prescription",
    "prescription_item",
    "order_catalog_item",
    "order",
    "order_item",
    "lab_result",
    "analyte_result",
    "charge_master",
    "invoice",
    "invoice_line",
    "payment",
    "patient_coverage",
    "claim",
    "patient_portal_user",
    "portal_intake_form",
    "ops_metric",
    "referral_analytic",
    "referral",
    "referrer",
    "followup_booking",
    "prerequisite_library",
    "referral_commission",
    "referral_prerequisite",
    "followup_prerequisite",
    "abha_linkage",
    "aarogyasri_eligibility",
    "medication_catalog",
    "lab_catalog",
    "lab_order",
    "webhook_subscription",
    "prerequisite_definition",
    "practitioner_availability",
    "clinical_note_addendum",
    "encounter_document",
    "allergy_intolerance",
    "condition",
    "medication_statement",
    "lab_order_item",
    "lab_unmatched_result",
    "tenant_formulary",
    "prescription_override",
    "prescription_favorite",
    "invoice_item",
    "portal_invitation",
    "portal_user",
    "portal_questionnaire",
    "portal_proxy",
    "portal_message",
    "webhook_delivery_log",
    "integration_log"
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
