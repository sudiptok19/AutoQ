"""device-chain v2 readers and immutable offline associations

Revision ID: 0007_chain_versions
Revises: 0006_offline_authority

- device_events.chain_version: the version each event was hashed under (replay recomputes with exactly that).
- devices.chain_version_high: highest version this device has written; a lower version afterwards is a regression.
- Database-enforced immutability (application bugs cannot rewrite history):
    device_events      never updated or deleted
    offline_snapshots  identity (device, shipment, operator, key, token, issue/expiry) never changes; recovery and
                       settlement fields are write-once; nothing changes after settlement
    devices            the checkpoint only advances, and only to a (seq, hash) recorded in device_events (deferred
                       to commit, so the event and the checkpoint commit together)
"""
import sqlalchemy as sa
from alembic import op

revision = "0007_chain_versions"
down_revision = "0006_offline_authority"
branch_labels = None
depends_on = None

TRIGGERS = r"""
CREATE FUNCTION device_events_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'device_events are immutable (% on device % seq %)', TG_OP, OLD.device_id, OLD.seq USING ERRCODE = 'integrity_constraint_violation';
END $$;
CREATE TRIGGER device_events_immutable BEFORE UPDATE OR DELETE ON device_events FOR EACH ROW EXECUTE FUNCTION device_events_immutable();

CREATE FUNCTION offline_snapshots_write_once() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'offline snapshot % cannot be deleted', OLD.id USING ERRCODE = 'integrity_constraint_violation';
  END IF;
  IF (NEW.id, NEW.device_id, NEW.shipment_id, NEW.operator_id, NEW.kid, NEW.token, NEW.issued_at, NEW.expires_at)
     IS DISTINCT FROM (OLD.id, OLD.device_id, OLD.shipment_id, OLD.operator_id, OLD.kid, OLD.token, OLD.issued_at, OLD.expires_at) THEN
    RAISE EXCEPTION 'offline snapshot % identity is immutable', OLD.id USING ERRCODE = 'integrity_constraint_violation';
  END IF;
  IF OLD.settled_at IS NOT NULL AND ROW(NEW.*) IS DISTINCT FROM ROW(OLD.*) THEN
    RAISE EXCEPTION 'offline snapshot % is settled and cannot change', OLD.id USING ERRCODE = 'integrity_constraint_violation';
  END IF;
  IF (OLD.recovery_requested_at IS NOT NULL AND NEW.recovery_requested_at IS DISTINCT FROM OLD.recovery_requested_at)
     OR (OLD.recovery_task_id IS NOT NULL AND NEW.recovery_task_id IS DISTINCT FROM OLD.recovery_task_id) THEN
    RAISE EXCEPTION 'offline snapshot % recovery fields are write-once', OLD.id USING ERRCODE = 'integrity_constraint_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER offline_snapshots_write_once BEFORE UPDATE OR DELETE ON offline_snapshots FOR EACH ROW EXECUTE FUNCTION offline_snapshots_write_once();

CREATE FUNCTION devices_checkpoint_advances() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.operator_id IS DISTINCT FROM OLD.operator_id THEN
    RAISE EXCEPTION 'device % is bound to its operator', OLD.id USING ERRCODE = 'integrity_constraint_violation';
  END IF;
  IF NEW.last_seq < OLD.last_seq OR (NEW.last_seq = OLD.last_seq AND NEW.head_hash IS DISTINCT FROM OLD.head_hash)
     OR NEW.chain_version_high < OLD.chain_version_high THEN
    RAISE EXCEPTION 'device % checkpoint can only advance', OLD.id USING ERRCODE = 'integrity_constraint_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER devices_checkpoint_advances BEFORE UPDATE ON devices FOR EACH ROW EXECUTE FUNCTION devices_checkpoint_advances();

CREATE FUNCTION devices_checkpoint_recorded() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.last_seq > 0 AND NOT EXISTS (SELECT 1 FROM device_events e WHERE e.device_id = NEW.id AND e.seq = NEW.last_seq AND e.hash = NEW.head_hash) THEN
    RAISE EXCEPTION 'device % checkpoint (seq %) has no matching recorded event', NEW.id, NEW.last_seq USING ERRCODE = 'integrity_constraint_violation';
  END IF;
  RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER devices_checkpoint_recorded AFTER INSERT OR UPDATE ON devices DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW EXECUTE FUNCTION devices_checkpoint_recorded();
"""


def upgrade() -> None:
    op.add_column("device_events", sa.Column("chain_version", sa.SmallInteger(), server_default=sa.text("1"), nullable=False))
    op.add_column("devices", sa.Column("chain_version_high", sa.SmallInteger(), server_default=sa.text("1"), nullable=False))
    op.create_check_constraint("ck_device_events_chain_version", "device_events", "chain_version IN (1, 2)")
    op.execute(TRIGGERS)


def downgrade() -> None:
    op.execute("""
      DROP TRIGGER IF EXISTS devices_checkpoint_recorded ON devices; DROP FUNCTION IF EXISTS devices_checkpoint_recorded();
      DROP TRIGGER IF EXISTS devices_checkpoint_advances ON devices; DROP FUNCTION IF EXISTS devices_checkpoint_advances();
      DROP TRIGGER IF EXISTS offline_snapshots_write_once ON offline_snapshots; DROP FUNCTION IF EXISTS offline_snapshots_write_once();
      DROP TRIGGER IF EXISTS device_events_immutable ON device_events; DROP FUNCTION IF EXISTS device_events_immutable();
    """)
    op.drop_constraint("ck_device_events_chain_version", "device_events")
    op.drop_column("devices", "chain_version_high")
    op.drop_column("device_events", "chain_version")
