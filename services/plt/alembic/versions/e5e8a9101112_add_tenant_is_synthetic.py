"""add tenant is_synthetic column

Revision ID: e5e8a9101112
Revises: ff72b1b00476
Create Date: 2026-07-31 15:19:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'e5e8a9101112'
down_revision = 'ff72b1b00476'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE tenant ADD COLUMN IF NOT EXISTS is_synthetic BOOLEAN NOT NULL DEFAULT FALSE;")


def downgrade():
    op.execute("ALTER TABLE tenant DROP COLUMN IF EXISTS is_synthetic;")
