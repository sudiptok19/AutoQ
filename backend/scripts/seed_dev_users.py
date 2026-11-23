"""DEV ONLY: create the five documented development accounts (password `autoq-dev`), register
the signing key's public half, and publish the DEMONSTRATION default policy `demo-1` if none is active. Run explicitly; never executed on server start."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy.dialects.postgresql import insert  # noqa: E402

from app.core.auth import hash_password  # noqa: E402
from app.core.signing import register_active_key  # noqa: E402
from app.policy import policies  # noqa: E402
from app.db import models as m  # noqa: E402
from app.db.session import session_factory  # noqa: E402

DEV_USERS = [
    ("usr_admin", "admin", "Admin (dev)", ["admin"]),
    ("usr_ravi", "op.ravi", "Ravi Kumar", ["operator"]),
    ("usr_ananya", "qe.ananya", "Ananya Sen", ["quality_engineer"]),
    ("usr_farhan", "qm.farhan", "Farhan Ali", ["quality_manager", "quality_engineer"]),
    ("usr_kiran", "disp.kiran", "Kiran Das", ["dispatcher"]),
]


def seed(db) -> None:
    pw = hash_password("autoq-dev")
    for uid, username, name, roles in DEV_USERS:
        db.execute(insert(m.User).values(id=uid, username=username, display_name=name, password_hash=pw).on_conflict_do_nothing(index_elements=["username"]))
        for r in roles:
            db.execute(insert(m.UserRole).values(user_id=uid, role=r).on_conflict_do_nothing())
    register_active_key(db)
    if not db.query(m.Policy).filter(m.Policy.active.is_(True)).first():
        policies.publish(db, policies.PolicySettings(), None, version="demo-1", demo_default=True)


if __name__ == "__main__":
    with session_factory()() as db:
        seed(db)
        db.commit()
    print("seeded dev users: " + ", ".join(u[1] for u in DEV_USERS))
