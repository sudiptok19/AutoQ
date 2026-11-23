from fastapi import APIRouter, Depends, File, Form, UploadFile
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import audit
from app.core.auth import Principal, current_principal, require_roles
from app.core.errors import ApiError
from app.core.storage import sniff
from app.db import models as m
from app.db.session import get_db
from app.features.catalog_sampling import service as s
from app.policy import policies

router = APIRouter(tags=["catalog", "lots"])
Admin = Depends(require_roles("admin"))
Any_ = Depends(current_principal)
Code = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9._\-]+$")


def page(items: list) -> dict:
    return {"items": items, "next_cursor": None}


class SupplierIn(BaseModel):
    code: str = Code
    name: str = Field(min_length=1, max_length=200)
    synthetic: bool = False


class PartIn(BaseModel):
    part_number: str = Code
    name: str = Field(min_length=1, max_length=200)
    drawing_rev: str = Field(min_length=1, max_length=16)
    is_a_part: bool = False
    default_sample_size: int = Field(5, ge=1, le=500)
    inspection_profile_id: str | None = None


class LineIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    station_id: str = Code


class CustomerIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    city: str = Field(min_length=1, max_length=80)


class LotIn(BaseModel):
    lot_code: str = Code
    part_id: str
    line_id: str
    heat_ids: list[str] = Field(min_length=1)
    planned_qty: int = Field(ge=1, le=100_000)


class UnitsIn(BaseModel):
    count: int = Field(ge=1, le=s.MAX_UNITS_PER_CALL)


@router.get("/suppliers")
def suppliers(db: Session = Depends(get_db), _: Principal = Any_):
    return page([s.supplier_view(x) for x in db.scalars(select(m.Supplier).order_by(m.Supplier.code))])


@router.post("/suppliers", status_code=201)
def add_supplier(body: SupplierIn, db: Session = Depends(get_db), p: Principal = Admin):
    return s.create_supplier(db, p, body.code, body.name, body.synthetic)


@router.get("/parts")
def parts(db: Session = Depends(get_db), _: Principal = Any_):
    return page([s.part_view(x) for x in db.scalars(select(m.Part).order_by(m.Part.part_number))])


@router.post("/parts", status_code=201)
def add_part(body: PartIn, db: Session = Depends(get_db), p: Principal = Admin):
    return s.create_part(db, p, **body.model_dump())


@router.get("/lines")
def lines(db: Session = Depends(get_db), _: Principal = Any_):
    return page([s.line_view(x) for x in db.scalars(select(m.Line).order_by(m.Line.name))])


@router.post("/lines", status_code=201)
def add_line(body: LineIn, db: Session = Depends(get_db), p: Principal = Admin):
    return s.create_line(db, p, body.name, body.station_id)


@router.get("/customers")
def customers(db: Session = Depends(get_db), _: Principal = Any_):
    return page([s.customer_view(x) for x in db.scalars(select(m.Customer).order_by(m.Customer.name))])


@router.post("/customers", status_code=201)
def add_customer(body: CustomerIn, db: Session = Depends(get_db), p: Principal = Admin):
    return s.create_customer(db, p, body.name, body.city)


@router.get("/heats")
def heats(db: Session = Depends(get_db), _: Principal = Any_):
    return page([s.heat_view(x) for x in db.scalars(select(m.Heat).order_by(m.Heat.heat_code))])


@router.post("/heats", status_code=201)
async def add_heat(heat_code: str = Form(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9._\-]+$"), supplier_id: str = Form(), material_grade: str = Form(min_length=1, max_length=40),
                   certificate: UploadFile = File(), db: Session = Depends(get_db), p: Principal = Depends(require_roles("admin", "quality_engineer", "quality_manager"))):
    data = await certificate.read()
    return await run_in_threadpool(s.create_heat, db, p, heat_code, supplier_id, material_grade, data, certificate.filename or "certificate", sniff(data) or (certificate.content_type or ""))


@router.get("/lots")
def lots(db: Session = Depends(get_db), _: Principal = Any_):
    return page([s.lot_view(db, x) for x in db.scalars(select(m.Lot).order_by(m.Lot.created_at.desc()))])


@router.get("/lots/{lot_id}")
def lot(lot_id: str, db: Session = Depends(get_db), _: Principal = Any_):
    return s.lot_view(db, s._get(db, m.Lot, lot_id, "lot"))


@router.post("/lots", status_code=201)
def add_lot(body: LotIn, db: Session = Depends(get_db), p: Principal = Depends(require_roles("admin", "operator", "quality_engineer", "quality_manager"))):
    return s.create_lot(db, p, body.lot_code, body.part_id, body.line_id, body.heat_ids, body.planned_qty)


@router.post("/lots/{lot_id}/units", status_code=201)
def add_units(lot_id: str, body: UnitsIn, db: Session = Depends(get_db), p: Principal = Depends(require_roles("operator", "admin"))):
    return page(s.serialize_units(db, p, lot_id, body.count))


@router.post("/lots/{lot_id}/freeze-and-sample")
def freeze(lot_id: str, db: Session = Depends(get_db), p: Principal = Depends(require_roles("quality_engineer", "quality_manager"))):
    return s.freeze_and_sample(db, p, lot_id)


@router.get("/units")
def units(lot_id: str | None = None, db: Session = Depends(get_db), _: Principal = Any_):
    q = select(m.Unit).order_by(m.Unit.lot_id, m.Unit.production_seq)
    if lot_id:
        q = q.where(m.Unit.lot_id == lot_id)
    return page([s.unit_view(db, u) for u in db.scalars(q.limit(2000))])


@router.get("/units/{key}")
def unit(key: str, db: Session = Depends(get_db), _: Principal = Any_):
    return s.unit_view(db, s.find_unit(db, key), detail=True)


@router.get("/policies")
def get_policy(db: Session = Depends(get_db), _: Principal = Any_):
    return policies.view(policies.active(db))


class PolicyIn(policies.PolicySettings):
    version: str | None = Field(None, pattern=r"^[A-Za-z0-9._\-]{1,32}$")


@router.put("/policies")
def put_policy(body: PolicyIn, db: Session = Depends(get_db), p: Principal = Admin):
    data = body.model_dump(exclude={"version"})
    pol = policies.publish(db, policies.PolicySettings.model_validate(data), p.id, version=body.version)
    audit.append(db, p.username, "POLICY_PUBLISHED", "policy", pol.id, data={"version": pol.version})
    return policies.view(pol)


class ProfileIn(BaseModel):
    part_id: str
    station_id: str


@router.post("/inspection-profiles", status_code=201)
def add_profile(body: ProfileIn, db: Session = Depends(get_db), p: Principal = Depends(require_roles("quality_engineer", "quality_manager"))):
    return s.create_profile(db, p, body.part_id, body.station_id)


@router.get("/inspection-profiles/{profile_id}")
def get_profile(profile_id: str, db: Session = Depends(get_db), _: Principal = Any_):
    return s.profile_row_view(db, s._get(db, m.InspectionProfile, profile_id, "profile"))


@router.post("/inspection-profiles/{profile_id}/calibrate")
async def calibrate(profile_id: str, hold: float = Form(gt=0, lt=1), reject: float = Form(gt=0, le=1), reason: str = Form(min_length=1, max_length=2000),
                    evaluation: UploadFile = File(), db: Session = Depends(get_db), p: Principal = Depends(require_roles("quality_manager"))):
    data = await evaluation.read(1_000_001)
    if len(data) > 1_000_000:
        raise ApiError(413, "EVALUATION_TOO_LARGE", "Evaluation report larger than 1 MB")
    return await run_in_threadpool(s.calibrate_profile, db, p, profile_id, hold, reject, data, reason)
