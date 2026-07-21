"""baseline — schema, RLS, app role, grants (mirrors infra/postgres/init.sql).

Revision ID: 0001_baseline
Revises:
Create Date: 2026-07-20

Story / FRD: PLT-002 (tenant isolation), PLT-005 (audit).

Idempotent (IF NOT EXISTS / DO NOTHING) so it's safe to run against a database
that was already bootstrapped by init.sql. In practice, run
    alembic -c services/plt/alembic.ini stamp 0001_baseline
after container bootstrap so this revision is marked applied without executing.
"""
from __future__ import annotations

from alembic import op

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE IF NOT EXISTS tenant (
        id           TEXT PRIMARY KEY,
        name         TEXT NOT NULL,
        region       TEXT NOT NULL DEFAULT 'india',
        locale       TEXT NOT NULL DEFAULT 'en-IN',
        currency     TEXT NOT NULL DEFAULT 'INR',
        features     JSONB NOT NULL DEFAULT '{"ref_commission": false}'::jsonb,
        created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
    );

    CREATE TABLE IF NOT EXISTS patient (
        id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id    TEXT NOT NULL REFERENCES tenant(id),
        given_name   TEXT NOT NULL,
        family_name  TEXT NOT NULL,
        dob          DATE,
        national_id  TEXT,
        phone        TEXT,
        created_by   TEXT,
        created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS ix_patient_tenant ON patient (tenant_id);
    CREATE INDEX IF NOT EXISTS ix_patient_name   ON patient (tenant_id, family_name, given_name);

    CREATE TABLE IF NOT EXISTS patient_consent (
        id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        tenant_id    TEXT NOT NULL REFERENCES tenant(id),
        patient_id   UUID NOT NULL,
        purpose      TEXT NOT NULL,
        granted_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
        granted_by   TEXT,
        revoked_at   TIMESTAMPTZ,
        source_note  TEXT
    );
    CREATE INDEX IF NOT EXISTS ix_consent_patient_purpose
        ON patient_consent (tenant_id, patient_id, purpose)
        WHERE revoked_at IS NULL;

    CREATE TABLE IF NOT EXISTS audit_event (
        id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        tenant_id     TEXT NOT NULL,
        actor_user_id TEXT NOT NULL,
        actor_role    TEXT NOT NULL,
        action        TEXT NOT NULL,
        resource_type TEXT NOT NULL,
        resource_id   TEXT,
        patient_id    TEXT,
        source_ip     TEXT,
        context_note  TEXT,
        occurred_at   TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS ix_audit_tenant_time ON audit_event (tenant_id, occurred_at);

    DO $$
    BEGIN
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'hms_app') THEN
            CREATE ROLE hms_app LOGIN PASSWORD 'app_password_change_me';
        END IF;
    END $$;

    GRANT SELECT, INSERT, UPDATE ON patient TO hms_app;
    GRANT SELECT, INSERT ON audit_event TO hms_app;
    GRANT SELECT, INSERT, UPDATE ON patient_consent TO hms_app;
    GRANT SELECT ON tenant TO hms_app;
    GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO hms_app;

    CREATE OR REPLACE FUNCTION current_tenant() RETURNS TEXT AS $$
        SELECT current_setting('app.tenant_id', true);
    $$ LANGUAGE sql STABLE;

    ALTER TABLE patient ENABLE ROW LEVEL SECURITY;
    ALTER TABLE patient FORCE ROW LEVEL SECURITY;
    DROP POLICY IF EXISTS patient_tenant_isolation ON patient;
    CREATE POLICY patient_tenant_isolation ON patient
        USING (tenant_id = current_tenant())
        WITH CHECK (tenant_id = current_tenant());

    ALTER TABLE audit_event ENABLE ROW LEVEL SECURITY;
    ALTER TABLE audit_event FORCE ROW LEVEL SECURITY;
    DROP POLICY IF EXISTS audit_tenant_isolation ON audit_event;
    CREATE POLICY audit_tenant_isolation ON audit_event
        USING (tenant_id = current_tenant())
        WITH CHECK (tenant_id = current_tenant());

    ALTER TABLE patient_consent ENABLE ROW LEVEL SECURITY;
    ALTER TABLE patient_consent FORCE ROW LEVEL SECURITY;
    DROP POLICY IF EXISTS consent_tenant_isolation ON patient_consent;
    CREATE POLICY consent_tenant_isolation ON patient_consent
        USING (tenant_id = current_tenant())
        WITH CHECK (tenant_id = current_tenant());
    """)


def downgrade() -> None:
    # Baseline. Rolling this back drops patient data — refuse.
    raise RuntimeError("Refusing to downgrade the baseline migration (would drop patient data).")
