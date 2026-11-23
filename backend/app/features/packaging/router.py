from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.core.auth import Principal, current_principal, require_roles
from app.core.errors import ApiError
from app.db import models as m
from app.db.session import get_db
from app.features.packaging import service as s
from app.ports import Ports, ports

router = APIRouter(tags=["orders", "packaging"])
interim_seal_router = APIRouter(tags=["seal-vision (interim)"])
MAX_IMAGE = 15 * 1024 * 1024
Any_ = Depends(current_principal)


async def _read(f: UploadFile) -> bytes:
    data = await f.read(MAX_IMAGE + 1)
    if len(data) > MAX_IMAGE:
        raise ApiError(413, "EVIDENCE_TOO_LARGE", "Image larger than 15 MB")
    return data


class OrderIn(BaseModel):
    order_no: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9._\-]+$")
    customer_id: str
    part_id: str
    qty_units: int = Field(ge=1, le=100_000)
    destination: str = Field(min_length=1, max_length=200)


@router.get("/orders")
def orders(db: Session = Depends(get_db), _: Principal = Any_) -> dict:
    return {"items": [s.order_view(db, o) for o in db.scalars(select(m.Order).order_by(m.Order.created_at.desc()))], "next_cursor": None}


@router.get("/orders/{order_id}")
def order(order_id: str, db: Session = Depends(get_db), _: Principal = Any_) -> dict:
    o = db.get(m.Order, order_id) or db.scalars(select(m.Order).where(m.Order.order_no == order_id)).first()
    if not o:
        raise ApiError(404, "ORDER_NOT_FOUND", "Order not found")
    return s.order_view(db, o)


@router.post("/orders", status_code=201)
def add_order(body: OrderIn, db: Session = Depends(get_db), p: Principal = Depends(require_roles("admin", "dispatcher"))) -> dict:
    return s.create_order(db, p, **body.model_dump())


class PackageIn(BaseModel):
    order_id: str


class ItemIn(BaseModel):
    unit_id: str | None = None
    token: str | None = Field(None, max_length=8192)


class ReasonIn(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)


Packer = Depends(require_roles("operator"))


@router.get("/packages")
def packages(order_id: str | None = None, state: str | None = None, db: Session = Depends(get_db), _: Principal = Any_) -> dict:
    q = select(m.Package).order_by(m.Package.label)
    if order_id:
        q = q.where(m.Package.order_id == order_id)
    if state:
        q = q.where(m.Package.state == state)
    return {"items": [s.package_view(db, x) for x in db.scalars(q)], "next_cursor": None}


@router.get("/packages/{key}")
def package(key: str, db: Session = Depends(get_db), _: Principal = Any_) -> dict:
    return s.package_view(db, s.find_package(db, key))


@router.post("/packages", status_code=201)
def add_package(body: PackageIn, db: Session = Depends(get_db), p: Principal = Packer) -> dict:
    return s.create_package(db, p, body.order_id)


@router.post("/packages/{key}/items")
def add_item(key: str, body: ItemIn, db: Session = Depends(get_db), p: Principal = Packer) -> dict:
    if not body.unit_id and not body.token:
        raise ApiError(422, "VALIDATION", "unit_id or token is required")
    return s.add_item(db, p, key, body.unit_id, body.token)


@router.delete("/packages/{key}/items/{unit_id}")
def remove_item(key: str, unit_id: str, reason: str | None = Query(None, max_length=2000), db: Session = Depends(get_db), p: Principal = Packer) -> dict:
    return s.remove_item(db, p, key, unit_id, reason)


@router.post("/packages/{key}/seal")
async def seal(key: str, image: UploadFile = File(), evidence_source: str = Form("LIVE_CAPTURE"), db: Session = Depends(get_db), p: Principal = Packer) -> dict:
    return await run_in_threadpool(s.seal, db, p, key, await _read(image), evidence_source)


@router.post("/packages/{key}/unpack")
def unpack(key: str, body: ReasonIn, db: Session = Depends(get_db), p: Principal = Depends(require_roles("operator", "quality_engineer", "quality_manager"))) -> dict:
    return s.unpack(db, p, key, body.reason)


@router.post("/packages/{key}/re-enroll")
async def re_enroll(key: str, image: UploadFile = File(), reason: str = Form(min_length=1, max_length=2000), evidence_source: str = Form("LIVE_CAPTURE"),
                    db: Session = Depends(get_db), p: Principal = Depends(require_roles("quality_engineer", "quality_manager"))) -> dict:
    return await run_in_threadpool(s.re_enroll, db, p, key, await _read(image), reason, evidence_source)


@interim_seal_router.post("/packages/{package_id}/seal-checks")
async def seal_check(package_id: str, image: UploadFile = File(), evidence_source: str = Form("LIVE_CAPTURE"), db: Session = Depends(get_db),
                     p: Principal = Depends(require_roles("dispatcher", "quality_engineer", "quality_manager")), P: Ports = Depends(ports)) -> dict:
    """INTERIM route with the same contract as Astra's A2 router; main.py mounts A2's instead once it exists."""
    from app.features.packaging import interim_seal
    probe = await _read(image)

    def run():
        ctx = P.load_seal_context(db, package_id, p)
        ev = interim_seal.compare(ctx.reference_bytes, probe, ctx.profile)
        return P.record_recheck(db, ctx, ev, probe, image.content_type or "image/jpeg", evidence_source, p)
    return await run_in_threadpool(run)
