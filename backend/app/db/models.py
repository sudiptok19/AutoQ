"""AutoQ-Trace relational schema (CONTRACT v0.2). Owner: Opus. Single migration lineage in alembic/versions.

Conventions
- Primary keys are prefixed, time-sortable ULID strings; ascending id is the lock order within a class.
- History is appended, never overwritten: memberships/loads/seal revisions carry closing timestamps,
  passports are superseded, inspections are immutable.
- "Current" uniqueness (one open package membership, one active load, one current passport) is enforced
  with partial unique indexes so the database itself rejects races.
"""
from datetime import datetime

from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, DateTime, Float, ForeignKey, Index, Integer, String, Text,
    UniqueConstraint, func, text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _enum(col: str, *values: str) -> CheckConstraint:
    vals = ",".join(f"'{v}'" for v in values)
    return CheckConstraint(f"{col} IN ({vals})", name=f"ck_{col}")


Ts = DateTime(timezone=True)
now = func.now()
ROLES = ("admin", "operator", "quality_engineer", "quality_manager", "dispatcher")
EVIDENCE_SOURCES = ("LIVE_CAPTURE", "PRERECORDED_EVAL", "SYNTHETIC_TEST", "UPLOAD")


# ── identity, evidence, audit, outbox ───────────────────────────────────────
class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    display_name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(Ts, server_default=now)


class UserRole(Base):
    __tablename__ = "user_roles"
    __table_args__ = (_enum("role", *ROLES),)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role: Mapped[str] = mapped_column(String(32), primary_key=True)


class Evidence(Base):
    __tablename__ = "evidence"
    __table_args__ = (_enum("evidence_source", *EVIDENCE_SOURCES),)
    sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    media_type: Mapped[str] = mapped_column(String(100))
    byte_size: Mapped[int] = mapped_column(BigInteger)
    storage_path: Mapped[str] = mapped_column(Text)
    evidence_source: Mapped[str] = mapped_column(String(24))
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(Ts, server_default=now)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    seq: Mapped[int] = mapped_column(BigInteger, unique=True)
    at: Mapped[datetime] = mapped_column(Ts)
    actor: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64))
    entity_type: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[str] = mapped_column(String(80), index=True)
    reason: Mapped[str | None] = mapped_column(Text)
    data: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64), unique=True)


class OutboxEvent(Base):
    """Written in the same transaction as the fact; relayed to the WS bus only after commit."""
    __tablename__ = "outbox_events"
    __table_args__ = (_enum("severity", "INFO", "MEDIUM", "HIGH", "CRITICAL"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    type: Mapped[str] = mapped_column(String(40))
    severity: Mapped[str] = mapped_column(String(10))
    audible_siren: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    entity_type: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[str] = mapped_column(String(80))
    reason_codes: Mapped[list] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    payload: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(Ts, server_default=now)
    sent_at: Mapped[datetime | None] = mapped_column(Ts, index=True)


class WsTicket(Base):
    __tablename__ = "ws_tickets"
    ticket: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    expires_at: Mapped[datetime] = mapped_column(Ts)
    used_at: Mapped[datetime | None] = mapped_column(Ts)


class SigningKey(Base):
    """Public metadata only — private keys never enter the database."""
    __tablename__ = "signing_keys"
    __table_args__ = (_enum("status", "ACTIVE", "RETIRED", "REVOKED"),)
    kid: Mapped[str] = mapped_column(String(40), primary_key=True)
    alg: Mapped[str] = mapped_column(String(16), server_default=text("'Ed25519'"))
    public_key: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(10))
    not_before: Mapped[datetime] = mapped_column(Ts)
    not_after: Mapped[datetime] = mapped_column(Ts)


class Policy(Base):
    __tablename__ = "policies"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    version: Mapped[str] = mapped_column(String(32), unique=True)
    settings: Mapped[dict] = mapped_column(JSONB)
    active: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    demo_default: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(Ts, server_default=now)
    __table_args__ = (Index("uq_policies_one_active", "active", unique=True, postgresql_where=text("active")),)


# ── catalog ─────────────────────────────────────────────────────────────────
class Supplier(Base):
    __tablename__ = "suppliers"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    synthetic: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))


class Part(Base):
    __tablename__ = "parts"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    part_number: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    drawing_rev: Mapped[str] = mapped_column(String(16))
    is_a_part: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    default_sample_size: Mapped[int] = mapped_column(Integer, server_default=text("5"))
    inspection_profile_id: Mapped[str | None] = mapped_column(String(40))


class Line(Base):
    __tablename__ = "lines"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    station_id: Mapped[str] = mapped_column(String(40), unique=True)


class Customer(Base):
    __tablename__ = "customers"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    city: Mapped[str] = mapped_column(String(80))


class InspectionProfile(Base):
    __tablename__ = "inspection_profiles"
    __table_args__ = (UniqueConstraint("part_id", "station_id", "method_version", "calibration_version"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    part_id: Mapped[str] = mapped_column(ForeignKey("parts.id"))
    station_id: Mapped[str] = mapped_column(String(40))
    method_version: Mapped[str] = mapped_column(String(32))
    calibration_version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    calibrated: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    reference_sha256: Mapped[str | None] = mapped_column(ForeignKey("evidence.sha256"))
    thresholds: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(Ts, server_default=now)


# ── material → lot → unit ───────────────────────────────────────────────────
class Heat(Base):
    __tablename__ = "heats"
    __table_args__ = (_enum("status", "ACTIVE", "QUARANTINED"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    heat_code: Mapped[str] = mapped_column(String(40), unique=True)
    supplier_id: Mapped[str] = mapped_column(ForeignKey("suppliers.id"))
    material_grade: Mapped[str] = mapped_column(String(40))
    certificate_sha256: Mapped[str] = mapped_column(ForeignKey("evidence.sha256"))
    certificate_name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(12), server_default=text("'ACTIVE'"))
    received_at: Mapped[datetime] = mapped_column(Ts, server_default=now)


class Lot(Base):
    __tablename__ = "lots"
    __table_args__ = (_enum("sample_status", "NOT_STARTED", "IN_PROGRESS", "PASSED", "FAILED"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    lot_code: Mapped[str] = mapped_column(String(40), unique=True)
    part_id: Mapped[str] = mapped_column(ForeignKey("parts.id"))
    line_id: Mapped[str] = mapped_column(ForeignKey("lines.id"))
    planned_qty: Mapped[int] = mapped_column(Integer)
    policy_version: Mapped[str | None] = mapped_column(String(32))
    frozen_at: Mapped[datetime | None] = mapped_column(Ts)
    sample_status: Mapped[str] = mapped_column(String(12), server_default=text("'NOT_STARTED'"))
    created_at: Mapped[datetime] = mapped_column(Ts, server_default=now)


class LotHeat(Base):
    __tablename__ = "lot_heats"
    lot_id: Mapped[str] = mapped_column(ForeignKey("lots.id"), primary_key=True)
    heat_id: Mapped[str] = mapped_column(ForeignKey("heats.id"), primary_key=True, index=True)


class Unit(Base):
    __tablename__ = "units"
    __table_args__ = (
        UniqueConstraint("lot_id", "production_seq"),
        _enum("quality_status", "UNINSPECTED", "PASS", "HOLD", "REJECT"),
    )
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    serial: Mapped[str] = mapped_column(String(60), unique=True)
    lot_id: Mapped[str] = mapped_column(ForeignKey("lots.id"), index=True)
    part_id: Mapped[str] = mapped_column(ForeignKey("parts.id"))
    production_seq: Mapped[int] = mapped_column(Integer)
    quality_status: Mapped[str] = mapped_column(String(12), server_default=text("'UNINSPECTED'"))
    requires_individual_inspection: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    four_m_tagged: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(Ts, server_default=now)


class LotSample(Base):
    __tablename__ = "lot_samples"
    lot_id: Mapped[str] = mapped_column(ForeignKey("lots.id"), primary_key=True)
    unit_id: Mapped[str] = mapped_column(ForeignKey("units.id"), primary_key=True)
    policy_version: Mapped[str] = mapped_column(String(32))
    selected_at: Mapped[datetime] = mapped_column(Ts, server_default=now)


# ── inspection & passports ──────────────────────────────────────────────────
class Inspection(Base):
    __tablename__ = "inspections"
    __table_args__ = (_enum("verdict", "PASS", "HOLD", "REJECT"), _enum("evidence_source", *EVIDENCE_SOURCES))
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    unit_id: Mapped[str] = mapped_column(ForeignKey("units.id"), index=True)
    lot_id: Mapped[str] = mapped_column(ForeignKey("lots.id"), index=True)
    station_id: Mapped[str] = mapped_column(String(40))
    profile_id: Mapped[str | None] = mapped_column(ForeignKey("inspection_profiles.id"))
    method_version: Mapped[str] = mapped_column(String(32))
    image_sha256: Mapped[str] = mapped_column(ForeignKey("evidence.sha256"))
    aligned_sha256: Mapped[str | None] = mapped_column(ForeignKey("evidence.sha256"))
    diff_sha256: Mapped[str | None] = mapped_column(ForeignKey("evidence.sha256"))
    quality_gate: Mapped[dict] = mapped_column(JSONB)
    analysis: Mapped[dict] = mapped_column(JSONB)
    raw_score: Mapped[float | None] = mapped_column(Float)
    verdict: Mapped[str] = mapped_column(String(8))
    reasons: Mapped[list] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    evidence_source: Mapped[str] = mapped_column(String(24))
    policy_version: Mapped[str] = mapped_column(String(32))
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(Ts, server_default=now, index=True)


class InspectionReview(Base):
    __tablename__ = "inspection_reviews"
    __table_args__ = (_enum("action", "REINSPECT", "REJECT"), UniqueConstraint("inspection_id"))
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    inspection_id: Mapped[str] = mapped_column(ForeignKey("inspections.id"))
    action: Mapped[str] = mapped_column(String(10))
    reason: Mapped[str] = mapped_column(Text)
    reviewer_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(Ts, server_default=now)


class Passport(Base):
    __tablename__ = "passports"
    __table_args__ = (
        _enum("status", "ACTIVE", "HELD", "REVOKED", "SUPERSEDED"),
        _enum("inspection_basis", "INDIVIDUAL_100PCT", "SAMPLED_LOT"),
        Index("uq_passports_current_per_unit", "unit_id", unique=True, postgresql_where=text("status IN ('ACTIVE','HELD')")),
    )
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    unit_id: Mapped[str] = mapped_column(ForeignKey("units.id"), index=True)
    kid: Mapped[str] = mapped_column(ForeignKey("signing_keys.kid"))
    token: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSONB)
    payload_version: Mapped[int] = mapped_column(Integer)
    inspection_basis: Mapped[str] = mapped_column(String(20))
    four_m_tagged: Mapped[bool] = mapped_column(Boolean)
    status: Mapped[str] = mapped_column(String(12))
    superseded_by_passport_id: Mapped[str | None] = mapped_column(ForeignKey("passports.id"))
    issued_at: Mapped[datetime] = mapped_column(Ts, server_default=now)


class PassportInspection(Base):
    __tablename__ = "passport_inspections"
    passport_id: Mapped[str] = mapped_column(ForeignKey("passports.id"), primary_key=True)
    inspection_id: Mapped[str] = mapped_column(ForeignKey("inspections.id"), primary_key=True)
    ord: Mapped[int] = mapped_column(Integer)
    evidence_digest: Mapped[str] = mapped_column(String(80))


# ── 4M containment ──────────────────────────────────────────────────────────
class FourMEvent(Base):
    __tablename__ = "four_m_events"
    __table_args__ = (_enum("category", "MAN", "MACHINE", "MATERIAL", "METHOD"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True)
    line_id: Mapped[str] = mapped_column(ForeignKey("lines.id"))
    category: Mapped[str] = mapped_column(String(10))
    planned: Mapped[bool] = mapped_column(Boolean)
    description: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    effective_at: Mapped[datetime] = mapped_column(Ts)
    policy_version: Mapped[str] = mapped_column(String(32))
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(Ts, server_default=now)


class ContainmentRun(Base):
    __tablename__ = "containment_runs"
    __table_args__ = (_enum("status", "ACTIVE", "READY_FOR_CLEARANCE", "CLEARED"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    event_id: Mapped[str] = mapped_column(ForeignKey("four_m_events.id"), unique=True)
    line_id: Mapped[str] = mapped_column(ForeignKey("lines.id"), index=True)
    required_qty: Mapped[int] = mapped_column(Integer)
    cleared_qty: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    sequence_epoch: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    status: Mapped[str] = mapped_column(String(20), server_default=text("'ACTIVE'"))
    rule: Mapped[dict] = mapped_column(JSONB)
    policy_version: Mapped[str] = mapped_column(String(32))
    cleared_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    clearance_reason: Mapped[str | None] = mapped_column(Text)
    cleared_at: Mapped[datetime | None] = mapped_column(Ts)
    created_at: Mapped[datetime] = mapped_column(Ts, server_default=now)


class ContainmentUnit(Base):
    __tablename__ = "containment_units"
    __table_args__ = (_enum("scope_role", "EXISTING_RECHECK", "EXISTING_HOLD", "NEW_PRODUCTION"),)
    run_id: Mapped[str] = mapped_column(ForeignKey("containment_runs.id"), primary_key=True)
    unit_id: Mapped[str] = mapped_column(ForeignKey("units.id"), primary_key=True, index=True)
    production_seq: Mapped[int] = mapped_column(Integer)
    scope_role: Mapped[str] = mapped_column(String(20))


class ContainmentCredit(Base):
    __tablename__ = "containment_credits"
    run_id: Mapped[str] = mapped_column(ForeignKey("containment_runs.id"), primary_key=True)
    epoch: Mapped[int] = mapped_column(Integer, primary_key=True)
    unit_id: Mapped[str] = mapped_column(ForeignKey("units.id"), primary_key=True)
    inspection_id: Mapped[str] = mapped_column(ForeignKey("inspections.id"))
    created_at: Mapped[datetime] = mapped_column(Ts, server_default=now)


# ── orders, packages, seals ─────────────────────────────────────────────────
class Order(Base):
    __tablename__ = "orders"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    order_no: Mapped[str] = mapped_column(String(40), unique=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"))
    part_id: Mapped[str] = mapped_column(ForeignKey("parts.id"))
    qty_units: Mapped[int] = mapped_column(Integer)
    destination: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(Ts, server_default=now)


class Package(Base):
    __tablename__ = "packages"
    __table_args__ = (_enum("state", "OPEN", "SEALED", "SEAL_REVIEW", "LOADED", "IN_TRANSIT", "DELIVERED"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    label: Mapped[str] = mapped_column(String(40), unique=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    part_id: Mapped[str] = mapped_column(ForeignKey("parts.id"))
    state: Mapped[str] = mapped_column(String(12), server_default=text("'OPEN'"))
    current_seal_revision_id: Mapped[str | None] = mapped_column(String(40))
    token: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(Ts, server_default=now)


class PackageItem(Base):
    __tablename__ = "package_items"
    __table_args__ = (Index("uq_package_items_open_unit", "unit_id", unique=True, postgresql_where=text("removed_at IS NULL")),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    package_id: Mapped[str] = mapped_column(ForeignKey("packages.id"), index=True)
    unit_id: Mapped[str] = mapped_column(ForeignKey("units.id"), index=True)
    passport_id: Mapped[str] = mapped_column(ForeignKey("passports.id"))
    added_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    added_at: Mapped[datetime] = mapped_column(Ts, server_default=now)
    removed_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    removed_at: Mapped[datetime | None] = mapped_column(Ts)
    removal_reason: Mapped[str | None] = mapped_column(Text)


class SealRevision(Base):
    __tablename__ = "seal_revisions"
    __table_args__ = (UniqueConstraint("package_id", "revision_no"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    package_id: Mapped[str] = mapped_column(ForeignKey("packages.id"), index=True)
    revision_no: Mapped[int] = mapped_column(Integer)
    ref_sha256: Mapped[str] = mapped_column(ForeignKey("evidence.sha256"))
    reference_features: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    enrolled_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    enrolled_at: Mapped[datetime] = mapped_column(Ts, server_default=now)
    invalidated_at: Mapped[datetime | None] = mapped_column(Ts)
    invalidation_reason: Mapped[str | None] = mapped_column(Text)


class SealCheck(Base):
    __tablename__ = "seal_checks"
    __table_args__ = (
        _enum("kind", "ENROLL", "RECHECK"),
        CheckConstraint("result IS NULL OR result IN ('INTACT','SUSPICIOUS','UNVERIFIABLE')", name="ck_result"),
        _enum("evidence_source", *EVIDENCE_SOURCES),
    )
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    package_id: Mapped[str] = mapped_column(ForeignKey("packages.id"), index=True)
    revision_id: Mapped[str] = mapped_column(ForeignKey("seal_revisions.id"))
    kind: Mapped[str] = mapped_column(String(8))
    result: Mapped[str | None] = mapped_column(String(14))
    method_version: Mapped[str] = mapped_column(String(32))
    metrics: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    quality_gate: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    evidence: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    explanation: Mapped[str] = mapped_column(Text)
    evidence_source: Mapped[str] = mapped_column(String(24))
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(Ts, server_default=now)


# ── shipments, scans, loads, movements ──────────────────────────────────────
class Shipment(Base):
    __tablename__ = "shipments"
    __table_args__ = (_enum("status", "OPEN", "CLOSED", "IN_TRANSIT", "DELIVERED"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    vehicle_no: Mapped[str] = mapped_column(String(32))
    dock: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(12), server_default=text("'OPEN'"))
    manifest_sha256: Mapped[str | None] = mapped_column(String(64))
    closed_at: Mapped[datetime | None] = mapped_column(Ts)
    created_at: Mapped[datetime] = mapped_column(Ts, server_default=now)


class Scan(Base):
    __tablename__ = "scans"
    __table_args__ = (_enum("decision", "ACCEPTED", "BLOCKED"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    shipment_id: Mapped[str] = mapped_column(ForeignKey("shipments.id"), index=True)
    package_id: Mapped[str | None] = mapped_column(ForeignKey("packages.id"))
    client_event_id: Mapped[str] = mapped_column(String(80), unique=True)
    content_sha256: Mapped[str] = mapped_column(String(64))
    device_id: Mapped[str | None] = mapped_column(String(40))
    decision: Mapped[str] = mapped_column(String(8))
    reasons: Mapped[list] = mapped_column(JSONB)
    checks: Mapped[list] = mapped_column(JSONB)
    offline: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    captured_at: Mapped[datetime | None] = mapped_column(Ts)
    decided_at: Mapped[datetime] = mapped_column(Ts, server_default=now)
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"))


class Load(Base):
    __tablename__ = "loads"
    __table_args__ = (Index("uq_loads_active_package", "package_id", unique=True, postgresql_where=text("removed_at IS NULL")),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    package_id: Mapped[str] = mapped_column(ForeignKey("packages.id"), index=True)
    shipment_id: Mapped[str] = mapped_column(ForeignKey("shipments.id"), index=True)
    accepted_scan_id: Mapped[str] = mapped_column(ForeignKey("scans.id"), unique=True)
    unit_count: Mapped[int] = mapped_column(Integer)
    loaded_at: Mapped[datetime] = mapped_column(Ts, server_default=now)
    removed_at: Mapped[datetime | None] = mapped_column(Ts)
    removed_reason: Mapped[str | None] = mapped_column(Text)


class MovementEvent(Base):
    __tablename__ = "movement_events"
    __table_args__ = (_enum("type", "DEPARTED", "CHECKPOINT", "DELIVERED"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    shipment_id: Mapped[str] = mapped_column(ForeignKey("shipments.id"), index=True)
    type: Mapped[str] = mapped_column(String(10))
    location: Mapped[str] = mapped_column(String(200))
    occurred_at: Mapped[datetime] = mapped_column(Ts)
    recorded_at: Mapped[datetime] = mapped_column(Ts, server_default=now)
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"))


# ── offline devices ─────────────────────────────────────────────────────────
class Device(Base):
    __tablename__ = "devices"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    operator_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    last_seq: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    head_hash: Mapped[str] = mapped_column(String(64), server_default=text("repeat('0', 64)"))
    registered_at: Mapped[datetime] = mapped_column(Ts, server_default=now)
    # Last moment the server knew this device had nothing unsynced: a sync, or a snapshot download reporting an empty queue.
    checked_in_at: Mapped[datetime | None] = mapped_column(Ts)


class OfflineSnapshot(Base):
    __tablename__ = "offline_snapshots"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id"), index=True)
    shipment_id: Mapped[str] = mapped_column(ForeignKey("shipments.id"))
    operator_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    kid: Mapped[str] = mapped_column(ForeignKey("signing_keys.kid"))
    token: Mapped[str] = mapped_column(Text)
    issued_at: Mapped[datetime] = mapped_column(Ts)
    expires_at: Mapped[datetime] = mapped_column(Ts)


class DeviceEvent(Base):
    __tablename__ = "device_events"
    __table_args__ = (_enum("outcome", "ACCEPTED", "BLOCKED", "DUPLICATE", "CONFLICT"),)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id"), primary_key=True)
    seq: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_event_id: Mapped[str] = mapped_column(String(80), unique=True)
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))
    snapshot_id: Mapped[str | None] = mapped_column(ForeignKey("offline_snapshots.id"))
    scan_id: Mapped[str | None] = mapped_column(ForeignKey("scans.id"))
    local_decision: Mapped[str] = mapped_column(String(20))
    outcome: Mapped[str] = mapped_column(String(10))
    scanned_at: Mapped[datetime] = mapped_column(Ts)
    synced_at: Mapped[datetime] = mapped_column(Ts, server_default=now)
    operator_id: Mapped[str] = mapped_column(ForeignKey("users.id"))


# ── recall, segregation, alerts ─────────────────────────────────────────────
class Recall(Base):
    __tablename__ = "recalls"
    __table_args__ = (_enum("scope_type", "HEAT", "LOT", "UNIT", "PACKAGE"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    scope_type: Mapped[str] = mapped_column(String(8))
    scope_id: Mapped[str] = mapped_column(String(40))
    reason: Mapped[str] = mapped_column(Text)
    impact: Mapped[dict] = mapped_column(JSONB)
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    executed_at: Mapped[datetime] = mapped_column(Ts, server_default=now)
    released_at: Mapped[datetime | None] = mapped_column(Ts)
    released_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    release_reason: Mapped[str | None] = mapped_column(Text)


class RecallHold(Base):
    __tablename__ = "recall_holds"
    __table_args__ = (
        _enum("entity_type", "HEAT", "LOT", "UNIT", "PACKAGE"),
        Index("ix_recall_holds_active", "entity_type", "entity_id", postgresql_where=text("released_at IS NULL")),
    )
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    recall_id: Mapped[str] = mapped_column(ForeignKey("recalls.id"), index=True)
    entity_type: Mapped[str] = mapped_column(String(8))
    entity_id: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(Ts, server_default=now)
    released_at: Mapped[datetime | None] = mapped_column(Ts)


class SegregationTask(Base):
    __tablename__ = "segregation_tasks"
    __table_args__ = (
        _enum("type", "REMOVAL_REQUIRED", "SEGREGATION_REQUIRED", "IN_TRANSIT_RECALL", "NOTIFY_CUSTOMER"),
        _enum("status", "OPEN", "COMPLETED"),
    )
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    type: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(10), server_default=text("'OPEN'"))
    dedupe_key: Mapped[str] = mapped_column(String(200), unique=True)
    recall_id: Mapped[str | None] = mapped_column(ForeignKey("recalls.id"))
    scan_id: Mapped[str | None] = mapped_column(ForeignKey("scans.id"))
    package_id: Mapped[str | None] = mapped_column(ForeignKey("packages.id"))
    shipment_id: Mapped[str | None] = mapped_column(ForeignKey("shipments.id"))
    reason: Mapped[str] = mapped_column(Text)
    consignee_action: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(Ts, server_default=now)
    completed_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    completed_at: Mapped[datetime | None] = mapped_column(Ts)
    completion_reason: Mapped[str | None] = mapped_column(Text)


class Alert(Base):
    __tablename__ = "alerts"
    __table_args__ = (
        _enum("severity", "INFO", "MEDIUM", "HIGH", "CRITICAL"),
        _enum("status", "OPEN", "ACK", "RESOLVED"),
    )
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    type: Mapped[str] = mapped_column(String(32))
    severity: Mapped[str] = mapped_column(String(10))
    audible_siren: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    entity_type: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[str] = mapped_column(String(80))
    reason_codes: Mapped[list] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    details: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    dedupe_key: Mapped[str | None] = mapped_column(String(200), unique=True)
    status: Mapped[str] = mapped_column(String(10), server_default=text("'OPEN'"))
    created_at: Mapped[datetime] = mapped_column(Ts, server_default=now)
    ack_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    ack_at: Mapped[datetime | None] = mapped_column(Ts)
    ack_reason: Mapped[str | None] = mapped_column(Text)
    resolved_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    resolved_at: Mapped[datetime | None] = mapped_column(Ts)
    resolution_reason: Mapped[str | None] = mapped_column(Text)


# ── SPC persistence (computation is Astra's spc module) ─────────────────────
class SpcBaseline(Base):
    __tablename__ = "spc_baselines"
    __table_args__ = (UniqueConstraint("part_id", "station_id", "method_version"), _enum("status", "COLLECTING", "LOW_VARIANCE", "READY"))
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    part_id: Mapped[str] = mapped_column(ForeignKey("parts.id"))
    station_id: Mapped[str] = mapped_column(String(40))
    method_version: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(14))
    n: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    mean: Mapped[float | None] = mapped_column(Float)
    std: Mapped[float | None] = mapped_column(Float)
    frozen_at: Mapped[datetime | None] = mapped_column(Ts)
    rule: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))


class SpcWarning(Base):
    __tablename__ = "spc_warnings"
    __table_args__ = (UniqueConstraint("baseline_id", "inspection_id"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    baseline_id: Mapped[str] = mapped_column(ForeignKey("spc_baselines.id"))
    inspection_id: Mapped[str] = mapped_column(ForeignKey("inspections.id"))
    rules: Mapped[list] = mapped_column(JSONB)
    alert_id: Mapped[str | None] = mapped_column(ForeignKey("alerts.id"))
    created_at: Mapped[datetime] = mapped_column(Ts, server_default=now)


# ── 8D reports ──────────────────────────────────────────────────────────────
class Report(Base):
    __tablename__ = "reports"
    __table_args__ = (_enum("status", "DRAFT", "APPROVED"), _enum("source_type", "RECALL", "FOUR_M", "ALERT"))
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    source_type: Mapped[str] = mapped_column(String(8))
    source_id: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(10), server_default=text("'DRAFT'"))
    generator: Mapped[str] = mapped_column(String(40))
    current_revision_id: Mapped[str | None] = mapped_column(String(40))
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(Ts, server_default=now)
    approved_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    approved_at: Mapped[datetime | None] = mapped_column(Ts)
    approval_reason: Mapped[str | None] = mapped_column(Text)


class ReportRevision(Base):
    __tablename__ = "report_revisions"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    report_id: Mapped[str] = mapped_column(ForeignKey("reports.id"), index=True)
    snapshot: Mapped[dict] = mapped_column(JSONB)
    snapshot_sha256: Mapped[str] = mapped_column(String(64))
    pdf_sha256: Mapped[str | None] = mapped_column(ForeignKey("evidence.sha256"))
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(Ts, server_default=now)


class ReportEvidence(Base):
    __tablename__ = "report_evidence"
    revision_id: Mapped[str] = mapped_column(ForeignKey("report_revisions.id"), primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(40), primary_key=True)
    entity_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    sha256: Mapped[str | None] = mapped_column(ForeignKey("evidence.sha256"))
