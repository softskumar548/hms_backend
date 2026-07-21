"""add_ord_tables

Revision ID: da965b53eaab
Revises: bdc182f48fd9
Create Date: 2026-07-20 23:03:32.584198

Story / FRD: ORD-001, ORD-003, ORD-004, ORD-006, ORD-008
"""
from __future__ import annotations

from alembic import op


revision = 'da965b53eaab'
down_revision = 'bdc182f48fd9'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE IF NOT EXISTS lab_catalog (
        id                       TEXT PRIMARY KEY,
        tenant_id                TEXT NOT NULL REFERENCES tenant(id),
        test_code                TEXT NOT NULL, -- LOINC
        name                     TEXT NOT NULL,
        specimen_requirements    TEXT,
        preparation_requirements TEXT,
        created_at               TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS ix_lab_catalog_tenant ON lab_catalog (tenant_id);

    CREATE TABLE IF NOT EXISTS lab_order (
        id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id       TEXT NOT NULL REFERENCES tenant(id),
        patient_id      UUID NOT NULL REFERENCES patient(id),
        practitioner_id TEXT NOT NULL REFERENCES practitioner(id),
        encounter_id    UUID NOT NULL REFERENCES encounter(id),
        status          TEXT NOT NULL DEFAULT 'ordered', -- 'ordered', 'specimen_collected', 'in_laboratory', 'resulted', 'reviewed'
        priority        TEXT NOT NULL DEFAULT 'routine', -- 'routine', 'urgent'
        created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS ix_lab_order_tenant_patient ON lab_order (tenant_id, patient_id);

    CREATE TABLE IF NOT EXISTS lab_order_item (
        id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id TEXT NOT NULL REFERENCES tenant(id),
        order_id  UUID NOT NULL REFERENCES lab_order(id),
        test_id   TEXT NOT NULL REFERENCES lab_catalog(id)
    );
    CREATE INDEX IF NOT EXISTS ix_lab_order_item_order ON lab_order_item (tenant_id, order_id);

    CREATE TABLE IF NOT EXISTS lab_result (
        id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id       TEXT NOT NULL REFERENCES tenant(id),
        order_id        UUID REFERENCES lab_order(id),
        patient_id      UUID REFERENCES patient(id),
        test_id         TEXT NOT NULL REFERENCES lab_catalog(id),
        value           NUMERIC NOT NULL,
        unit            TEXT NOT NULL,
        reference_range TEXT,
        is_abnormal     BOOLEAN NOT NULL DEFAULT FALSE,
        is_critical     BOOLEAN NOT NULL DEFAULT FALSE,
        resulted_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
        reviewed_at     TIMESTAMPTZ,
        reviewed_by     TEXT
    );
    CREATE INDEX IF NOT EXISTS ix_lab_result_tenant_order ON lab_result (tenant_id, order_id);

    CREATE TABLE IF NOT EXISTS lab_unmatched_result (
        id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id   TEXT NOT NULL REFERENCES tenant(id),
        payload     JSONB NOT NULL,
        status      TEXT NOT NULL DEFAULT 'pending', -- 'pending', 'resolved', 'rejected'
        resolved_by TEXT,
        resolved_at TIMESTAMPTZ
    );
    CREATE INDEX IF NOT EXISTS ix_unmatched_tenant ON lab_unmatched_result (tenant_id);

    -- Grant Permissions
    GRANT SELECT, INSERT, UPDATE, DELETE ON lab_catalog TO hms_app;
    GRANT SELECT, INSERT, UPDATE, DELETE ON lab_order TO hms_app;
    GRANT SELECT, INSERT, UPDATE, DELETE ON lab_order_item TO hms_app;
    GRANT SELECT, INSERT, UPDATE, DELETE ON lab_result TO hms_app;
    GRANT SELECT, INSERT, UPDATE, DELETE ON lab_unmatched_result TO hms_app;

    -- Enable RLS & Configure Policies
    ALTER TABLE lab_catalog ENABLE ROW LEVEL SECURITY;
    ALTER TABLE lab_catalog FORCE ROW LEVEL SECURITY;
    DROP POLICY IF EXISTS lab_catalog_tenant_isolation ON lab_catalog;
    CREATE POLICY lab_catalog_tenant_isolation ON lab_catalog USING (tenant_id = current_tenant()) WITH CHECK (tenant_id = current_tenant());

    ALTER TABLE lab_order ENABLE ROW LEVEL SECURITY;
    ALTER TABLE lab_order FORCE ROW LEVEL SECURITY;
    DROP POLICY IF EXISTS lab_order_tenant_isolation ON lab_order;
    CREATE POLICY lab_order_tenant_isolation ON lab_order USING (tenant_id = current_tenant()) WITH CHECK (tenant_id = current_tenant());

    ALTER TABLE lab_order_item ENABLE ROW LEVEL SECURITY;
    ALTER TABLE lab_order_item FORCE ROW LEVEL SECURITY;
    DROP POLICY IF EXISTS lab_order_item_tenant_isolation ON lab_order_item;
    CREATE POLICY lab_order_item_tenant_isolation ON lab_order_item USING (tenant_id = current_tenant()) WITH CHECK (tenant_id = current_tenant());

    ALTER TABLE lab_result ENABLE ROW LEVEL SECURITY;
    ALTER TABLE lab_result FORCE ROW LEVEL SECURITY;
    DROP POLICY IF EXISTS lab_result_tenant_isolation ON lab_result;
    CREATE POLICY lab_result_tenant_isolation ON lab_result USING (tenant_id = current_tenant()) WITH CHECK (tenant_id = current_tenant());

    ALTER TABLE lab_unmatched_result ENABLE ROW LEVEL SECURITY;
    ALTER TABLE lab_unmatched_result FORCE ROW LEVEL SECURITY;
    DROP POLICY IF EXISTS lab_unmatched_result_tenant_isolation ON lab_unmatched_result;
    CREATE POLICY lab_unmatched_result_tenant_isolation ON lab_unmatched_result USING (tenant_id = current_tenant()) WITH CHECK (tenant_id = current_tenant());
    """)


def downgrade() -> None:
    op.execute("""
    DROP TABLE IF EXISTS lab_unmatched_result;
    DROP TABLE IF EXISTS lab_result;
    DROP TABLE IF EXISTS lab_order_item;
    DROP TABLE IF EXISTS lab_order;
    DROP TABLE IF EXISTS lab_catalog;
    """)

