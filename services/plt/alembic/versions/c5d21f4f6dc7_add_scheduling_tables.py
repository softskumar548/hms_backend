"""add_scheduling_tables

Revision ID: c5d21f4f6dc7
Revises: 19ee4fa187ab
Create Date: 2026-07-20 22:53:37.670692

Story / FRD: SCH-001, SCH-002, SCH-005, SCH-010
"""
from __future__ import annotations

from alembic import op


revision = 'c5d21f4f6dc7'
down_revision = '19ee4fa187ab'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE IF NOT EXISTS site (
        id           TEXT PRIMARY KEY,
        tenant_id    TEXT NOT NULL REFERENCES tenant(id),
        name         TEXT NOT NULL,
        address      TEXT,
        created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS ix_site_tenant ON site (tenant_id);

    CREATE TABLE IF NOT EXISTS room (
        id           TEXT PRIMARY KEY,
        site_id      TEXT NOT NULL REFERENCES site(id),
        tenant_id    TEXT NOT NULL REFERENCES tenant(id),
        name         TEXT NOT NULL,
        type         TEXT,
        created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS ix_room_tenant ON room (tenant_id);

    CREATE TABLE IF NOT EXISTS service (
        id               TEXT PRIMARY KEY,
        tenant_id        TEXT NOT NULL REFERENCES tenant(id),
        name             TEXT NOT NULL,
        duration_minutes INTEGER NOT NULL,
        created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS ix_service_tenant ON service (tenant_id);

    CREATE TABLE IF NOT EXISTS practitioner (
        id           TEXT PRIMARY KEY,
        tenant_id    TEXT NOT NULL REFERENCES tenant(id),
        name         TEXT NOT NULL,
        specialism   TEXT,
        created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS ix_practitioner_tenant ON practitioner (tenant_id);

    CREATE TABLE IF NOT EXISTS practitioner_availability (
        id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        practitioner_id TEXT NOT NULL REFERENCES practitioner(id),
        site_id         TEXT NOT NULL REFERENCES site(id),
        tenant_id       TEXT NOT NULL REFERENCES tenant(id),
        day_of_week     INTEGER NOT NULL, -- 0 (Sunday) to 6 (Saturday)
        start_time      TIME NOT NULL,
        end_time        TIME NOT NULL
    );
    CREATE INDEX IF NOT EXISTS ix_availability_tenant_practitioner ON practitioner_availability (tenant_id, practitioner_id);

    CREATE TABLE IF NOT EXISTS prerequisite_definition (
        id               TEXT PRIMARY KEY,
        tenant_id        TEXT NOT NULL REFERENCES tenant(id),
        code             TEXT NOT NULL,
        description      TEXT NOT NULL,
        enforcement_type TEXT NOT NULL DEFAULT 'advisory' -- 'hard-stop' or 'advisory'
    );
    CREATE INDEX IF NOT EXISTS ix_prereq_tenant ON prerequisite_definition (tenant_id);

    CREATE TABLE IF NOT EXISTS appointment (
        id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id       TEXT NOT NULL REFERENCES tenant(id),
        patient_id      UUID NOT NULL REFERENCES patient(id),
        practitioner_id TEXT NOT NULL REFERENCES practitioner(id),
        site_id         TEXT NOT NULL REFERENCES site(id),
        room_id         TEXT NOT NULL REFERENCES room(id),
        service_id      TEXT NOT NULL REFERENCES service(id),
        status          TEXT NOT NULL, -- 'DRAFT', 'BOOKED', 'ARRIVED', 'WAITING', 'IN_CONSULTATION', 'COMPLETED', 'CANCELLED', 'NO_SHOW'
        start_time      TIMESTAMPTZ NOT NULL,
        end_time        TIMESTAMPTZ NOT NULL,
        referred_by_id  TEXT,
        referred_by_name TEXT,
        created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS ix_appointment_tenant_time ON appointment (tenant_id, start_time);
    CREATE INDEX IF NOT EXISTS ix_appointment_practitioner_time ON appointment (tenant_id, practitioner_id, start_time);
    CREATE INDEX IF NOT EXISTS ix_appointment_patient ON appointment (tenant_id, patient_id);

    CREATE TABLE IF NOT EXISTS appointment_prerequisite (
        appointment_id  UUID NOT NULL REFERENCES appointment(id),
        prerequisite_id TEXT NOT NULL REFERENCES prerequisite_definition(id),
        tenant_id       TEXT NOT NULL REFERENCES tenant(id),
        satisfied       BOOLEAN NOT NULL DEFAULT FALSE,
        satisfied_at    TIMESTAMPTZ,
        satisfied_by    TEXT,
        PRIMARY KEY (appointment_id, prerequisite_id)
    );
    CREATE INDEX IF NOT EXISTS ix_app_prereq_tenant ON appointment_prerequisite (tenant_id);

    -- Grant Permissions
    GRANT SELECT, INSERT, UPDATE, DELETE ON site TO hms_app;
    GRANT SELECT, INSERT, UPDATE, DELETE ON room TO hms_app;
    GRANT SELECT, INSERT, UPDATE, DELETE ON service TO hms_app;
    GRANT SELECT, INSERT, UPDATE, DELETE ON practitioner TO hms_app;
    GRANT SELECT, INSERT, UPDATE, DELETE ON practitioner_availability TO hms_app;
    GRANT SELECT, INSERT, UPDATE, DELETE ON prerequisite_definition TO hms_app;
    GRANT SELECT, INSERT, UPDATE, DELETE ON appointment TO hms_app;
    GRANT SELECT, INSERT, UPDATE, DELETE ON appointment_prerequisite TO hms_app;

    -- Enable RLS & Configure Policies
    ALTER TABLE site ENABLE ROW LEVEL SECURITY;
    ALTER TABLE site FORCE ROW LEVEL SECURITY;
    DROP POLICY IF EXISTS site_tenant_isolation ON site;
    CREATE POLICY site_tenant_isolation ON site USING (tenant_id = current_tenant()) WITH CHECK (tenant_id = current_tenant());

    ALTER TABLE room ENABLE ROW LEVEL SECURITY;
    ALTER TABLE room FORCE ROW LEVEL SECURITY;
    DROP POLICY IF EXISTS room_tenant_isolation ON room;
    CREATE POLICY room_tenant_isolation ON room USING (tenant_id = current_tenant()) WITH CHECK (tenant_id = current_tenant());

    ALTER TABLE service ENABLE ROW LEVEL SECURITY;
    ALTER TABLE service FORCE ROW LEVEL SECURITY;
    DROP POLICY IF EXISTS service_tenant_isolation ON service;
    CREATE POLICY service_tenant_isolation ON service USING (tenant_id = current_tenant()) WITH CHECK (tenant_id = current_tenant());

    ALTER TABLE practitioner ENABLE ROW LEVEL SECURITY;
    ALTER TABLE practitioner FORCE ROW LEVEL SECURITY;
    DROP POLICY IF EXISTS practitioner_tenant_isolation ON practitioner;
    CREATE POLICY practitioner_tenant_isolation ON practitioner USING (tenant_id = current_tenant()) WITH CHECK (tenant_id = current_tenant());

    ALTER TABLE practitioner_availability ENABLE ROW LEVEL SECURITY;
    ALTER TABLE practitioner_availability FORCE ROW LEVEL SECURITY;
    DROP POLICY IF EXISTS availability_tenant_isolation ON practitioner_availability;
    CREATE POLICY availability_tenant_isolation ON practitioner_availability USING (tenant_id = current_tenant()) WITH CHECK (tenant_id = current_tenant());

    ALTER TABLE prerequisite_definition ENABLE ROW LEVEL SECURITY;
    ALTER TABLE prerequisite_definition FORCE ROW LEVEL SECURITY;
    DROP POLICY IF EXISTS prerequisite_tenant_isolation ON prerequisite_definition;
    CREATE POLICY prerequisite_tenant_isolation ON prerequisite_definition USING (tenant_id = current_tenant()) WITH CHECK (tenant_id = current_tenant());

    ALTER TABLE appointment ENABLE ROW LEVEL SECURITY;
    ALTER TABLE appointment FORCE ROW LEVEL SECURITY;
    DROP POLICY IF EXISTS appointment_tenant_isolation ON appointment;
    CREATE POLICY appointment_tenant_isolation ON appointment USING (tenant_id = current_tenant()) WITH CHECK (tenant_id = current_tenant());

    ALTER TABLE appointment_prerequisite ENABLE ROW LEVEL SECURITY;
    ALTER TABLE appointment_prerequisite FORCE ROW LEVEL SECURITY;
    DROP POLICY IF EXISTS app_prereq_tenant_isolation ON appointment_prerequisite;
    CREATE POLICY app_prereq_tenant_isolation ON appointment_prerequisite USING (tenant_id = current_tenant()) WITH CHECK (tenant_id = current_tenant());
    """)


def downgrade() -> None:
    op.execute("""
    DROP TABLE IF EXISTS appointment_prerequisite;
    DROP TABLE IF EXISTS appointment;
    DROP TABLE IF EXISTS prerequisite_definition;
    DROP TABLE IF EXISTS practitioner_availability;
    DROP TABLE IF EXISTS practitioner;
    DROP TABLE IF EXISTS service;
    DROP TABLE IF EXISTS room;
    DROP TABLE IF EXISTS site;
    """)

