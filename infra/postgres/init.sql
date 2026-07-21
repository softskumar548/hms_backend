-- HMS platform — initial schema + Row-Level Security (PLT-002, PLT-005).
-- Applied automatically by the postgres container on first start.
--
-- Isolation model: every tenant-scoped table carries tenant_id and has an RLS
-- policy that compares it to the session variable app.tenant_id, which the app
-- sets per-transaction via hms_tenancy.tenant_session(). No app.tenant_id => no
-- rows. This is enforced by the database, so an application bug cannot leak data
-- across tenants.

-- ---------------------------------------------------------------------------
-- Tenants (not itself RLS-protected; managed by the platform operator)
-- ---------------------------------------------------------------------------
CREATE TABLE tenant (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    region       TEXT NOT NULL DEFAULT 'india',
    locale       TEXT NOT NULL DEFAULT 'en-IN',
    currency     TEXT NOT NULL DEFAULT 'INR',
    -- feature flags; REF commission engine defaults OFF (India: locked off)
    features     JSONB NOT NULL DEFAULT '{"ref_commission": false}'::jsonb,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Patient (demo of a tenant-scoped clinical table)
-- ---------------------------------------------------------------------------
CREATE TABLE patient (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    TEXT NOT NULL REFERENCES tenant(id),
    given_name   TEXT NOT NULL,
    family_name  TEXT NOT NULL,
    dob          DATE,
    national_id  TEXT,               -- e.g. Aadhaar-linked ABHA later (AP/ABDM)
    phone        TEXT,
    created_by   TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_patient_tenant ON patient (tenant_id);
CREATE INDEX ix_patient_name   ON patient (tenant_id, family_name, given_name);

-- ---------------------------------------------------------------------------
-- Patient consent (PLT-010). Per-patient, per-purpose grants. Append-first:
-- revocation sets revoked_at; the grant row is never deleted, preserving trail.
-- The purpose taxonomy needs product/clinical sign-off — the DB stores whatever
-- the app writes.
-- ---------------------------------------------------------------------------
CREATE TABLE patient_consent (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id    TEXT NOT NULL REFERENCES tenant(id),
    patient_id   UUID NOT NULL,      -- FK enforced logically via app + RLS
    purpose      TEXT NOT NULL,      -- e.g. "share:abdm", "comms:appointment_reminder"
    granted_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    granted_by   TEXT,
    revoked_at   TIMESTAMPTZ,        -- NULL = still granted
    source_note  TEXT
);
CREATE INDEX ix_consent_patient_purpose
    ON patient_consent (tenant_id, patient_id, purpose)
    WHERE revoked_at IS NULL;

-- ---------------------------------------------------------------------------
-- Audit event (append-only; PLT-005). No UPDATE/DELETE granted to app role.
-- ---------------------------------------------------------------------------
CREATE TABLE audit_event (
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
CREATE INDEX ix_audit_tenant_time ON audit_event (tenant_id, occurred_at);

-- ---------------------------------------------------------------------------
-- Application role + RLS. The app connects as hms_app (NOT the superuser), so
-- RLS actually applies (superusers bypass RLS).
-- ---------------------------------------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'hms_app') THEN
        CREATE ROLE hms_app LOGIN PASSWORD 'app_password_change_me';
    END IF;
END $$;

GRANT SELECT, INSERT, UPDATE ON patient TO hms_app;
GRANT SELECT, INSERT ON audit_event TO hms_app;      -- append-only: no UPDATE/DELETE
GRANT SELECT, INSERT, UPDATE ON patient_consent TO hms_app;   -- UPDATE only to set revoked_at
GRANT SELECT ON tenant TO hms_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO hms_app;

-- Helper: read the per-request tenant from the session variable.
CREATE OR REPLACE FUNCTION current_tenant() RETURNS TEXT AS $$
    SELECT current_setting('app.tenant_id', true);
$$ LANGUAGE sql STABLE;

-- Patient RLS: rows visible/writable only for the current tenant.
ALTER TABLE patient ENABLE ROW LEVEL SECURITY;
ALTER TABLE patient FORCE ROW LEVEL SECURITY;
CREATE POLICY patient_tenant_isolation ON patient
    USING (tenant_id = current_tenant())
    WITH CHECK (tenant_id = current_tenant());

-- Audit RLS: a tenant reads only its own events; inserts must match tenant.
ALTER TABLE audit_event ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_event FORCE ROW LEVEL SECURITY;
CREATE POLICY audit_tenant_isolation ON audit_event
    USING (tenant_id = current_tenant())
    WITH CHECK (tenant_id = current_tenant());

-- Consent RLS: same tenant-scoping model as patient/audit.
ALTER TABLE patient_consent ENABLE ROW LEVEL SECURITY;
ALTER TABLE patient_consent FORCE ROW LEVEL SECURITY;
CREATE POLICY consent_tenant_isolation ON patient_consent
    USING (tenant_id = current_tenant())
    WITH CHECK (tenant_id = current_tenant());
