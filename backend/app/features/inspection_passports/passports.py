"""Passport issuance, superseding and verification (CONTRACT §10.1-10.3). Owner: Opus.
Signing/verification primitives are Astra's `passport_crypto`; this module decides WHEN and WHAT."""
import hashlib
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import audit, outbox
from app.core.canonical import canonicalize
from app.core.errors import ApiError
from app.core.ids import new_id
from app.core.signing import list_public_keys, load_signing_key, register_active_key
from app.db import models as m
from app.features.passport_crypto.service import CryptoError, KeyRecord, sign, verify
from app.policy import eligibility


def inspection_digest(i: m.Inspection) -> str:
    """Digest of the immutable inspection evidence record a passport cites."""
    rec = {"id": i.id, "unit": i.unit_id, "image": i.image_sha256, "verdict": i.verdict, "method_version": i.method_version,
           "score": None if i.raw_score is None else f"{i.raw_score:.6f}", "evidence_source": i.evidence_source}
    return "sha256:" + hashlib.sha256(canonicalize(rec).encode()).hexdigest()


def open_runs(db: Session, unit: m.Unit) -> list[m.ContainmentRun]:
    return eligibility.open_containment_runs(db, unit)


def latest_inspection(db: Session, unit_id: str) -> m.Inspection | None:
    return db.scalars(select(m.Inspection).where(m.Inspection.unit_id == unit_id).order_by(m.Inspection.created_at.desc(), m.Inspection.id.desc()).limit(1)).first()


def sample_pass_inspections(db: Session, lot: m.Lot) -> list[m.Inspection] | None:
    """Latest inspection of every frozen sample unit, if ALL passed; otherwise None."""
    ids = list(db.scalars(select(m.LotSample.unit_id).where(m.LotSample.lot_id == lot.id)))
    if not ids:
        return None
    out = []
    for uid in ids:
        i = latest_inspection(db, uid)
        if not i or i.verdict != "PASS":
            return None
        out.append(i)
    return out


def issuance_plan(db: Session, unit: m.Unit) -> tuple[str, list[m.Inspection]] | list[dict]:
    """Return (basis, authorizing inspections) or the blockers that prevent issuance. Never guesses."""
    lot = db.get(m.Lot, unit.lot_id)
    part = db.get(m.Part, unit.part_id)
    blockers: list[dict] = []
    if not lot.frozen_at:
        blockers.append({"code": "LOT_NOT_FROZEN", "message": "Freeze the lot before release"})
    for run in open_runs(db, unit):
        blockers.append({"code": "CONTAINMENT_ACTIVE", "message": f"4M containment run {run.id} is {run.status}; issuance blocked until QM clearance", "ref": run.id})
    if unit.quality_status in ("HOLD", "REJECT"):
        blockers.append({"code": f"UNIT_{unit.quality_status}", "message": f"Unit's own latest result is {unit.quality_status}"})
    if blockers:
        return blockers
    own = latest_inspection(db, unit.id)
    if own and own.verdict == "PASS":
        return "INDIVIDUAL_100PCT", [own]
    if part.is_a_part or unit.requires_individual_inspection:
        return [{"code": "INDIVIDUAL_INSPECTION_REQUIRED", "message": "A-part or 4M-affected unit needs its own PASS"}]
    sample = sample_pass_inspections(db, lot)
    if sample is None:
        return [{"code": "SAMPLE_INCOMPLETE", "message": f"Lot sample status is {lot.sample_status}; every frozen sample unit must PASS"}]
    return "SAMPLED_LOT", sample


def _sign_pp(db: Session, unit: m.Unit, basis: str, auth: list[m.Inspection], pid: str) -> tuple[str, dict, str]:
    kid, key = load_signing_key()
    register_active_key(db)
    lot = db.get(m.Lot, unit.lot_id)
    part = db.get(m.Part, unit.part_id)
    heats = sorted(db.scalars(select(m.Heat.heat_code).join(m.LotHeat, m.LotHeat.heat_id == m.Heat.id).where(m.LotHeat.lot_id == lot.id)))
    auth_sorted = sorted(auth, key=lambda i: i.id)
    payload = {"v": 2, "typ": "PP", "kid": kid, "id": pid, "iat": int(datetime.now(UTC).timestamp()), "unit": unit.id, "serial": unit.serial,
               "lot": lot.lot_code, "part": part.part_number, "heats": heats, "inspection_basis": basis,
               "authorizing_inspection_ids": [i.id for i in auth_sorted], "evidence_digests": [inspection_digest(i) for i in auth_sorted],
               "four_m_tagged": bool(unit.four_m_tagged)}
    try:
        token = sign(payload, kid, key)
    except CryptoError as e:
        raise ApiError(500, e.code, f"Passport signing failed: {e.message}", {"component": "passport_crypto"})
    return token, payload, kid


def issue(db: Session, unit: m.Unit, actor: str) -> tuple[m.Passport | None, list[dict]]:
    """Idempotent. Returns (current passport, []) or (None, blockers). Supersedes a SAMPLED_LOT credential
    when the unit now qualifies individually. Caller holds the unit row lock."""
    plan = issuance_plan(db, unit)
    if isinstance(plan, list):
        return None, plan
    basis, auth = plan
    current = eligibility.current_passport(db, unit)
    if current and current.inspection_basis == "INDIVIDUAL_100PCT" and current.four_m_tagged == bool(unit.four_m_tagged):
        return current, []  # already individually certified with the same 4M tag; retries never re-mint
    if current and basis == "SAMPLED_LOT":
        return current, []  # never re-mint or downgrade on sample basis
    pid = new_id("pp")
    token, payload, kid = _sign_pp(db, unit, basis, auth, pid)
    if current:
        current.status = "SUPERSEDED"
        db.flush()
    pp = m.Passport(id=pid, unit_id=unit.id, kid=kid, token=token, payload=payload, payload_version=2, inspection_basis=basis,
                    four_m_tagged=bool(unit.four_m_tagged), status="ACTIVE")
    db.add(pp); db.flush()
    if current:
        current.superseded_by_passport_id = pid
    for n, i in enumerate(sorted(auth, key=lambda i: i.id)):
        db.add(m.PassportInspection(passport_id=pid, inspection_id=i.id, ord=n, evidence_digest=inspection_digest(i)))
    db.flush()
    audit.append(db, actor, "PASSPORT_SUPERSEDED" if current else "PASSPORT_ISSUED", "passport", pid,
                 data={"unit": unit.serial, "basis": basis, "kid": kid, "supersedes": current.id if current else None, "four_m_tagged": bool(unit.four_m_tagged)})
    return pp, []


def revoke(db: Session, unit: m.Unit, actor: str, reason: str) -> m.Passport | None:
    pp = eligibility.current_passport(db, unit)
    if not pp:
        return None
    pp.status = "REVOKED"
    db.flush()
    audit.append(db, actor, "PASSPORT_REVOKED", "passport", pp.id, reason, data={"unit": unit.serial})
    outbox.emit(db, "PASSPORT_REVOKED", "PASSPORT", pp.id, severity="HIGH", reason_codes=["UNIT_REJECTED"], payload={"unit_id": unit.id, "serial": unit.serial})
    return pp


def view(db: Session, pp: m.Passport) -> dict:
    unit = db.get(m.Unit, pp.unit_id)
    return {"id": pp.id, "unit_id": pp.unit_id, "serial": unit.serial if unit else None, "status": pp.status, "token": pp.token, "kid": pp.kid,
            "payload_version": pp.payload_version, "inspection_basis": pp.inspection_basis, "four_m_tagged": pp.four_m_tagged,
            "authorizing_inspection_ids": pp.payload.get("authorizing_inspection_ids", []), "evidence_digests": pp.payload.get("evidence_digests", []),
            "superseded_by_passport_id": pp.superseded_by_passport_id, "issued_at": pp.issued_at.isoformat() if pp.issued_at else None}


def trusted_keys(db: Session) -> list[KeyRecord]:
    return [KeyRecord(kid=k["kid"], public_key=k["public_key"], status=k["status"], not_before=k["not_before"], not_after=k["not_after"]) for k in list_public_keys(db)]


def verify_token(db: Session, token: str) -> dict:
    """Cryptographic validity and live eligibility are reported separately — a valid signature is not authorization."""
    try:
        v = verify(token, trusted_keys(db), None, int(datetime.now(UTC).timestamp()))
    except CryptoError as e:
        return {"signature_valid": False, "failure": e.code, "message": e.message, "kid": None, "typ": None, "v": None, "payload": None,
                "exists": False, "status": None, "eligibility": None}
    out = {"signature_valid": True, "failure": None, "kid": v.kid, "typ": v.typ, "v": v.v, "payload": v.payload, "warning": v.warning,
           "exists": False, "status": None, "superseded_by_passport_id": None, "eligibility": None}
    if v.typ == "PP":
        pp = db.get(m.Passport, v.payload["id"])
        if pp:
            same = pp.token == token
            out |= {"exists": same, "status": pp.status if same else "TOKEN_MISMATCH", "superseded_by_passport_id": pp.superseded_by_passport_id}
            if same:
                out["eligibility"] = eligibility.compute(db, db.get(m.Unit, pp.unit_id)).as_dict()
    elif v.typ == "PKG":
        pkg = db.get(m.Package, v.payload["id"])
        if pkg:
            out |= {"exists": pkg.token == token, "status": pkg.state if pkg.token == token else "TOKEN_MISMATCH"}
    return out
