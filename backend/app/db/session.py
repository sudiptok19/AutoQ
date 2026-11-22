from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings


@lru_cache
def engine() -> Engine:
    return create_engine(settings().database_url, pool_pre_ping=True, pool_size=10, max_overflow=10)


@lru_cache
def session_factory() -> sessionmaker[Session]:
    return sessionmaker(engine(), expire_on_commit=False, autoflush=False)


def get_db() -> Iterator[Session]:
    """One request = one transaction. Commit on success, roll back on any exception (fail closed)."""
    db = session_factory()()
    try:
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


@event.listens_for(Session, "after_transaction_end")
def _reset_chain_flag(session: Session, transaction) -> None:
    if transaction.parent is None:
        session.info.pop("audit_chain_locked", None)
