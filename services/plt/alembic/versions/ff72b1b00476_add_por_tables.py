"""add_por_tables

Revision ID: ff72b1b00476
Revises: f48eaaa56941
Create Date: 2026-07-20 23:09:59.920630

Story / FRD: POR-001, POR-002, POR-003, POR-004, POR-005, POR-006, POR-007
"""
from __future__ import annotations

from alembic import op


revision = 'ff72b1b00476'
down_revision = 'f48eaaa56941'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE IF NOT EXISTS portal_invitation (
        id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id  TEXT NOT NULL REFERENCES tenant(id),
        patient_id UUID NOT NULL REFERENCES patient(id),
        email      TEXT NOT NULL,
        phone      TEXT NOT NULL,
        otp_code   TEXT NOT NULL,
        expires_at TIMESTAMPTZ NOT NULL,
        status     TEXT NOT NULL DEFAULT 'pending' -- 'pending', 'activated', 'expired'
    );
    CREATE INDEX IF NOT EXISTS ix_portal_invite_patient ON portal_invitation (tenant_id, patient_id);

    CREATE TABLE IF NOT EXISTS portal_user (
        id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id     TEXT NOT NULL REFERENCES tenant(id),
        patient_id    UUID NOT NULL REFERENCES patient(id),
        username      TEXT NOT NULL,
        password_hash TEXT NOT NULL,
        active        BOOLEAN NOT NULL DEFAULT TRUE,
        created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    CREATE UNIQUE INDEX IF NOT EXISTS uix_portal_user_username ON portal_user (tenant_id, username);

    CREATE TABLE IF NOT EXISTS portal_questionnaire (
        id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id          TEXT NOT NULL REFERENCES tenant(id),
        appointment_id     UUID NOT NULL REFERENCES appointment(id),
        questionnaire_type TEXT NOT NULL, -- e.g. 'general', 'orthopedic_consent'
        questions_json     JSONB NOT NULL,
        answers_json       JSONB,
        submitted_at       TIMESTAMPTZ
    );
    CREATE INDEX IF NOT EXISTS ix_questionnaire_app ON portal_questionnaire (tenant_id, appointment_id);

    CREATE TABLE IF NOT EXISTS portal_proxy (
        id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id         TEXT NOT NULL REFERENCES tenant(id),
        patient_id        UUID NOT NULL REFERENCES patient(id),
        proxy_patient_id  UUID NOT NULL REFERENCES patient(id),
        relationship_type TEXT NOT NULL, -- e.g. 'parent', 'guardian'
        status            TEXT NOT NULL DEFAULT 'active', -- 'active', 'expired'
        expires_at        TIMESTAMPTZ NOT NULL
    );
    CREATE INDEX IF NOT EXISTS ix_portal_proxy ON portal_proxy (tenant_id, patient_id);

    CREATE TABLE IF NOT EXISTS portal_message (
        id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id    TEXT NOT NULL REFERENCES tenant(id),
        patient_id   UUID NOT NULL REFERENCES patient(id),
        direction    TEXT NOT NULL, -- 'patient_to_clinic', 'clinic_to_patient'
        message_text TEXT NOT NULL,
        read_at      TIMESTAMPTZ,
        created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS ix_portal_message ON portal_message (tenant_id, patient_id);

    -- Grant Permissions
    GRANT SELECT, INSERT, UPDATE, DELETE ON portal_invitation TO hms_app;
    GRANT SELECT, INSERT, UPDATE, DELETE ON portal_user TO hms_app;
    GRANT SELECT, INSERT, UPDATE, DELETE ON portal_questionnaire TO hms_app;
    GRANT SELECT, INSERT, UPDATE, DELETE ON portal_proxy TO hms_app;
    GRANT SELECT, INSERT, UPDATE, DELETE ON portal_message TO hms_app;

    -- Enable RLS & Configure Policies
    ALTER TABLE portal_invitation ENABLE ROW LEVEL SECURITY;
    ALTER TABLE portal_invitation FORCE ROW LEVEL SECURITY;
    DROP POLICY IF EXISTS invitation_tenant_isolation ON portal_invitation;
    CREATE POLICY invitation_tenant_isolation ON portal_invitation USING (tenant_id = current_tenant()) WITH CHECK (tenant_id = current_tenant());

    ALTER TABLE portal_user ENABLE ROW LEVEL SECURITY;
    ALTER TABLE portal_user FORCE ROW LEVEL SECURITY;
    DROP POLICY IF EXISTS user_tenant_isolation ON portal_user;
    CREATE POLICY user_tenant_isolation ON portal_user USING (tenant_id = current_tenant()) WITH CHECK (tenant_id = current_tenant());

    ALTER TABLE portal_questionnaire ENABLE ROW LEVEL SECURITY;
    ALTER TABLE portal_questionnaire FORCE ROW LEVEL SECURITY;
    DROP POLICY IF EXISTS questionnaire_tenant_isolation ON portal_questionnaire;
    CREATE POLICY questionnaire_tenant_isolation ON portal_questionnaire USING (tenant_id = current_tenant()) WITH CHECK (tenant_id = current_tenant());

    ALTER TABLE portal_proxy ENABLE ROW LEVEL SECURITY;
    ALTER TABLE portal_proxy FORCE ROW LEVEL SECURITY;
    DROP POLICY IF EXISTS proxy_tenant_isolation ON portal_proxy;
    CREATE POLICY proxy_tenant_isolation ON portal_proxy USING (tenant_id = current_tenant()) WITH CHECK (tenant_id = current_tenant());

    ALTER TABLE portal_message ENABLE ROW LEVEL SECURITY;
    ALTER TABLE portal_message FORCE ROW LEVEL SECURITY;
    DROP POLICY IF EXISTS message_tenant_isolation ON portal_message;
    CREATE POLICY message_tenant_isolation ON portal_message USING (tenant_id = current_tenant()) WITH CHECK (tenant_id = current_tenant());
    """)


def downgrade() -> None:
    op.execute("""
    DROP TABLE IF EXISTS portal_message;
    DROP TABLE IF EXISTS portal_proxy;
    DROP TABLE IF EXISTS portal_questionnaire;
    DROP TABLE IF EXISTS portal_user;
    DROP TABLE IF EXISTS portal_invitation;
    """)

