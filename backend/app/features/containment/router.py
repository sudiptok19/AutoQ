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
