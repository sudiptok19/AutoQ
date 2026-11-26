"""per-snapshot settlement replaces the device-wide check-in

Revision ID: 0004_snapshot_settlement
Revises: 0003_device_checkin

A snapshot is an authorization for one device to capture offline for one shipment. It stays OUTSTANDING (and blocks
manifest close and departure) until it is settled: acknowledged by the device with an empty queue, superseded by a
refresh with an empty queue, or recovered by a Quality Manager for a lost device. Expiry settles nothing.

Existing data: snapshots of shipments that are no longer OPEN were already past manifest close under the old rules;
they are marked LEGACY (with a reason) so they don't show as open authorizations forever. Snapshots of OPEN shipments
are deliberately left OUTSTANDING: nothing proves their captures were reconciled, so each must be ended on the device
or recovered by a Quality Manager before that manifest can close.
"""
import sqlalchemy as sa
from alembic import op

revision = "0004_snapshot_settlement"
down_revision = "0003_device_checkin"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("offline_snapshots", sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("offline_snapshots", sa.Column("settlement", sa.String(12), nullable=True))
    op.add_column("offline_snapshots", sa.Column("settled_by", sa.String(40), sa.ForeignKey("users.id"), nullable=True))
    op.add_column("offline_snapshots", sa.Column("settled_seq", sa.Integer(), nullable=True))
    op.add_column("offline_snapshots", sa.Column("settlement_reason", sa.Text(), nullable=True))
    op.create_check_constraint("ck_offline_snapshots_settlement", "offline_snapshots",
                               "settlement IS NULL OR settlement IN ('ACKNOWLEDGED', 'SUPERSEDED', 'RECOVERED', 'LEGACY')")
    op.execute("""UPDATE offline_snapshots sn SET settled_at = now(), settlement = 'LEGACY',
                    settlement_reason = 'Issued before per-snapshot settlement (0004); its shipment was already ' || s.status || ' at upgrade'
                  FROM shipments s WHERE s.id = sn.shipment_id AND s.status <> 'OPEN'""")
    op.create_index("ix_offline_snapshots_open", "offline_snapshots", ["shipment_id"], postgresql_where=sa.text("settled_at IS NULL"))
    # The snapshot id the device CLAIMED for an event, verbatim (snapshot_id keeps only a validated association).
    op.add_column("device_events", sa.Column("claimed_snapshot_id", sa.String(40), nullable=True))
    op.execute("UPDATE device_events SET claimed_snapshot_id = snapshot_id")
    op.drop_column("devices", "checked_in_at")


def downgrade() -> None:
    op.add_column("devices", sa.Column("checked_in_at", sa.DateTime(timezone=True), nullable=True))
    op.drop_column("device_events", "claimed_snapshot_id")
    op.drop_index("ix_offline_snapshots_open", table_name="offline_snapshots")
    op.drop_constraint("ck_offline_snapshots_settlement", "offline_snapshots")
    for c in ("settlement_reason", "settled_seq", "settled_by", "settlement", "settled_at"):
        op.drop_column("offline_snapshots", c)
