from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import Principal, current_principal, require_roles
from app.db import models as m
from app.db.session import get_db
from app.features.report_workflow import service as s

router = APIRouter(tags=["reports"])


class Source(BaseModel):
    type: Literal["RECALL", "FOUR_M", "ALERT"]
    id: str = Field(min_length=1, max_length=80)


class CreateIn(BaseModel):
    source: Source


class ApproveIn(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)
    review_evidence_ids: list[str] = Field(default_factory=list, max_length=50)


@router.post("/reports/8d", status_code=201)
def create(body: CreateIn, db: Session = Depends(get_db), p: Principal = Depends(require_roles("quality_engineer", "quality_manager"))) -> dict:
    return s.create(db, p, body.source.type, body.source.id)


@router.get("/reports")
def reports(db: Session = Depends(get_db), _: Principal = Depends(current_principal)) -> dict:
    return {"items": [s.view(db, r) for r in db.scalars(select(m.Report).order_by(m.Report.created_at.desc()))], "next_cursor": None}


@router.get("/reports/{report_id}")
def report(report_id: str, db: Session = Depends(get_db), _: Principal = Depends(current_principal)) -> dict:
    return s.view(db, s.find(db, report_id))


@router.post("/reports/{report_id}/approve")
def approve(report_id: str, body: ApproveIn, db: Session = Depends(get_db), p: Principal = Depends(require_roles("quality_manager"))) -> dict:
    return s.approve(db, p, report_id, body.reason, body.review_evidence_ids)
