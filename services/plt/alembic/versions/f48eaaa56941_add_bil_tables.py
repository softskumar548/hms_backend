"""add_bil_tables

Revision ID: f48eaaa56941
Revises: da965b53eaab
Create Date: 2026-07-20 23:06:44.383711

Story / FRD: BIL-001, BIL-002, BIL-003, BIL-004, BIL-005, BIL-006, BIL-008, BIL-010
"""
from __future__ import annotations

from alembic import op


revision = 'f48eaaa56941'
down_revision = 'da965b53eaab'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE IF NOT EXISTS charge_master (
        id             TEXT PRIMARY KEY,
        tenant_id      TEXT NOT NULL REFERENCES tenant(id),
        code           TEXT NOT NULL,
        name           TEXT NOT NULL,
        category       TEXT NOT NULL, -- e.g. 'consultation', 'procedure', 'pharmacy', 'laboratory'
        standard_price NUMERIC NOT NULL,
        tax_percent    NUMERIC NOT NULL DEFAULT 0,
        active         BOOLEAN NOT NULL DEFAULT TRUE,
        created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS ix_charge_master_tenant ON charge_master (tenant_id);

    CREATE TABLE IF NOT EXISTS patient_coverage (
        id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id             TEXT NOT NULL REFERENCES tenant(id),
        patient_id            UUID NOT NULL REFERENCES patient(id),
        scheme_type           TEXT NOT NULL, -- 'aarogyasri', 'pmjay', 'private'
        plan_name             TEXT NOT NULL,
        member_id             TEXT NOT NULL,
        validity_start        DATE NOT NULL,
        validity_end          DATE NOT NULL,
        patient_share_percent NUMERIC NOT NULL,
        created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS ix_coverage_tenant_patient ON patient_coverage (tenant_id, patient_id);

    CREATE TABLE IF NOT EXISTS invoice (
        id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id            TEXT NOT NULL REFERENCES tenant(id),
        patient_id           UUID NOT NULL REFERENCES patient(id),
        encounter_id         UUID NOT NULL REFERENCES encounter(id),
        status               TEXT NOT NULL DEFAULT 'draft', -- 'draft', 'finalized', 'reversed'
        coverage_id          UUID REFERENCES patient_coverage(id),
        total_amount         NUMERIC NOT NULL DEFAULT 0,
        payer_responsibility NUMERIC NOT NULL DEFAULT 0,
        patient_responsibility NUMERIC NOT NULL DEFAULT 0,
        created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS ix_invoice_tenant_patient ON invoice (tenant_id, patient_id);

    CREATE TABLE IF NOT EXISTS invoice_item (
        id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id       TEXT NOT NULL REFERENCES tenant(id),
        invoice_id      UUID NOT NULL REFERENCES invoice(id),
        charge_item_id  TEXT NOT NULL REFERENCES charge_master(id),
        quantity        INTEGER NOT NULL DEFAULT 1,
        unit_price      NUMERIC NOT NULL,
        tax_amount      NUMERIC NOT NULL DEFAULT 0,
        discount_amount NUMERIC NOT NULL DEFAULT 0,
        patient_share   NUMERIC NOT NULL,
        payer_share     NUMERIC NOT NULL
    );
    CREATE INDEX IF NOT EXISTS ix_invoice_item_invoice ON invoice_item (tenant_id, invoice_id);

    CREATE TABLE IF NOT EXISTS payment (
        id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id             TEXT NOT NULL REFERENCES tenant(id),
        invoice_id            UUID NOT NULL REFERENCES invoice(id),
        payment_method        TEXT NOT NULL, -- 'cash', 'card', 'insurance_remittance'
        amount                NUMERIC NOT NULL,
        transaction_reference TEXT,
        received_at           TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS ix_payment_invoice ON payment (tenant_id, invoice_id);

    CREATE TABLE IF NOT EXISTS claim (
        id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id     TEXT NOT NULL REFERENCES tenant(id),
        invoice_id    UUID NOT NULL REFERENCES invoice(id),
        coverage_id   UUID NOT NULL REFERENCES patient_coverage(id),
        status        TEXT NOT NULL DEFAULT 'draft', -- 'draft', 'submitted', 'accepted', 'rejected', 'paid'
        total_claimed NUMERIC NOT NULL,
        submitted_at  TIMESTAMPTZ,
        updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS ix_claim_invoice ON claim (tenant_id, invoice_id);

    -- Grant Permissions
    GRANT SELECT, INSERT, UPDATE, DELETE ON charge_master TO hms_app;
    GRANT SELECT, INSERT, UPDATE, DELETE ON patient_coverage TO hms_app;
    GRANT SELECT, INSERT, UPDATE, DELETE ON invoice TO hms_app;
    GRANT SELECT, INSERT, UPDATE, DELETE ON invoice_item TO hms_app;
    GRANT SELECT, INSERT, UPDATE, DELETE ON payment TO hms_app;
    GRANT SELECT, INSERT, UPDATE, DELETE ON claim TO hms_app;

    -- Enable RLS & Configure Policies
    ALTER TABLE charge_master ENABLE ROW LEVEL SECURITY;
    ALTER TABLE charge_master FORCE ROW LEVEL SECURITY;
    DROP POLICY IF EXISTS charge_master_tenant_isolation ON charge_master;
    CREATE POLICY charge_master_tenant_isolation ON charge_master USING (tenant_id = current_tenant()) WITH CHECK (tenant_id = current_tenant());

    ALTER TABLE patient_coverage ENABLE ROW LEVEL SECURITY;
    ALTER TABLE patient_coverage FORCE ROW LEVEL SECURITY;
    DROP POLICY IF EXISTS coverage_tenant_isolation ON patient_coverage;
    CREATE POLICY coverage_tenant_isolation ON patient_coverage USING (tenant_id = current_tenant()) WITH CHECK (tenant_id = current_tenant());

    ALTER TABLE invoice ENABLE ROW LEVEL SECURITY;
    ALTER TABLE invoice FORCE ROW LEVEL SECURITY;
    DROP POLICY IF EXISTS invoice_tenant_isolation ON invoice;
    CREATE POLICY invoice_tenant_isolation ON invoice USING (tenant_id = current_tenant()) WITH CHECK (tenant_id = current_tenant());

    ALTER TABLE invoice_item ENABLE ROW LEVEL SECURITY;
    ALTER TABLE invoice_item FORCE ROW LEVEL SECURITY;
    DROP POLICY IF EXISTS invoice_item_tenant_isolation ON invoice_item;
    CREATE POLICY invoice_item_tenant_isolation ON invoice_item USING (tenant_id = current_tenant()) WITH CHECK (tenant_id = current_tenant());

    ALTER TABLE payment ENABLE ROW LEVEL SECURITY;
    ALTER TABLE payment FORCE ROW LEVEL SECURITY;
    DROP POLICY IF EXISTS payment_tenant_isolation ON payment;
    CREATE POLICY payment_tenant_isolation ON payment USING (tenant_id = current_tenant()) WITH CHECK (tenant_id = current_tenant());

    ALTER TABLE claim ENABLE ROW LEVEL SECURITY;
    ALTER TABLE claim FORCE ROW LEVEL SECURITY;
    DROP POLICY IF EXISTS claim_tenant_isolation ON claim;
    CREATE POLICY claim_tenant_isolation ON claim USING (tenant_id = current_tenant()) WITH CHECK (tenant_id = current_tenant());
    """)


def downgrade() -> None:
    op.execute("""
    DROP TABLE IF EXISTS claim;
    DROP TABLE IF EXISTS payment;
    DROP TABLE IF EXISTS invoice_item;
    DROP TABLE IF EXISTS invoice;
    DROP TABLE IF EXISTS patient_coverage;
    DROP TABLE IF EXISTS charge_master;
    """)

