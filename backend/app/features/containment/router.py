from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import Principal, current_principal, require_roles
from app.core.errors import ApiError
from app.db import models as m
from app.db.session import get_db
from app.features.containment import service as s

router = APIRouter(tags=["containment"])


class FourMIn(BaseModel):
    category: Literal["MAN", "MACHINE", "MATERIAL", "METHOD"]
    planned: bool = False
    line_id: str
    description: str = Field(min_length=1, max_length=2000)
    reason: str = Field(min_length=1, max_length=2000)
    effective_at: datetime | None = None


class ClearIn(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)


@router.post("/four-m-events", status_code=201)
def log(body: FourMIn, db: Session = Depends(get_db), p: Principal = Depends(require_roles("quality_engineer", "quality_manager"))) -> dict:
    return s.log_event(db, p, body.category, body.planned, body.line_id, body.description, body.reason, body.effective_at)


@router.get("/four-m-events")
def events(db: Session = Depends(get_db), _: Principal = Depends(current_principal)) -> dict:
    return {"items": [s.event_view(db, e) for e in db.scalars(select(m.FourMEvent).order_by(m.FourMEvent.created_at.desc()))], "next_cursor": None}


@router.get("/four-m-events/{event_id}")
def event(event_id: str, db: Session = Depends(get_db), _: Principal = Depends(current_principal)) -> dict:
    e = db.get(m.FourMEvent, event_id)
    if not e:
        raise ApiError(404, "FOUR_M_EVENT_NOT_FOUND", "4M event not found")
    return s.event_view(db, e)


@router.get("/containments")
def runs(status: str | None = None, db: Session = Depends(get_db), _: Principal = Depends(current_principal)) -> dict:
    q = select(m.ContainmentRun).order_by(m.ContainmentRun.created_at.desc())
    if status:
        q = q.where(m.ContainmentRun.status == status)
    return {"items": [s.view(db, r) for r in db.scalars(q)], "next_cursor": None}


@router.get("/containments/{run_id}")
def run(run_id: str, db: Session = Depends(get_db), _: Principal = Depends(current_principal)) -> dict:
    r = db.get(m.ContainmentRun, run_id)
    if not r:
        raise ApiError(404, "CONTAINMENT_NOT_FOUND", "Containment run not found")
    return s.view(db, r)


@router.post("/containments/{run_id}/clear")
def clear(run_id: str, body: ClearIn, db: Session = Depends(get_db), p: Principal = Depends(require_roles("quality_manager"))) -> dict:
    return s.clear(db, p, run_id, body.reason)


# ── A3: SPC containment reviews (Astra's recommendation, Opus storage + human approval) ──
from typing import Literal  # noqa: E402

from app.features.containment import spc_review  # noqa: E402


class SpcReviewIn(BaseModel):
    inspection_id: str
    alert_id: str | None = None


class SpcDecisionIn(BaseModel):
    decision: Literal["APPROVE", "REJECT"]
    reason: str = Field(min_length=10, max_length=2000)
    lot_ids: list[str] = Field(default_factory=list, max_length=200)


class SpcQuarantineIn(BaseModel):
    lot_id: str


@router.post("/spc-reviews", status_code=201)
def create_spc_review(body: SpcReviewIn, db: Session = Depends(get_db), p: Principal = Depends(require_roles("quality_engineer", "quality_manager"))) -> dict:
    """Run Astra's investigation + recommendation for an inspection (usually a drift alert's) and freeze the result."""
    return spc_review.create(db, p, body.inspection_id, body.alert_id)


@router.get("/spc-reviews")
def list_spc_reviews(db: Session = Depends(get_db), _: Principal = Depends(current_principal)) -> dict:
    return {"items": spc_review.list_reviews(db)}


@router.get("/spc-reviews/{review_id}")
def get_spc_review(review_id: str, db: Session = Depends(get_db), _: Principal = Depends(current_principal)) -> dict:
    return spc_review.get(db, review_id)


@router.post("/spc-reviews/{review_id}/decision")
def decide_spc_review(review_id: str, body: SpcDecisionIn, db: Session = Depends(get_db), p: Principal = Depends(require_roles("quality_manager"))) -> dict:
    return spc_review.decide(db, p, review_id, body.decision, body.reason, body.lot_ids)


@router.post("/spc-reviews/{review_id}/quarantine")
def quarantine_spc_lot(review_id: str, body: SpcQuarantineIn, db: Session = Depends(get_db), p: Principal = Depends(require_roles("quality_manager"))) -> dict:
    return spc_review.quarantine(db, p, review_id, body.lot_id)
