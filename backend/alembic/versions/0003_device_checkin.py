"""device check-in time (offline reconciliation before manifest close)

Revision ID: 0003_device_checkin
Revises: 0002_package_label_seq
"""
import sqlalchemy as sa
from alembic import op

revision = "0003_device_checkin"
down_revision = "0002_package_label_seq"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("devices", sa.Column("checked_in_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("devices", "checked_in_at")
