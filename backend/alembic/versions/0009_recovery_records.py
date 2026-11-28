"""Recovery reconciliation records: evidence bound to one pending recovery, consumed once

Revision ID: 0009_recovery_records
Revises: 0008_spc_reviews
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0009_recovery_records"
down_revision = "0008_spc_reviews"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # One row per reconciliation record. The digest is the primary key, so a record settles at most one recovery,
    # whatever shipment another transaction has locked; one snapshot settles from at most one record.
    op.create_table(
        "recovery_records",
        sa.Column("sha256", sa.String(64), sa.ForeignKey("evidence.sha256"), primary_key=True),
        sa.Column("snapshot_id", sa.String(40), sa.ForeignKey("offline_snapshots.id"), nullable=False, unique=True),
        sa.Column("task_id", sa.String(40), sa.ForeignKey("segregation_tasks.id"), nullable=False),
        sa.Column("shipment_id", sa.String(40), sa.ForeignKey("shipments.id"), nullable=False),
        sa.Column("attested_by", sa.String(40), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("disposition", sa.String(24), nullable=False),
        sa.Column("counted_scope", JSONB(), nullable=False),        # what the person counted: locations, packages, note
        sa.Column("manifest", JSONB(), nullable=False),             # the server's manifest under the shipment lock
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("disposition IN ('MATCHES_MANIFEST', 'DISCREPANCY_SEGREGATED')", name="ck_recovery_records_disposition"),
    )


def downgrade() -> None:
    op.drop_table("recovery_records")
