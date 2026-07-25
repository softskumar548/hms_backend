-- HMS platform — initial schema + Row-Level Security (PLT-002, PLT-005).
-- Applied automatically by the postgres container on first start.

CREATE TABLE IF NOT EXISTS tenant (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    region       TEXT NOT NULL DEFAULT 'india',
    locale       TEXT NOT NULL DEFAULT 'en-IN',
    currency     TEXT NOT NULL DEFAULT 'INR',
    features     JSONB NOT NULL DEFAULT '{"ref_commission": false}'::jsonb,
    status       TEXT NOT NULL DEFAULT 'active',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Seed baseline demo tenants
INSERT INTO tenant (id, name) VALUES ('apollo', 'Apollo Clinic (demo)'), ('kims', 'KIMS Hospital (demo)'), ('t_a', 'Tenant A'), ('t_b', 'Tenant B') ON CONFLICT DO NOTHING;

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'hms_app') THEN
        CREATE ROLE hms_app LOGIN PASSWORD 'app_password_change_me';
    END IF;
END $$;

CREATE OR REPLACE FUNCTION current_tenant() RETURNS TEXT AS $$
    SELECT current_setting('app.tenant_id', true);
$$ LANGUAGE sql STABLE;

-- 1. Patient Table
CREATE TABLE IF NOT EXISTS patient (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id          TEXT NOT NULL REFERENCES tenant(id),
    given_name         TEXT NOT NULL,
    family_name        TEXT NOT NULL,
    dob                DATE,
    national_id        TEXT,
    phone              TEXT,
    gender             TEXT,
    email              TEXT,
    preferred_language TEXT DEFAULT 'te',
    abha_number        TEXT,
    abha_address       TEXT,
    aarogyasri_id      TEXT,
    pmjay_id           TEXT,
    aadhaar_last_four  TEXT,
    referred_by_type   TEXT,
    referred_by_name   TEXT,
    referred_by_id     TEXT,
    address            JSONB,
    next_of_kin        JSONB,
    fhir_resource      JSONB,
    created_by         TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_patient_tenant ON patient (tenant_id);
CREATE INDEX IF NOT EXISTS ix_patient_name   ON patient (tenant_id, family_name, given_name);

-- 2. Audit Event Table (Append-Only PLT-005)
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

-- 3. Patient Consent Table
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

-- 4. Explicit module tables
CREATE TABLE IF NOT EXISTS site (
    id           TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id    TEXT NOT NULL REFERENCES tenant(id),
    name         TEXT NOT NULL,
    address      TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS room (
    id           TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    site_id      TEXT REFERENCES site(id),
    tenant_id    TEXT NOT NULL REFERENCES tenant(id),
    name         TEXT NOT NULL,
    type         TEXT DEFAULT 'consult',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS service (
    id               TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id        TEXT NOT NULL REFERENCES tenant(id),
    name             TEXT NOT NULL,
    duration_minutes INT DEFAULT 30,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS practitioner (
    id           TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id    TEXT NOT NULL REFERENCES tenant(id),
    name         TEXT NOT NULL,
    specialism   TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS appointment (
    id              TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id       TEXT NOT NULL REFERENCES tenant(id),
    patient_id      UUID REFERENCES patient(id),
    practitioner_id TEXT REFERENCES practitioner(id),
    site_id         TEXT REFERENCES site(id),
    room_id         TEXT REFERENCES room(id),
    service_id      TEXT REFERENCES service(id),
    status          TEXT NOT NULL DEFAULT 'BOOKED',
    start_time      TIMESTAMPTZ NOT NULL DEFAULT now(),
    end_time        TIMESTAMPTZ NOT NULL DEFAULT now() + interval '30 min',
    referred_by_id   TEXT,
    referred_by_name TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS encounter (
    id              TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id       TEXT NOT NULL REFERENCES tenant(id),
    appointment_id  TEXT REFERENCES appointment(id),
    patient_id      UUID REFERENCES patient(id),
    practitioner_id TEXT REFERENCES practitioner(id),
    site_id         TEXT REFERENCES site(id),
    status          TEXT NOT NULL DEFAULT 'open',
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at        TIMESTAMPTZ,
    signed_at       TIMESTAMPTZ,
    signed_by       TEXT,
    updated_at      TIMESTAMPTZ DEFAULT now(),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS patient_coverage (
    id                    TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id             TEXT NOT NULL REFERENCES tenant(id),
    patient_id            UUID REFERENCES patient(id),
    scheme_type           TEXT,
    plan_name             TEXT,
    member_id             TEXT,
    validity_start        DATE,
    validity_end          DATE,
    patient_share_percent INT DEFAULT 20,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS invoice (
    id                     TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id              TEXT NOT NULL REFERENCES tenant(id),
    patient_id             UUID REFERENCES patient(id),
    encounter_id           TEXT REFERENCES encounter(id),
    coverage_id            TEXT REFERENCES patient_coverage(id),
    status                 TEXT NOT NULL DEFAULT 'draft',
    total_amount           NUMERIC DEFAULT 0,
    payer_responsibility   NUMERIC DEFAULT 0,
    patient_responsibility NUMERIC DEFAULT 0,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS medication_catalog (
    id           TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id    TEXT NOT NULL REFERENCES tenant(id),
    name         TEXT NOT NULL,
    generic_name TEXT,
    form         TEXT,
    strength     TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS lab_catalog (
    id                       TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id                TEXT NOT NULL REFERENCES tenant(id),
    test_code                TEXT NOT NULL,
    name                     TEXT NOT NULL,
    specimen_requirements    TEXT,
    preparation_requirements TEXT,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS lab_order (
    id              TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id       TEXT NOT NULL REFERENCES tenant(id),
    patient_id      UUID REFERENCES patient(id),
    practitioner_id TEXT REFERENCES practitioner(id),
    encounter_id    TEXT REFERENCES encounter(id),
    status          TEXT NOT NULL DEFAULT 'ordered',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS prescription (
    id              TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id       TEXT NOT NULL REFERENCES tenant(id),
    patient_id      UUID REFERENCES patient(id),
    practitioner_id TEXT REFERENCES practitioner(id),
    encounter_id    TEXT REFERENCES encounter(id),
    status          TEXT NOT NULL DEFAULT 'draft',
    signed_at       TIMESTAMPTZ,
    signed_by       TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS charge_master (
    id             TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id      TEXT NOT NULL REFERENCES tenant(id),
    code           TEXT NOT NULL,
    name           TEXT NOT NULL,
    category       TEXT NOT NULL,
    standard_price NUMERIC DEFAULT 0,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS webhook_subscription (
    id           TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id    TEXT NOT NULL REFERENCES tenant(id),
    event_type   TEXT NOT NULL,
    url          TEXT NOT NULL,
    secret_key   TEXT NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS prerequisite_definition (
    id               TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id        TEXT NOT NULL REFERENCES tenant(id),
    code             TEXT NOT NULL,
    description      TEXT NOT NULL,
    enforcement_type TEXT DEFAULT 'advisory',
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS practitioner_availability (
    id              TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id       TEXT NOT NULL REFERENCES tenant(id),
    practitioner_id TEXT REFERENCES practitioner(id),
    site_id         TEXT REFERENCES site(id),
    day_of_week     INT NOT NULL,
    start_time      TEXT NOT NULL,
    end_time        TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS appointment_prerequisite (
    id              TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id       TEXT NOT NULL REFERENCES tenant(id),
    appointment_id  TEXT REFERENCES appointment(id),
    prerequisite_id TEXT REFERENCES prerequisite_definition(id),
    satisfied       BOOLEAN DEFAULT false,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS clinical_note (
    id                TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id         TEXT NOT NULL REFERENCES tenant(id),
    encounter_id      TEXT REFERENCES encounter(id),
    template_type     TEXT,
    structured_content JSONB,
    rich_text_content TEXT,
    version           INT DEFAULT 1,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS clinical_note_addendum (
    id           TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id    TEXT NOT NULL REFERENCES tenant(id),
    encounter_id TEXT REFERENCES encounter(id),
    author_id    TEXT,
    content      TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS encounter_document (
    id           TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id    TEXT NOT NULL REFERENCES tenant(id),
    encounter_id TEXT REFERENCES encounter(id),
    patient_id   UUID REFERENCES patient(id),
    file_path    TEXT,
    file_type    TEXT,
    label        TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS allergy_intolerance (
    id                TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id         TEXT NOT NULL REFERENCES tenant(id),
    patient_id        UUID REFERENCES patient(id),
    substance_code    TEXT,
    substance_display TEXT,
    reaction          TEXT,
    severity          TEXT,
    criticality       TEXT,
    is_no_known       BOOLEAN DEFAULT FALSE,
    asserted_at       TIMESTAMPTZ DEFAULT now(),
    asserted_by       TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS condition (
    id              TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id       TEXT NOT NULL REFERENCES tenant(id),
    patient_id      UUID REFERENCES patient(id),
    clinical_status TEXT DEFAULT 'active',
    code            TEXT,
    display         TEXT,
    onset_date      DATE,
    resolution_date DATE,
    asserted_at     TIMESTAMPTZ DEFAULT now(),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS medication_statement (
    id                 TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id          TEXT NOT NULL REFERENCES tenant(id),
    patient_id         UUID REFERENCES patient(id),
    status             TEXT DEFAULT 'active',
    medication_code    TEXT,
    medication_display TEXT,
    sig                TEXT,
    asserted_at        TIMESTAMPTZ DEFAULT now(),
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS vital_sign (
    id           TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id    TEXT NOT NULL REFERENCES tenant(id),
    encounter_id TEXT REFERENCES encounter(id),
    patient_id   UUID REFERENCES patient(id),
    type         TEXT,
    value        NUMERIC,
    unit         TEXT,
    recorded_at  TIMESTAMPTZ DEFAULT now(),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS lab_order_item (
    id        TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id TEXT NOT NULL REFERENCES tenant(id),
    order_id  TEXT REFERENCES lab_order(id),
    test_id   TEXT REFERENCES lab_catalog(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS lab_result (
    id              TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id       TEXT NOT NULL REFERENCES tenant(id),
    order_id        TEXT REFERENCES lab_order(id),
    patient_id      UUID REFERENCES patient(id),
    test_id         TEXT REFERENCES lab_catalog(id),
    value           NUMERIC,
    unit            TEXT,
    reference_range TEXT,
    is_abnormal     BOOLEAN DEFAULT FALSE,
    is_critical     BOOLEAN DEFAULT FALSE,
    resulted_at     TIMESTAMPTZ DEFAULT now(),
    reviewed_at     TIMESTAMPTZ,
    reviewed_by     TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS lab_unmatched_result (
    id        TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id TEXT NOT NULL REFERENCES tenant(id),
    payload   JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS tenant_formulary (
    id            TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id     TEXT NOT NULL REFERENCES tenant(id),
    medication_id TEXT REFERENCES medication_catalog(id),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS prescription_item (
    id              TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id       TEXT NOT NULL REFERENCES tenant(id),
    prescription_id TEXT REFERENCES prescription(id),
    medication_id   TEXT REFERENCES medication_catalog(id),
    dose            NUMERIC,
    unit            TEXT,
    route           TEXT,
    frequency       TEXT,
    duration_days   INT,
    quantity        INT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS prescription_override (
    id              TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id       TEXT NOT NULL REFERENCES tenant(id),
    prescription_id TEXT REFERENCES prescription(id),
    alert_type      TEXT,
    severity        TEXT,
    reason          TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS prescription_favorite (
    id              TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id       TEXT NOT NULL REFERENCES tenant(id),
    practitioner_id TEXT REFERENCES practitioner(id),
    medication_id   TEXT REFERENCES medication_catalog(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS invoice_item (
    id             TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id      TEXT NOT NULL REFERENCES tenant(id),
    invoice_id     TEXT REFERENCES invoice(id),
    charge_item_id TEXT REFERENCES charge_master(id),
    unit_price     NUMERIC,
    patient_share  NUMERIC,
    payer_share    NUMERIC,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS payment (
    id             TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id      TEXT NOT NULL REFERENCES tenant(id),
    invoice_id     TEXT REFERENCES invoice(id),
    payment_method TEXT,
    amount         NUMERIC,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS claim (
    id            TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id     TEXT NOT NULL REFERENCES tenant(id),
    invoice_id    TEXT REFERENCES invoice(id),
    coverage_id   TEXT REFERENCES patient_coverage(id),
    total_claimed NUMERIC,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS portal_invitation (
    id         TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id  TEXT NOT NULL REFERENCES tenant(id),
    patient_id UUID REFERENCES patient(id),
    email      TEXT,
    phone      TEXT,
    otp_code   TEXT,
    expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS portal_user (
    id            TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id     TEXT NOT NULL REFERENCES tenant(id),
    patient_id    UUID REFERENCES patient(id),
    username      TEXT,
    password_hash TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS portal_questionnaire (
    id                 TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id          TEXT NOT NULL REFERENCES tenant(id),
    appointment_id     TEXT REFERENCES appointment(id),
    questionnaire_type TEXT,
    questions_json     JSONB,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS portal_proxy (
    id                TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id         TEXT NOT NULL REFERENCES tenant(id),
    patient_id        UUID REFERENCES patient(id),
    proxy_patient_id  UUID REFERENCES patient(id),
    relationship_type TEXT,
    expires_at        TIMESTAMPTZ,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS portal_message (
    id           TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id    TEXT NOT NULL REFERENCES tenant(id),
    patient_id   UUID REFERENCES patient(id),
    direction    TEXT,
    message_text TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS webhook_delivery_log (
    id              TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id       TEXT NOT NULL REFERENCES tenant(id),
    subscription_id TEXT REFERENCES webhook_subscription(id),
    event_type      TEXT,
    payload         JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS integration_log (
    id           TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id    TEXT NOT NULL REFERENCES tenant(id),
    direction    TEXT,
    message_type TEXT,
    status       TEXT,
    payload      JSONB,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Macro to create remaining child tables dynamically with RLS policies and grants
DO $$
DECLARE
    tbl TEXT;
    pol_name TEXT;
    explicit_tables TEXT[] := ARRAY[
        'patient', 'audit_event', 'patient_consent', 'site', 'room', 'service', 'practitioner', 
        'appointment', 'encounter', 'patient_coverage', 'invoice', 'medication_catalog', 
        'lab_catalog', 'lab_order', 'prescription', 'charge_master', 'webhook_subscription', 
        'prerequisite_definition', 'practitioner_availability', 'appointment_prerequisite',
        'clinical_note', 'clinical_note_addendum', 'encounter_document', 'allergy_intolerance',
        'condition', 'medication_statement', 'vital_sign', 'lab_order_item', 'lab_result',
        'lab_unmatched_result', 'tenant_formulary', 'prescription_item', 'prescription_override',
        'prescription_favorite', 'invoice_item', 'payment', 'claim', 'portal_invitation',
        'portal_user', 'portal_questionnaire', 'portal_proxy', 'portal_message',
        'webhook_delivery_log', 'integration_log'
    ];
    tables TEXT[] := ARRAY[
        'clinical_service', 'tenant_config', 'tenant_invitation', 'migration_staging', 'readiness_checklist',
        'subscription_invoice', 'cashless_claim', 'problem', 'order_catalog_item', 'order', 
        'order_item', 'analyte_result', 'invoice_line', 'patient_portal_user', 'portal_intake_form',
        'ops_metric', 'referral_analytic', 'referral', 'referrer', 'followup_booking',
        'prerequisite_library', 'referral_commission', 'referral_prerequisite',
        'followup_prerequisite', 'abha_linkage', 'aarogyasri_eligibility'
    ];
BEGIN
    FOREACH tbl IN ARRAY tables LOOP
        EXECUTE format('
            CREATE TABLE IF NOT EXISTS %I (
                id           TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
                tenant_id    TEXT NOT NULL REFERENCES tenant(id),
                name         TEXT,
                data         JSONB DEFAULT ''{}''::jsonb,
                created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
            );
        ', tbl);
    END LOOP;

    FOREACH tbl IN ARRAY (explicit_tables || tables) LOOP
        pol_name := tbl || '_isolation';
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY;', tbl);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY;', tbl);
        EXECUTE format('DROP POLICY IF EXISTS %I ON %I;', pol_name, tbl);
        EXECUTE format('CREATE POLICY %I ON %I USING (tenant_id = current_tenant()) WITH CHECK (tenant_id = current_tenant());', pol_name, tbl);

        IF tbl = 'audit_event' THEN
            EXECUTE format('GRANT SELECT, INSERT ON %I TO hms_app;', tbl);
        ELSE
            EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %I TO hms_app;', tbl);
        END IF;
    END LOOP;
END $$;

GRANT SELECT, INSERT, UPDATE ON tenant TO hms_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO hms_app;
