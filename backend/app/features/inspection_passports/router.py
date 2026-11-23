from typing import Literal

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.auth import Principal, current_principal, require_roles
from app.core.errors import ApiError
from app.db import models as m
from app.db.session import get_db
from app.features.catalog_sampling.service import find_unit, unit_view
from app.features.inspection_passports import passports, service

router = APIRouter(tags=["inspection", "passports"])
MAX_IMAGE = 15 * 1024 * 1024


@router.post("/inspections", status_code=201)
async def inspect(unit_id: str = Form(min_length=1), station_id: str | None = Form(None), evidence_source: str = Form("LIVE_CAPTURE"),
                  image: UploadFile = File(), db: Session = Depends(get_db),
                  p: Principal = Depends(require_roles("operator", "quality_engineer", "quality_manager"))) -> dict:
    data = await image.read(MAX_IMAGE + 1)
    if len(data) > MAX_IMAGE:
        raise ApiError(413, "EVIDENCE_TOO_LARGE", "Image larger than 15 MB")
    # Blocking DB/CV work must never run on the event loop (a lock wait there stalls every request).
    return await run_in_threadpool(service.inspect, db, p, unit_id, station_id, data, evidence_source)


@router.get("/inspections")
def list_inspections(unit_id: str | None = None, lot_id: str | None = None, limit: int = Query(100, ge=1, le=500),
                     db: Session = Depends(get_db), _: Principal = Depends(current_principal)) -> dict:
    return {"items": service.list_inspections(db, unit_id, lot_id, limit), "next_cursor": None}


class ReviewIn(BaseModel):
    action: Literal["REINSPECT", "REJECT"]
    reason: str = Field(min_length=1, max_length=2000)


@router.post("/inspections/{inspection_id}/review")
def review(inspection_id: str, body: ReviewIn, db: Session = Depends(get_db), p: Principal = Depends(require_roles("quality_engineer", "quality_manager"))) -> dict:
    return service.review(db, p, inspection_id, body.action, body.reason)


class IssueIn(BaseModel):
    unit_id: str


@router.post("/passports/issue")
def issue(body: IssueIn, db: Session = Depends(get_db), p: Principal = Depends(require_roles("operator", "quality_engineer", "quality_manager"))) -> dict:
    return service.issue_explicit(db, p, body.unit_id)


@router.get("/passports/{passport_id}")
def get_passport(passport_id: str, db: Session = Depends(get_db), _: Principal = Depends(current_principal)) -> dict:
    pp = db.get(m.Passport, passport_id)
    if not pp:
        raise ApiError(404, "PASSPORT_NOT_FOUND", "Passport not found")
    return passports.view(db, pp)


class VerifyIn(BaseModel):
    token: str = Field(min_length=1, max_length=8192)


@router.post("/verify")
def verify(body: VerifyIn, db: Session = Depends(get_db), _: Principal = Depends(current_principal)) -> dict:
    return passports.verify_token(db, body.token)


@router.get("/units/{key}/detail")
def unit_detail(key: str, db: Session = Depends(get_db), _: Principal = Depends(current_principal)) -> dict:
    u = find_unit(db, key)
    return unit_view(db, u) | service.unit_detail(db, u)
