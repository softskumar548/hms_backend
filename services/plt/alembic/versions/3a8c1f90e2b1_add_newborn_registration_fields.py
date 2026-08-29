"""add_newborn_registration_fields

Revision ID: 3a8c1f90e2b1
Revises: 19ee4fa187ab
Create Date: 2026-08-29 21:42:00.000000

Story / FRD: REG-010, FHIR R4 Neonate Standard, ABDM Child Identity
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = '3a8c1f90e2b1'
down_revision = '19ee4fa187ab'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add newborn columns to patient table
    op.add_column('patient', sa.Column('is_newborn', sa.Boolean(), nullable=False, server_default=sa.text('false')))
    op.add_column('patient', sa.Column('mother_patient_id', UUID(as_uuid=True), nullable=True))
    op.add_column('patient', sa.Column('birth_time', sa.Text(), nullable=True))
    op.add_column('patient', sa.Column('birth_weight_grams', sa.Integer(), nullable=True))
    op.add_column('patient', sa.Column('gestational_age_weeks', sa.Integer(), nullable=True))
    op.add_column('patient', sa.Column('multiple_birth_order', sa.Integer(), nullable=False, server_default=sa.text('1')))
    op.add_column('patient', sa.Column('delivery_type', sa.Text(), nullable=True))
    op.add_column('patient', sa.Column('apgar_score_1min', sa.Integer(), nullable=True))
    op.add_column('patient', sa.Column('apgar_score_5min', sa.Integer(), nullable=True))

    # Foreign key link to mother
    op.create_foreign_key(
        'fk_patient_mother_patient_id',
        'patient', 'patient',
        ['mother_patient_id'], ['id'],
        ondelete='SET NULL'
    )

    # Index for child lookup by mother within tenant
    op.create_index('ix_patient_mother_patient_id', 'patient', ['tenant_id', 'mother_patient_id'], unique=False)
    op.create_index('ix_patient_is_newborn', 'patient', ['tenant_id', 'is_newborn'], unique=False)


def downgrade() -> None:
    # Drop indexes and constraints
    op.drop_index('ix_patient_is_newborn', table_name='patient')
    op.drop_index('ix_patient_mother_patient_id', table_name='patient')
    op.drop_constraint('fk_patient_mother_patient_id', 'patient', type_='foreignkey')

    # Drop columns
    op.drop_column('patient', 'apgar_score_5min')
    op.drop_column('patient', 'apgar_score_1min')
    op.drop_column('patient', 'delivery_type')
    op.drop_column('patient', 'multiple_birth_order')
    op.drop_column('patient', 'gestational_age_weeks')
    op.drop_column('patient', 'birth_weight_grams')
    op.drop_column('patient', 'birth_time')
    op.drop_column('patient', 'mother_patient_id')
    op.drop_column('patient', 'is_newborn')
