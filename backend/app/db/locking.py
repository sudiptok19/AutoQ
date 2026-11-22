"""Global lock hierarchy (CONTRACT §10.4): heat → lot → unit → package → order → shipment,
ascending id inside each class. Every transactional service acquires row locks ONLY through
`lock_set`, so two transactions can never wait on each other in opposite orders."""
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import models as m

LOCK_ORDER: list[type[m.Base]] = [m.Heat, m.Lot, m.Unit, m.Package, m.Order, m.Shipment]
_RANK = {cls: i for i, cls in enumerate(LOCK_ORDER)}


class LockOrderViolation(RuntimeError):
    pass


class LockSet:
    """Tracks what this transaction has locked and refuses out-of-order acquisition."""

    def __init__(self, db: Session):
        self.db = db
        self.highest = -1
        self.locked: dict[type, set[str]] = {}

    def lock(self, cls: type[m.Base], ids: Iterable[str]) -> list:
        if self.db.info.get("audit_chain_locked"):
            raise LockOrderViolation("business rows must be locked before appending audit/outbox events")
        rank = _RANK[cls]
        if rank < self.highest:
            raise LockOrderViolation(f"cannot lock {cls.__tablename__} after {LOCK_ORDER[self.highest].__tablename__}")
        want = sorted(set(ids) - self.locked.get(cls, set()))
        self.highest = max(self.highest, rank)
        if not want:
            return []
        rows = self.db.scalars(select(cls).where(cls.id.in_(want)).order_by(cls.id).with_for_update()).all()
        self.locked.setdefault(cls, set()).update(r.id for r in rows)
        return list(rows)


def lock_set(db: Session) -> LockSet:
    return LockSet(db)
