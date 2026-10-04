"""db_guard.py — fail-loud startup verification (services/plt/app/db_guard.py).

Purpose: the app must REFUSE to serve if the real database, RLS, or the
non-superuser role are not in place. This permanently closes the
MockAsyncSession class of failure (silent degradation to no-isolation).
"""

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


async def auto_sync_schema() -> None:
    """Idempotently ensure critical table columns and indexes exist."""
    from sqlalchemy.ext.asyncio import create_async_engine
    seed_url = os.environ.get("SEED_DATABASE_URL")
    if not seed_url:
        return
    try:
        admin_engine = create_async_engine(seed_url, echo=False)
        statements = [
            "ALTER TABLE patient ADD COLUMN IF NOT EXISTS is_newborn BOOLEAN NOT NULL DEFAULT FALSE;",
            "ALTER TABLE patient ADD COLUMN IF NOT EXISTS mother_patient_id UUID REFERENCES patient(id) ON DELETE SET NULL;",
            "ALTER TABLE patient ADD COLUMN IF NOT EXISTS birth_time TEXT;",
            "ALTER TABLE patient ADD COLUMN IF NOT EXISTS birth_weight_grams INTEGER;",
            "ALTER TABLE patient ADD COLUMN IF NOT EXISTS gestational_age_weeks INTEGER;",
            "ALTER TABLE patient ADD COLUMN IF NOT EXISTS multiple_birth_order INTEGER NOT NULL DEFAULT 1;",
            "ALTER TABLE patient ADD COLUMN IF NOT EXISTS delivery_type TEXT;",
            "ALTER TABLE patient ADD COLUMN IF NOT EXISTS apgar_score_1min INTEGER;",
            "ALTER TABLE patient ADD COLUMN IF NOT EXISTS apgar_score_5min INTEGER;",
            "CREATE INDEX IF NOT EXISTS ix_patient_mother ON patient (tenant_id, mother_patient_id);",
            "CREATE INDEX IF NOT EXISTS ix_patient_is_newborn ON patient (tenant_id, is_newborn);",
            """
            CREATE TABLE IF NOT EXISTS subscription_plan (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                code TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                description TEXT,
                price_inr_monthly NUMERIC(12, 2) NOT NULL DEFAULT 0.00,
                price_inr_annual NUMERIC(12, 2) NOT NULL DEFAULT 0.00,
                max_practitioners INTEGER NOT NULL DEFAULT 10,
                max_beds INTEGER NOT NULL DEFAULT 15,
                max_monthly_encounters INTEGER NOT NULL DEFAULT 2500,
                admins_limit INTEGER NOT NULL DEFAULT 5,
                staff_limit INTEGER NOT NULL DEFAULT 50,
                custom_catalogs_limit INTEGER NOT NULL DEFAULT 5,
                catalog_item_limit INTEGER NOT NULL DEFAULT 50,
                abdm_level TEXT NOT NULL DEFAULT 'M1 + M2 (HIP)',
                sms_limit INTEGER NOT NULL DEFAULT 1000,
                email_limit INTEGER NOT NULL DEFAULT 2500,
                whatsapp_limit INTEGER NOT NULL DEFAULT 5000,
                active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS tenant_permissions (
                tenant_id TEXT PRIMARY KEY,
                permissions JSONB NOT NULL DEFAULT '{}'::jsonb,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """,
            "GRANT ALL PRIVILEGES ON TABLE tenant_permissions TO hms_app;",
            """
            INSERT INTO subscription_plan (code, name, description, price_inr_monthly, price_inr_annual, max_practitioners, max_beds, max_monthly_encounters, admins_limit, staff_limit, custom_catalogs_limit, catalog_item_limit, abdm_level, sms_limit, email_limit, whatsapp_limit, active)
            VALUES 
              ('starter', 'Starter (Clinic)', 'Solo practitioner consultation chambers & outpatient clinics', 1999.00, 19990.00, 2, 0, 500, 1, 3, 0, 15, 'M1 (ABHA)', 200, 500, 1000, TRUE),
              ('growth', 'Growth (Polyclinic)', 'Multi-specialty outpatient clinics and nursing homes with up to 15 beds', 7999.00, 79990.00, 10, 15, 2500, 5, 50, 5, 50, 'M1 + M2 (HIP)', 1000, 2500, 5000, TRUE),
              ('enterprise', 'Enterprise (Hospital)', 'Comprehensive multi-department tertiary care hospitals and surgical centers', 24999.00, 249990.00, -1, -1, -1, 99, 9999, 999, 9999, 'M1 + M2 + M3 (HIU)', 10000, 25000, 50000, TRUE)
            ON CONFLICT (code) DO NOTHING;
            """,
        ]
        async with admin_engine.begin() as conn:
            for stmt in statements:
                if stmt.strip():
                    await conn.execute(text(stmt))
        await admin_engine.dispose()
        log.info("db_guard: auto_sync_schema applied successfully")
    except Exception as e:
        log.warning(f"db_guard: auto_sync_schema skipped or encountered: {e}")


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

    # Attempt automatic schema synchronization if admin DB URL is available
    await auto_sync_schema()

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
