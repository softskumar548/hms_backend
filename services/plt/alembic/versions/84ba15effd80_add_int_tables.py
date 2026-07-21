"""add_int_tables

Revision ID: 84ba15effd80
Revises: ff72b1b00476
Create Date: 2026-07-20 23:15:29.026883

Story / FRD: INT-001, INT-002, INT-003, INT-004, INT-006, INT-007
"""
from __future__ import annotations

from alembic import op


revision = '84ba15effd80'
down_revision = 'ff72b1b00476'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE IF NOT EXISTS webhook_subscription (
        id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id  TEXT NOT NULL REFERENCES tenant(id),
        event_type TEXT NOT NULL, -- 'appointment.*', 'result.final', 'invoice.finalized', etc.
        url        TEXT NOT NULL,
        secret_key TEXT NOT NULL,
        active     BOOLEAN NOT NULL DEFAULT TRUE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS ix_webhook_sub_tenant ON webhook_subscription (tenant_id);

    CREATE TABLE IF NOT EXISTS webhook_delivery_log (
        id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id       TEXT NOT NULL REFERENCES tenant(id),
        subscription_id UUID NOT NULL REFERENCES webhook_subscription(id) ON DELETE CASCADE,
        event_type      TEXT NOT NULL,
        payload         TEXT NOT NULL,
        status_code     INTEGER,
        attempt         INTEGER NOT NULL DEFAULT 1,
        success         BOOLEAN NOT NULL DEFAULT FALSE,
        error_message   TEXT,
        created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS ix_webhook_log_tenant ON webhook_delivery_log (tenant_id);

    CREATE TABLE IF NOT EXISTS integration_log (
        id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id     TEXT NOT NULL REFERENCES tenant(id),
        direction     TEXT NOT NULL, -- 'inbound', 'outbound'
        message_type  TEXT NOT NULL, -- 'HL7_ORM', 'HL7_ORU', 'CSV'
        status        TEXT NOT NULL, -- 'success', 'failed'
        payload       TEXT NOT NULL,
        error_message TEXT,
        created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS ix_integration_log_tenant ON integration_log (tenant_id);

    -- Grant Permissions
    GRANT SELECT, INSERT, UPDATE, DELETE ON webhook_subscription TO hms_app;
    GRANT SELECT, INSERT, UPDATE, DELETE ON webhook_delivery_log TO hms_app;
    GRANT SELECT, INSERT, UPDATE, DELETE ON integration_log TO hms_app;

    -- Enable RLS & Configure Policies
    ALTER TABLE webhook_subscription ENABLE ROW LEVEL SECURITY;
    ALTER TABLE webhook_subscription FORCE ROW LEVEL SECURITY;
    DROP POLICY IF EXISTS subscription_tenant_isolation ON webhook_subscription;
    CREATE POLICY subscription_tenant_isolation ON webhook_subscription USING (tenant_id = current_tenant()) WITH CHECK (tenant_id = current_tenant());

    ALTER TABLE webhook_delivery_log ENABLE ROW LEVEL SECURITY;
    ALTER TABLE webhook_delivery_log FORCE ROW LEVEL SECURITY;
    DROP POLICY IF EXISTS log_tenant_isolation ON webhook_delivery_log;
    CREATE POLICY log_tenant_isolation ON webhook_delivery_log USING (tenant_id = current_tenant()) WITH CHECK (tenant_id = current_tenant());

    ALTER TABLE integration_log ENABLE ROW LEVEL SECURITY;
    ALTER TABLE integration_log FORCE ROW LEVEL SECURITY;
    DROP POLICY IF EXISTS int_log_tenant_isolation ON integration_log;
    CREATE POLICY int_log_tenant_isolation ON integration_log USING (tenant_id = current_tenant()) WITH CHECK (tenant_id = current_tenant());
    """)


def downgrade() -> None:
    op.execute("""
    DROP TABLE IF EXISTS integration_log;
    DROP TABLE IF EXISTS webhook_delivery_log;
    DROP TABLE IF EXISTS webhook_subscription;
    """)

