"""offline authority: settlement binds the chain head; lost-device recovery needs physical reconciliation

Revision ID: 0006_offline_authority
Revises: 0005_ws_ticket_session

- offline_snapshots.settled_head: settlement binds the device's terminal sequence AND hash.
- offline_snapshots.recovery_*: recovering a lost device first REQUESTS recovery (authority ends, a DEVICE_RECONCILIATION
  task is raised); the snapshot is settled only by linked evidence or the completed reconciliation task.
- device_events.claim_recorded: true when claimed_snapshot_id is the device's actual claim (possibly a known null);
  false for rows from before this column, whose claim metadata is unavailable.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0006_offline_authority"
down_revision = "0005_ws_ticket_session"
branch_labels = None
depends_on = None

OLD_TYPES = "('REMOVAL_REQUIRED', 'SEGREGATION_REQUIRED', 'IN_TRANSIT_RECALL', 'NOTIFY_CUSTOMER')"
NEW_TYPES = "('REMOVAL_REQUIRED', 'SEGREGATION_REQUIRED', 'IN_TRANSIT_RECALL', 'NOTIFY_CUSTOMER', 'DEVICE_RECONCILIATION')"


def upgrade() -> None:
    op.add_column("offline_snapshots", sa.Column("settled_head", sa.String(64), nullable=True))
    op.add_column("offline_snapshots", sa.Column("recovery_requested_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("offline_snapshots", sa.Column("recovery_task_id", sa.String(40), sa.ForeignKey("segregation_tasks.id"), nullable=True))
    op.add_column("offline_snapshots", sa.Column("recovery_evidence", JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False))
    op.add_column("device_events", sa.Column("claim_recorded", sa.Boolean(), server_default=sa.text("false"), nullable=False))
    op.drop_constraint("ck_type", "segregation_tasks")
    op.create_check_constraint("ck_type", "segregation_tasks", f"type IN {NEW_TYPES}")


def downgrade() -> None:
    op.execute("DELETE FROM segregation_tasks WHERE type = 'DEVICE_RECONCILIATION' AND id NOT IN (SELECT recovery_task_id FROM offline_snapshots WHERE recovery_task_id IS NOT NULL)")
    op.execute("UPDATE offline_snapshots SET recovery_task_id = NULL")
    op.execute("DELETE FROM segregation_tasks WHERE type = 'DEVICE_RECONCILIATION'")
    op.drop_constraint("ck_type", "segregation_tasks")
    op.create_check_constraint("ck_type", "segregation_tasks", f"type IN {OLD_TYPES}")
    op.drop_column("device_events", "claim_recorded")
    for c in ("recovery_evidence", "recovery_task_id", "recovery_requested_at", "settled_head"):
        op.drop_column("offline_snapshots", c)
