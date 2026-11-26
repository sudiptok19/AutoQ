"""SPC containment reviews: frozen investigation + recommendation, human decision, linked quarantines

Revision ID: 0008_spc_reviews
Revises: 0007_chain_versions
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0008_spc_reviews"
down_revision = "0007_chain_versions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "spc_reviews",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("alert_id", sa.String(40), sa.ForeignKey("alerts.id"), nullable=True),
        sa.Column("target_inspection_id", sa.String(40), sa.ForeignKey("inspections.id"), nullable=False),
        sa.Column("qualification_version", sa.String(40), nullable=False),
        sa.Column("recommendation_version", sa.String(40), nullable=False),
        sa.Column("investigation", JSONB(), nullable=False),        # Astra's Investigation, verbatim
        sa.Column("recommendation", JSONB(), nullable=False),       # Astra's ContainmentRecommendation, verbatim
        sa.Column("read_context", JSONB(), nullable=False),         # what Opus read: prefix size, genealogy, as-of
        sa.Column("status", sa.String(12), nullable=False, server_default=sa.text("'PROPOSED'")),
        sa.Column("approved_lot_ids", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("recall_ids", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),     # lot_id → recall_id
        sa.Column("created_by", sa.String(40), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("decided_by", sa.String(40), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.CheckConstraint("status IN ('PROPOSED', 'APPROVED', 'REJECTED')", name="ck_spc_reviews_status"),
    )
    op.create_index("ix_spc_reviews_target", "spc_reviews", ["target_inspection_id"])
    # The evidence a review stands on never changes after it is recorded.
    op.execute(r"""
    CREATE FUNCTION spc_reviews_frozen() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'spc review % cannot be deleted', OLD.id USING ERRCODE = 'integrity_constraint_violation'; END IF;
      IF (NEW.id, NEW.alert_id, NEW.target_inspection_id, NEW.qualification_version, NEW.recommendation_version, NEW.investigation,
          NEW.recommendation, NEW.read_context, NEW.created_by, NEW.created_at)
         IS DISTINCT FROM (OLD.id, OLD.alert_id, OLD.target_inspection_id, OLD.qualification_version, OLD.recommendation_version,
          OLD.investigation, OLD.recommendation, OLD.read_context, OLD.created_by, OLD.created_at) THEN
        RAISE EXCEPTION 'spc review % evidence is immutable', OLD.id USING ERRCODE = 'integrity_constraint_violation';
      END IF;
      IF OLD.status <> 'PROPOSED' AND (NEW.status, NEW.approved_lot_ids, NEW.decided_by, NEW.decided_at, NEW.decision_reason)
         IS DISTINCT FROM (OLD.status, OLD.approved_lot_ids, OLD.decided_by, OLD.decided_at, OLD.decision_reason) THEN
        RAISE EXCEPTION 'spc review % decision is final', OLD.id USING ERRCODE = 'integrity_constraint_violation';
      END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER spc_reviews_frozen BEFORE UPDATE OR DELETE ON spc_reviews FOR EACH ROW EXECUTE FUNCTION spc_reviews_frozen();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS spc_reviews_frozen ON spc_reviews; DROP FUNCTION IF EXISTS spc_reviews_frozen();")
    op.drop_table("spc_reviews")
