"""Versioned, explainable policy. Every consequential row records the version it was decided under.
Numbers here are DEMONSTRATION defaults (provisional, not OEM policy) until calibrated on held-out data."""
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.core.ids import new_id
from app.db import models as m


class Sampling(BaseModel):
    sample_size: int = Field(5, ge=1, le=500)


class Inspection(BaseModel):
    hold_threshold: float = Field(0.45, gt=0, lt=1)
    reject_threshold: float = Field(0.70, gt=0, le=1)
    blur_min: float = Field(80, gt=0)
    glare_max: float = Field(0.12, gt=0, lt=1)
    min_w: int = Field(320, ge=1)
    min_h: int = Field(240, ge=1)

    @model_validator(mode="after")
    def _order(self):
        if self.hold_threshold >= self.reject_threshold:
            raise ValueError("hold_threshold must be below reject_threshold")
        return self


class Containment(BaseModel):
    first_n: int = Field(5, ge=1, le=100)
    lookback_minutes: int = Field(30, ge=0, le=24 * 60)
    recheck_existing_unplanned: bool = True
    recheck_existing_planned: bool = False
    threshold_tightening: float = Field(0.0, ge=0, lt=1)   # only applied with a calibrated profile


class PolicySettings(BaseModel):
    sampling: Sampling = Sampling()
    inspection: Inspection = Inspection()
    containment: Containment = Containment()
    seal_recheck_max_age_minutes: int = Field(60, ge=1, le=24 * 60)
    snapshot_ttl_minutes: int = Field(240, ge=5, le=24 * 60)
    allow_partial_close: bool = False
    retention_years_provisional: int = Field(11, ge=1, le=50)


def active(db: Session) -> m.Policy:
    p = db.scalars(select(m.Policy).where(m.Policy.active.is_(True))).first()
    if not p:
        raise ApiError(503, "POLICY_MISSING", "No active policy version", {"component": "policy", "remediation": "Run scripts/seed_dev_users.py or PUT /policies."})
    return p


def settings_of(p: m.Policy) -> PolicySettings:
    return PolicySettings.model_validate(p.settings)


def view(p: m.Policy) -> dict:
    return {"version": p.version, "active": p.active, "demo_default": p.demo_default, "settings": p.settings, "created_at": p.created_at.isoformat() if p.created_at else None}


def publish(db: Session, settings: PolicySettings, actor_id: str | None, *, version: str | None = None, demo_default: bool = False) -> m.Policy:
    """Activate a new immutable version; older versions stay for provenance."""
    n = len(db.scalars(select(m.Policy.id)).all()) + 1
    ver = version or f"p{n}"
    if db.scalars(select(m.Policy).where(m.Policy.version == ver)).first():
        raise ApiError(409, "POLICY_VERSION_EXISTS", f"Policy version {ver} already exists")
    db.execute(update(m.Policy).where(m.Policy.active.is_(True)).values(active=False))
    db.flush()
    p = m.Policy(id=new_id("pol"), version=ver, settings=settings.model_dump(), active=True, demo_default=demo_default, created_by=actor_id)
    db.add(p)
    db.flush()
    return p
