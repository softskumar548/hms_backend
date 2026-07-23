"""add_patient_reg_fields

Revision ID: 19ee4fa187ab
Revises: 0001_baseline
Create Date: 2026-07-20 22:47:19.413356

Story / FRD: REG-001, REG-002, REG-009
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = '19ee4fa187ab'
down_revision = '0001_baseline'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add columns to patient table
    op.add_column('patient', sa.Column('abha_number', sa.Text(), nullable=True))
    op.add_column('patient', sa.Column('abha_address', sa.Text(), nullable=True))
    op.add_column('patient', sa.Column('aarogyasri_id', sa.Text(), nullable=True))
    op.add_column('patient', sa.Column('pmjay_id', sa.Text(), nullable=True))
    op.add_column('patient', sa.Column('aadhaar_last_four', sa.String(length=4), nullable=True))
    op.add_column('patient', sa.Column('referred_by_type', sa.Text(), nullable=True))
    op.add_column('patient', sa.Column('referred_by_name', sa.Text(), nullable=True))
    op.add_column('patient', sa.Column('referred_by_id', sa.Text(), nullable=True))
    op.add_column('patient', sa.Column('gender', sa.Text(), nullable=True))
    op.add_column('patient', sa.Column('email', sa.Text(), nullable=True))
    op.add_column('patient', sa.Column('preferred_language', sa.Text(), nullable=True))
    op.add_column('patient', sa.Column('address', JSONB(), nullable=True))
    op.add_column('patient', sa.Column('next_of_kin', JSONB(), nullable=True))
    op.add_column('patient', sa.Column('fhir_resource', JSONB(), nullable=True))

    # Create indexes
    op.create_index('ix_patient_abha', 'patient', ['tenant_id', 'abha_number'], unique=False)
    op.create_index('ix_patient_phone', 'patient', ['tenant_id', 'phone'], unique=False)
    op.create_index(
        'ix_patient_fhir_resource',
        'patient',
        ['fhir_resource'],
        unique=False,
        postgresql_using='gin',
        postgresql_ops={'fhir_resource': 'jsonb_path_ops'}
    )


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

