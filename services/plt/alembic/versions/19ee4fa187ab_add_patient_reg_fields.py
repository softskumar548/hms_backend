"""add_patient_reg_fields

Revision ID: 19ee4fa187ab
Revises: 0001_baseline
Create Date: 2026-07-20 22:47:19.413356

Story / FRD: REG-001, REG-002, REG-009
"""
from __future__ import annotations

from alembic import op


revision = '19ee4fa187ab'
down_revision = '0001_baseline'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Idempotent DDL (IF NOT EXISTS), matching every other migration in this
    # repo: infra/postgres/init.sql may already have created these columns on a
    # fresh database, and the migration must still converge on older databases
    # where they are missing.
    op.execute("""
    ALTER TABLE patient ADD COLUMN IF NOT EXISTS abha_number TEXT;
    ALTER TABLE patient ADD COLUMN IF NOT EXISTS abha_address TEXT;
    ALTER TABLE patient ADD COLUMN IF NOT EXISTS aarogyasri_id TEXT;
    ALTER TABLE patient ADD COLUMN IF NOT EXISTS pmjay_id TEXT;
    ALTER TABLE patient ADD COLUMN IF NOT EXISTS aadhaar_last_four VARCHAR(4);
    ALTER TABLE patient ADD COLUMN IF NOT EXISTS referred_by_type TEXT;
    ALTER TABLE patient ADD COLUMN IF NOT EXISTS referred_by_name TEXT;
    ALTER TABLE patient ADD COLUMN IF NOT EXISTS referred_by_id TEXT;
    ALTER TABLE patient ADD COLUMN IF NOT EXISTS gender TEXT;
    ALTER TABLE patient ADD COLUMN IF NOT EXISTS email TEXT;
    ALTER TABLE patient ADD COLUMN IF NOT EXISTS preferred_language TEXT;
    ALTER TABLE patient ADD COLUMN IF NOT EXISTS address JSONB;
    ALTER TABLE patient ADD COLUMN IF NOT EXISTS next_of_kin JSONB;
    ALTER TABLE patient ADD COLUMN IF NOT EXISTS fhir_resource JSONB;

    CREATE INDEX IF NOT EXISTS ix_patient_abha ON patient (tenant_id, abha_number);
    CREATE INDEX IF NOT EXISTS ix_patient_phone ON patient (tenant_id, phone);
    CREATE INDEX IF NOT EXISTS ix_patient_fhir_resource
        ON patient USING gin (fhir_resource jsonb_path_ops);
    """)


def downgrade() -> None:
    # Drop indexes
    op.drop_index('ix_patient_fhir_resource', table_name='patient')
    op.drop_index('ix_patient_phone', table_name='patient')
    op.drop_index('ix_patient_abha', table_name='patient')

    # Drop columns
    op.drop_column('patient', 'fhir_resource')
    op.drop_column('patient', 'next_of_kin')
    op.drop_column('patient', 'address')
    op.drop_column('patient', 'preferred_language')
    op.drop_column('patient', 'email')
    op.drop_column('patient', 'gender')
    op.drop_column('patient', 'referred_by_id')
    op.drop_column('patient', 'referred_by_name')
    op.drop_column('patient', 'referred_by_type')
    op.drop_column('patient', 'aadhaar_last_four')
    op.drop_column('patient', 'pmjay_id')
    op.drop_column('patient', 'aarogyasri_id')
    op.drop_column('patient', 'abha_address')
    op.drop_column('patient', 'abha_number')

