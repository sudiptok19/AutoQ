"""Integration seam between Opus (DB/transactions) and Astra's isolated modules.

Owner: Opus. Astra imports these types and the `ports` dependency; Astra never edits this file.
Astra's routers depend on `ports()` for all persistence; their tests replace it with a fake via
`app.dependency_overrides[ports] = lambda: FakePorts()` — no database required.
Opus registers the real implementation in `app/main.py` (`register_ports(...)`).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from sqlalchemy.orm import Session
from typing import TYPE_CHECKING

from app.core.auth import Principal
from app.core.errors import ApiError

if TYPE_CHECKING:   # type-only: Astra's SPC DTOs stay feature-local (no import cycle, no runtime dependency)
    from app.features.spc.service import ContainmentGenealogy, Investigation, InvestigationContext, InvestigationEvent

# ── inputs Opus hands to Astra's pure functions ─────────────────────────────


@dataclass(frozen=True)
class VisionProfile:
    id: str
    part_id: str
    station_id: str
    method_version: str            # e.g. "gd-0.3"; owned by component_vision
    calibration_version: int
    calibrated: bool               # False until a held-out evaluation is accepted
    reference_sha256: str | None
    thresholds: dict[str, Any]     # blur_min, glare_max, min_w, min_h, hold, reject, min_inliers, …


@dataclass(frozen=True)
class SealProfile:
    method_version: str            # e.g. "seal-orb-ssim-0.2"
    min_inliers: int = 40
    intact_ssim: float = 0.72
    suspicious_ssim: float = 0.60
    roi: tuple[int, int, int, int] | None = None   # x, y, w, h in reference pixels; None = whole frame
    blur_min: float = 80.0
    glare_max: float = 0.12


@dataclass(frozen=True)
class SealContext:
    package_id: str
    revision_id: str
    reference_bytes: bytes
    profile: SealProfile


@dataclass(frozen=True)
class SpcPoint:
    inspection_id: str
    t: datetime
    score: float
    synthetic: bool


@dataclass(frozen=True)
class SpcBaseline:
    n: int
    mean: float | None
    std: float | None
    frozen_at: datetime | None


@dataclass(frozen=True)
class SpcRule:
    k_sigma: float = 0.5
    h_sigma: float = 5.0
    min_n: int = 30
    min_std: float = 0.005


@dataclass(frozen=True)
class EvidenceRef:
    type: str                      # INSPECTION | FOUR_M | SCAN | RECALL | PACKAGE | …
    id: str
    label: str
    sha256: str | None = None


@dataclass(frozen=True)
class ReportItem:
    kind: str                      # FACT | HYPOTHESIS | PROPOSED | PENDING_HUMAN
    text: str
    evidence: tuple[EvidenceRef, ...] = ()


@dataclass(frozen=True)
class ReportSection:
    key: str                       # D1 … D8
    title: str
    items: tuple[ReportItem, ...]


@dataclass(frozen=True)
class ReportSnapshot:
    id: str
    revision_id: str
    status: str                    # DRAFT | APPROVED
    source_type: str               # RECALL | FOUR_M | ALERT
    source_id: str
    generator: str
    created_at: datetime
    as_of: datetime
    counts: dict[str, int]         # affected_units, held_packages, at_plant, loaded_not_departed, in_transit, delivered
    evidence_source: str           # LIVE_CAPTURE | PRERECORDED_EVAL | SYNTHETIC_TEST | MIXED
    sections: tuple[ReportSection, ...]
    approved_by: str | None = None
    approved_at: datetime | None = None
    meta: dict[str, Any] = field(default_factory=dict)


# ── what Opus implements ────────────────────────────────────────────────────


class Ports(Protocol):
    # component vision
    def list_profiles(self, db: Session, principal: Principal) -> list[VisionProfile]: ...
    def load_profile(self, db: Session, profile_id: str, principal: Principal) -> VisionProfile: ...
    def save_reference(self, db: Session, profile_id: str, image: bytes, media_type: str, principal: Principal) -> VisionProfile: ...
    # seal vision
    def load_seal_context(self, db: Session, package_id: str, principal: Principal) -> SealContext: ...
    def record_recheck(self, db: Session, ctx: SealContext, evidence: Any, probe: bytes, probe_media_type: str, evidence_source: str, principal: Principal) -> dict: ...
    # spc
    def load_spc_inputs(self, db: Session, part_id: str, line_id: str, method_version: str, principal: Principal) -> tuple[list[SpcPoint], SpcBaseline | None, SpcRule, list[dict]]: ...
    # A3 containment review (agreed with Astra on PR #35): Opus reads the immutable inspection prefix and a bounded
    # genealogy; Astra's pure investigate()/recommend_containment() decide nothing about storage or approval.
    def load_spc_investigation_inputs(self, db: Session, target_inspection_id: str, principal: Principal) -> tuple[InvestigationContext, list[InvestigationEvent], SpcRule]: ...
    def load_spc_containment_genealogy(self, db: Session, investigation: Investigation, principal: Principal) -> ContainmentGenealogy: ...
    # reports
    def load_snapshot(self, db: Session, report_id: str, principal: Principal) -> ReportSnapshot: ...
    def record_pdf(self, db: Session, snapshot: ReportSnapshot, pdf: bytes, principal: Principal) -> str: ...
    # crypto
    def list_public_keys(self, db: Session) -> list[dict]: ...


_impl: Ports | None = None


def register_ports(impl: Ports) -> None:
    global _impl
    _impl = impl


def ports() -> Ports:
    """FastAPI dependency. Fails closed until Opus registers the real adapters."""
    if _impl is None:
        raise ApiError(503, "ADAPTER_UNAVAILABLE", "Persistence adapters not registered", {"component": "ports"})
    return _impl
