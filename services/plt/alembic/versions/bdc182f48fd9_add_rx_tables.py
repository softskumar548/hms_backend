"""add_rx_tables

Revision ID: bdc182f48fd9
Revises: cabc3615f6d8
Create Date: 2026-07-20 23:01:01.432827

Story / FRD: RX-001, RX-002, RX-003, RX-005, RX-006, RX-008
"""
from __future__ import annotations

from alembic import op


revision = 'bdc182f48fd9'
down_revision = 'cabc3615f6d8'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE IF NOT EXISTS medication_catalog (
        id           TEXT PRIMARY KEY,
        tenant_id    TEXT NOT NULL REFERENCES tenant(id),
        name         TEXT NOT NULL,
        generic_name TEXT NOT NULL,
        form         TEXT NOT NULL, -- e.g. 'tablet', 'capsule', 'syrup'
        strength     TEXT NOT NULL, -- e.g. '500 mg', '10 mg/mL'
        created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS ix_med_catalog_tenant ON medication_catalog (tenant_id);

    CREATE TABLE IF NOT EXISTS tenant_formulary (
        id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id     TEXT NOT NULL REFERENCES tenant(id),
        medication_id TEXT NOT NULL REFERENCES medication_catalog(id),
        active        BOOLEAN NOT NULL DEFAULT TRUE,
        created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS ix_formulary_tenant ON tenant_formulary (tenant_id, medication_id);

    CREATE TABLE IF NOT EXISTS prescription (
        id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id       TEXT NOT NULL REFERENCES tenant(id),
        patient_id      UUID NOT NULL REFERENCES patient(id),
        practitioner_id TEXT NOT NULL REFERENCES practitioner(id),
        encounter_id    UUID NOT NULL REFERENCES encounter(id),
        status          TEXT NOT NULL DEFAULT 'draft', -- 'draft', 'signed', 'cancelled'
        created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
        signed_at       TIMESTAMPTZ,
        signed_by       TEXT
    );
    CREATE INDEX IF NOT EXISTS ix_prescription_tenant_patient ON prescription (tenant_id, patient_id);

    CREATE TABLE IF NOT EXISTS prescription_item (
        id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id       TEXT NOT NULL REFERENCES tenant(id),
        prescription_id UUID NOT NULL REFERENCES prescription(id),
        medication_id   TEXT NOT NULL REFERENCES medication_catalog(id),
        dose            NUMERIC NOT NULL,
        unit            TEXT NOT NULL, -- e.g. 'mg', 'tablet'
        route           TEXT NOT NULL, -- e.g. 'oral', 'intravenous'
        frequency       TEXT NOT NULL, -- e.g. 'once daily', 'twice daily'
        duration_days   INTEGER NOT NULL,
        prn             BOOLEAN NOT NULL DEFAULT FALSE,
        quantity        INTEGER NOT NULL,
        refills         INTEGER NOT NULL DEFAULT 0,
        free_text_sig   TEXT
    );
    CREATE INDEX IF NOT EXISTS ix_prescription_item_prescription ON prescription_item (tenant_id, prescription_id);

    CREATE TABLE IF NOT EXISTS prescription_override (
        id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id       TEXT NOT NULL REFERENCES tenant(id),
        prescription_id UUID NOT NULL REFERENCES prescription(id),
        alert_type      TEXT NOT NULL, -- 'drug-drug', 'drug-allergy', 'duplicate-therapy'
        severity        TEXT NOT NULL, -- 'high', 'moderate', 'low'
        reason          TEXT NOT NULL,
        created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS ix_override_prescription ON prescription_override (tenant_id, prescription_id);

    CREATE TABLE IF NOT EXISTS prescription_favorite (
        id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id       TEXT NOT NULL REFERENCES tenant(id),
        practitioner_id TEXT NOT NULL REFERENCES practitioner(id),
        medication_id   TEXT NOT NULL REFERENCES medication_catalog(id),
        dose            NUMERIC,
        unit            TEXT,
        route           TEXT,
        frequency       TEXT,
        created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS ix_favorite_practitioner ON prescription_favorite (tenant_id, practitioner_id);

    -- Grant Permissions
    GRANT SELECT, INSERT, UPDATE, DELETE ON medication_catalog TO hms_app;
    GRANT SELECT, INSERT, UPDATE, DELETE ON tenant_formulary TO hms_app;
    GRANT SELECT, INSERT, UPDATE, DELETE ON prescription TO hms_app;
    GRANT SELECT, INSERT, UPDATE, DELETE ON prescription_item TO hms_app;
    GRANT SELECT, INSERT, UPDATE, DELETE ON prescription_override TO hms_app;
    GRANT SELECT, INSERT, UPDATE, DELETE ON prescription_favorite TO hms_app;

    -- Enable RLS & Configure Policies
    ALTER TABLE medication_catalog ENABLE ROW LEVEL SECURITY;
    ALTER TABLE medication_catalog FORCE ROW LEVEL SECURITY;
    DROP POLICY IF EXISTS med_catalog_tenant_isolation ON medication_catalog;
    CREATE POLICY med_catalog_tenant_isolation ON medication_catalog USING (tenant_id = current_tenant()) WITH CHECK (tenant_id = current_tenant());

    ALTER TABLE tenant_formulary ENABLE ROW LEVEL SECURITY;
    ALTER TABLE tenant_formulary FORCE ROW LEVEL SECURITY;
    DROP POLICY IF EXISTS formulary_tenant_isolation ON tenant_formulary;
    CREATE POLICY formulary_tenant_isolation ON tenant_formulary USING (tenant_id = current_tenant()) WITH CHECK (tenant_id = current_tenant());

    ALTER TABLE prescription ENABLE ROW LEVEL SECURITY;
    ALTER TABLE prescription FORCE ROW LEVEL SECURITY;
    DROP POLICY IF EXISTS prescription_tenant_isolation ON prescription;
    CREATE POLICY prescription_tenant_isolation ON prescription USING (tenant_id = current_tenant()) WITH CHECK (tenant_id = current_tenant());

    ALTER TABLE prescription_item ENABLE ROW LEVEL SECURITY;
    ALTER TABLE prescription_item FORCE ROW LEVEL SECURITY;
    DROP POLICY IF EXISTS item_tenant_isolation ON prescription_item;
    CREATE POLICY item_tenant_isolation ON prescription_item USING (tenant_id = current_tenant()) WITH CHECK (tenant_id = current_tenant());

    ALTER TABLE prescription_override ENABLE ROW LEVEL SECURITY;
    ALTER TABLE prescription_override FORCE ROW LEVEL SECURITY;
    DROP POLICY IF EXISTS override_tenant_isolation ON prescription_override;
    CREATE POLICY override_tenant_isolation ON prescription_override USING (tenant_id = current_tenant()) WITH CHECK (tenant_id = current_tenant());

    ALTER TABLE prescription_favorite ENABLE ROW LEVEL SECURITY;
    ALTER TABLE prescription_favorite FORCE ROW LEVEL SECURITY;
    DROP POLICY IF EXISTS favorite_tenant_isolation ON prescription_favorite;
    CREATE POLICY favorite_tenant_isolation ON prescription_favorite USING (tenant_id = current_tenant()) WITH CHECK (tenant_id = current_tenant());
    """)


def downgrade() -> None:
    op.execute("""
    DROP TABLE IF EXISTS prescription_favorite;
    DROP TABLE IF EXISTS prescription_override;
    DROP TABLE IF EXISTS prescription_item;
    DROP TABLE IF EXISTS prescription;
    DROP TABLE IF EXISTS tenant_formulary;
    DROP TABLE IF EXISTS medication_catalog;
    """)
