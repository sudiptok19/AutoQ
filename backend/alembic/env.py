from alembic import context
from sqlalchemy import create_engine

from app.core.config import settings
from app.db.models import Base

config = context.config
url = config.attributes.get("database_url") or settings().database_url


def run() -> None:
    connectable = config.attributes.get("connection") or create_engine(url)
    if hasattr(connectable, "connect") and not hasattr(connectable, "dialect_options"):
        with connectable.connect() as conn:
            _migrate(conn)
    else:
        _migrate(connectable)


def _migrate(conn) -> None:
    context.configure(connection=conn, target_metadata=Base.metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


run()
