"""ws tickets remember when the session that minted them expires

Revision ID: 0005_ws_ticket_session
Revises: 0004_snapshot_settlement

A WebSocket opened with a ticket may not outlive the bearer session it was minted from; the connection closes then
and the client must re-authenticate to reconnect.
"""
import sqlalchemy as sa
from alembic import op

revision = "0005_ws_ticket_session"
down_revision = "0004_snapshot_settlement"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ws_tickets", sa.Column("session_expires_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("ws_tickets", "session_expires_at")
