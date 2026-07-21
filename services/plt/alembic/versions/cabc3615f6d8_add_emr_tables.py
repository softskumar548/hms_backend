"""add_emr_tables

Revision ID: cabc3615f6d8
Revises: c5d21f4f6dc7
Create Date: 2026-07-20 22:57:35.885315

Story / FRD: EMR-001, EMR-002, EMR-003, EMR-004, EMR-005, EMR-006, EMR-007, EMR-008, EMR-012
"""
from __future__ import annotations

from alembic import op


revision = 'cabc3615f6d8'
down_revision = 'c5d21f4f6dc7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE IF NOT EXISTS encounter (
        id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id       TEXT NOT NULL REFERENCES tenant(id),
        appointment_id  UUID REFERENCES appointment(id),
        patient_id      UUID NOT NULL REFERENCES patient(id),
        practitioner_id TEXT NOT NULL REFERENCES practitioner(id),
        site_id         TEXT NOT NULL REFERENCES site(id),
        status          TEXT NOT NULL DEFAULT 'open', -- 'open', 'in-documentation', 'signed', 'amended'
        created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
        signed_at       TIMESTAMPTZ,
        signed_by       TEXT
    );
    CREATE INDEX IF NOT EXISTS ix_encounter_tenant_patient ON encounter (tenant_id, patient_id);
    CREATE INDEX IF NOT EXISTS ix_encounter_appointment ON encounter (tenant_id, appointment_id);

    CREATE TABLE IF NOT EXISTS clinical_note (
        id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id          TEXT NOT NULL REFERENCES tenant(id),
        encounter_id       UUID NOT NULL REFERENCES encounter(id),
        template_type      TEXT NOT NULL,
        structured_content JSONB,
        rich_text_content  TEXT,
        version            INTEGER NOT NULL DEFAULT 1,
        created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS ix_clinical_note_encounter ON clinical_note (tenant_id, encounter_id);

    CREATE TABLE IF NOT EXISTS clinical_note_addendum (
        id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id    TEXT NOT NULL REFERENCES tenant(id),
        encounter_id UUID NOT NULL REFERENCES encounter(id),
        author_id    TEXT NOT NULL,
        content      TEXT NOT NULL,
        created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS ix_addendum_encounter ON clinical_note_addendum (tenant_id, encounter_id);

    CREATE TABLE IF NOT EXISTS allergy_intolerance (
        id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id         TEXT NOT NULL REFERENCES tenant(id),
        patient_id        UUID NOT NULL REFERENCES patient(id),
        substance_code    TEXT,
        substance_display TEXT,
        reaction          TEXT,
        severity          TEXT, -- 'mild', 'moderate', 'severe'
        criticality       TEXT, -- 'low', 'high', 'unable-to-assess'
        is_no_known       BOOLEAN NOT NULL DEFAULT FALSE,
        asserted_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
        asserted_by       TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS ix_allergy_patient ON allergy_intolerance (tenant_id, patient_id);

    CREATE TABLE IF NOT EXISTS condition (
        id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id       TEXT NOT NULL REFERENCES tenant(id),
        patient_id      UUID NOT NULL REFERENCES patient(id),
        clinical_status TEXT NOT NULL DEFAULT 'active', -- 'active', 'inactive', 'resolved'
        code            TEXT NOT NULL, -- ICD-10 or SNOMED
        display         TEXT NOT NULL,
        onset_date      DATE,
        resolution_date DATE,
        asserted_at     TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS ix_condition_patient ON condition (tenant_id, patient_id);

    CREATE TABLE IF NOT EXISTS medication_statement (
        id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id          TEXT NOT NULL REFERENCES tenant(id),
        patient_id         UUID NOT NULL REFERENCES patient(id),
        status             TEXT NOT NULL DEFAULT 'active',
        medication_code    TEXT NOT NULL,
        medication_display TEXT NOT NULL,
        sig                TEXT,
        asserted_at        TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS ix_med_stmt_patient ON medication_statement (tenant_id, patient_id);

    CREATE TABLE IF NOT EXISTS vital_sign (
        id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id    TEXT NOT NULL REFERENCES tenant(id),
        encounter_id UUID NOT NULL REFERENCES encounter(id),
        patient_id   UUID NOT NULL REFERENCES patient(id),
        type         TEXT NOT NULL, -- 'height', 'weight', 'bp_systolic', 'bp_diastolic', 'heart_rate', 'temperature', 'spo2'
        value        NUMERIC NOT NULL,
        unit         TEXT NOT NULL,
        recorded_at  TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS ix_vital_sign_encounter ON vital_sign (tenant_id, encounter_id);
    CREATE INDEX IF NOT EXISTS ix_vital_sign_patient ON vital_sign (tenant_id, patient_id);

    CREATE TABLE IF NOT EXISTS encounter_document (
        id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id    TEXT NOT NULL REFERENCES tenant(id),
        encounter_id UUID NOT NULL REFERENCES encounter(id),
        patient_id   UUID NOT NULL REFERENCES patient(id),
        file_path    TEXT NOT NULL,
        file_type    TEXT NOT NULL,
        label        TEXT NOT NULL,
        uploaded_at  TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS ix_enc_doc_encounter ON encounter_document (tenant_id, encounter_id);

    -- Grant Permissions
    GRANT SELECT, INSERT, UPDATE, DELETE ON encounter TO hms_app;
    GRANT SELECT, INSERT, UPDATE, DELETE ON clinical_note TO hms_app;
    GRANT SELECT, INSERT, UPDATE, DELETE ON clinical_note_addendum TO hms_app;
    GRANT SELECT, INSERT, UPDATE, DELETE ON allergy_intolerance TO hms_app;
    GRANT SELECT, INSERT, UPDATE, DELETE ON condition TO hms_app;
    GRANT SELECT, INSERT, UPDATE, DELETE ON medication_statement TO hms_app;
    GRANT SELECT, INSERT, UPDATE, DELETE ON vital_sign TO hms_app;
    GRANT SELECT, INSERT, UPDATE, DELETE ON encounter_document TO hms_app;

    -- Enable RLS & Configure Policies
    ALTER TABLE encounter ENABLE ROW LEVEL SECURITY;
    ALTER TABLE encounter FORCE ROW LEVEL SECURITY;
    DROP POLICY IF EXISTS encounter_tenant_isolation ON encounter;
    CREATE POLICY encounter_tenant_isolation ON encounter USING (tenant_id = current_tenant()) WITH CHECK (tenant_id = current_tenant());

    ALTER TABLE clinical_note ENABLE ROW LEVEL SECURITY;
    ALTER TABLE clinical_note FORCE ROW LEVEL SECURITY;
    DROP POLICY IF EXISTS note_tenant_isolation ON clinical_note;
    CREATE POLICY note_tenant_isolation ON clinical_note USING (tenant_id = current_tenant()) WITH CHECK (tenant_id = current_tenant());

    ALTER TABLE clinical_note_addendum ENABLE ROW LEVEL SECURITY;
    ALTER TABLE clinical_note_addendum FORCE ROW LEVEL SECURITY;
    DROP POLICY IF EXISTS addendum_tenant_isolation ON clinical_note_addendum;
    CREATE POLICY addendum_tenant_isolation ON clinical_note_addendum USING (tenant_id = current_tenant()) WITH CHECK (tenant_id = current_tenant());

    ALTER TABLE allergy_intolerance ENABLE ROW LEVEL SECURITY;
    ALTER TABLE allergy_intolerance FORCE ROW LEVEL SECURITY;
    DROP POLICY IF EXISTS allergy_tenant_isolation ON allergy_intolerance;
    CREATE POLICY allergy_tenant_isolation ON allergy_intolerance USING (tenant_id = current_tenant()) WITH CHECK (tenant_id = current_tenant());

    ALTER TABLE condition ENABLE ROW LEVEL SECURITY;
    ALTER TABLE condition FORCE ROW LEVEL SECURITY;
    DROP POLICY IF EXISTS condition_tenant_isolation ON condition;
    CREATE POLICY condition_tenant_isolation ON condition USING (tenant_id = current_tenant()) WITH CHECK (tenant_id = current_tenant());

    ALTER TABLE medication_statement ENABLE ROW LEVEL SECURITY;
    ALTER TABLE medication_statement FORCE ROW LEVEL SECURITY;
    DROP POLICY IF EXISTS med_stmt_tenant_isolation ON medication_statement;
    CREATE POLICY med_stmt_tenant_isolation ON medication_statement USING (tenant_id = current_tenant()) WITH CHECK (tenant_id = current_tenant());

    ALTER TABLE vital_sign ENABLE ROW LEVEL SECURITY;
    ALTER TABLE vital_sign FORCE ROW LEVEL SECURITY;
    DROP POLICY IF EXISTS vitals_tenant_isolation ON vital_sign;
    CREATE POLICY vitals_tenant_isolation ON vital_sign USING (tenant_id = current_tenant()) WITH CHECK (tenant_id = current_tenant());

    ALTER TABLE encounter_document ENABLE ROW LEVEL SECURITY;
    ALTER TABLE encounter_document FORCE ROW LEVEL SECURITY;
    DROP POLICY IF EXISTS document_tenant_isolation ON encounter_document;
    CREATE POLICY document_tenant_isolation ON encounter_document USING (tenant_id = current_tenant()) WITH CHECK (tenant_id = current_tenant());
    """)


def downgrade() -> None:
    op.execute("""
    DROP TABLE IF EXISTS encounter_document;
    DROP TABLE IF EXISTS vital_sign;
    DROP TABLE IF EXISTS medication_statement;
    DROP TABLE IF EXISTS condition;
    DROP TABLE IF EXISTS allergy_intolerance;
    DROP TABLE IF EXISTS clinical_note_addendum;
    DROP TABLE IF EXISTS clinical_note;
    DROP TABLE IF EXISTS encounter;
    """)

