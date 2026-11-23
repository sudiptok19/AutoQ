"""package label sequence

Revision ID: 0002_package_label_seq
Revises: 0001_initial
"""
from alembic import op

revision = "0002_package_label_seq"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SEQUENCE IF NOT EXISTS package_label_seq START 1")
    op.execute("CREATE SEQUENCE IF NOT EXISTS shipment_code_seq START 1")


def downgrade() -> None:
    op.execute("DROP SEQUENCE IF EXISTS shipment_code_seq")
    op.execute("DROP SEQUENCE IF EXISTS package_label_seq")
