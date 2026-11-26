"""SPC calculations for one part, station, and inspection method version."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from math import isfinite
from operator import attrgetter
from statistics import mean, stdev
from typing import Literal, cast

from app.core.errors import ApiError
from app.ports import SpcBaseline, SpcPoint, SpcRule


@dataclass(frozen=True)
class Series:
    baseline: dict
    rule: dict
    points: list[dict]
    warnings: list[dict]


INVESTIGATION_QUALIFICATION_VERSION = "spc-investigation-1"
FIVE_RISING_COUNT = 5


@dataclass(frozen=True)
class InvestigationContext:
    part_id: str
    station_id: str
    method_version: str
    profile_id: str
    reference_sha256: str


@dataclass(frozen=True)
class InvestigationObservation:
    observation_id: str
    inspection_id: str
    unit_id: str | None
    t: datetime
    score: float | None
    profile_id: str | None
    reference_sha256: str | None
    calibrated: bool
    evidence_source: Literal["LIVE_CAPTURE", "PRERECORDED_EVAL", "SYNTHETIC_TEST"] | None
    provenance_verified: bool
    routine: bool | None


@dataclass(frozen=True)
class InvestigationGap:
    observation_id: str
    t: datetime
    reason: str


InvestigationEvent = InvestigationObservation | InvestigationGap
BaselineStatus = Literal["COLLECTING", "LOW_VARIANCE", "READY"]
RuleName = Literal["CUSUM_UPPER", "FIVE_RISING"]
IssueCode = Literal[
    "NON_PRODUCTION_SOURCE", "REINSPECTION", "ROUTINE_STATUS_UNKNOWN", "UNIT_ID_MISSING",
    "UNIT_ALREADY_OBSERVED", "CALIBRATION_CONTEXT_MISSING", "CALIBRATION_CONTEXT_CHANGED",
    "UNCALIBRATED", "CAPTURE_PROVENANCE_UNVERIFIED", "SCORE_MISSING", "DECLARED_GAP",
    "TARGET_NOT_FOUND", "BASELINE_STABILITY_UNVERIFIED", "PRODUCTION_COVERAGE_UNKNOWN",
]


@dataclass(frozen=True)
class InvestigationIssue:
    code: IssueCode
    observation_id: str | None = None
    inspection_id: str | None = None
    detail: str | None = None


@dataclass(frozen=True)
class InvestigationBaseline:
    status: BaselineStatus
    stats: SpcBaseline
    observations: tuple[InvestigationObservation, ...]


@dataclass(frozen=True)
class InvestigationMeasurement:
    observation: InvestigationObservation
    z: float
    cusum: float


@dataclass(frozen=True)
class CusumEvidence:
    anchor_observation_id: str
    previous_zero_observation_id: str | None
    contributors: tuple[InvestigationMeasurement, ...]


@dataclass(frozen=True)
class FiveRisingEvidence:
    anchor_observation_id: str
    contributors: tuple[InvestigationMeasurement, ...]


@dataclass(frozen=True)
class Investigation:
    context: InvestigationContext
    target_inspection_id: str
    status: Literal["SIGNAL", "NO_SIGNAL", "INSUFFICIENT_EVIDENCE"]
    baseline: InvestigationBaseline
    rule: SpcRule
    issues: tuple[InvestigationIssue, ...]
    trigger: InvestigationMeasurement | None
    rules: tuple[RuleName, ...]
    cusum: CusumEvidence | None
    five_rising: FiveRisingEvidence | None
    qualification_version: str = INVESTIGATION_QUALIFICATION_VERSION
    anchor_basis: Literal["INSPECTION_ORDER"] = "INSPECTION_ORDER"
    five_rising_count: int = FIVE_RISING_COUNT


_REMEDIATION = {
    "SPC_RULE_INVALID": "Correct the SPC rule configuration for this part, station, and method version.",
    "SPC_INPUT_INVALID": "Check the inspection scores and timestamps supplied for this SPC context.",
    "SPC_BASELINE_INVALID": "Check the saved SPC baseline and its source observations before retrying.",
}


def _invalid(code: str, message: str) -> ApiError:
    return ApiError(422, code, message, {"component": "spc", "remediation": _REMEDIATION[code]})


def _finite(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return isfinite(value)
    except OverflowError:
        return False


def _valid_score(value: object) -> bool:
    return _finite(value) and 0 <= value <= 1


def _validate_rule(rule: SpcRule) -> None:
    if (
        not isinstance(rule, SpcRule)
        or not _finite(rule.k_sigma)
        or rule.k_sigma <= 0
        or not _finite(rule.h_sigma)
        or rule.h_sigma <= 0
        or type(rule.min_n) is not int
        or rule.min_n < 2
        or not _finite(rule.min_std)
        or rule.min_std < 0
    ):
        raise _invalid("SPC_RULE_INVALID", "SPC requires positive finite k and h, at least two observations, and a nonnegative finite minimum standard deviation")


def _utc_time(value: datetime, code: str) -> datetime:
    try:
        if isinstance(value, datetime) and value.utcoffset() is not None:
            return value.astimezone(UTC)
    except (ValueError, OverflowError) as exc:
        raise _invalid(code, "SPC timestamp must have a valid UTC offset") from exc
    raise _invalid(code, "SPC timestamp must include a time zone")


def _iso_z(value: datetime | None) -> str | None:
    return value.isoformat().replace("+00:00", "Z") if value is not None else None


def _ordered_points(points: list[SpcPoint]) -> list[SpcPoint]:
    if not isinstance(points, list):
        raise _invalid("SPC_INPUT_INVALID", "SPC observations must be supplied as a list")
    unique: dict[str, SpcPoint] = {}
    for point in points:
        if not isinstance(point, SpcPoint) or not isinstance(point.inspection_id, str) or not point.inspection_id.strip():
            raise _invalid("SPC_INPUT_INVALID", "SPC observations require a nonempty inspection ID")
        if point.inspection_id in unique:
            continue
        if not _valid_score(point.score) or type(point.synthetic) is not bool:
            raise _invalid("SPC_INPUT_INVALID", "SPC observations require a finite score in [0, 1] and a boolean synthetic flag")
        unique[point.inspection_id] = replace(point, t=_utc_time(point.t, "SPC_INPUT_INVALID"))
    return sorted(unique.values(), key=attrgetter("t", "inspection_id"))


def _freeze(points: list[SpcPoint], min_n: int) -> SpcBaseline | None:
    if len(points) < min_n:
        return None
    scores = [point.score for point in points[:min_n]]
    return _sample_baseline(scores, points[min_n - 1].t)


def _sample_baseline(scores: list[float], frozen_at: datetime) -> SpcBaseline:
    # Sample standard deviation uses n - 1 degrees of freedom.
    return SpcBaseline(len(scores), float(mean(scores)), stdev(scores), frozen_at)


def freeze_baseline(points: list[SpcPoint], rule: SpcRule) -> SpcBaseline | None:
    """Freeze the first min_n distinct observations supplied for this context."""
    _validate_rule(rule)
    return _freeze(_ordered_points(points), rule.min_n)


def _resolve_baseline(points: list[SpcPoint], baseline: SpcBaseline | None, min_n: int) -> SpcBaseline:
    if baseline is not None:
        if (
            not isinstance(baseline, SpcBaseline)
            or type(baseline.n) is not int
            or baseline.n < 0
            or (baseline.mean is not None and not _valid_score(baseline.mean))
            or (baseline.std is not None and (not _finite(baseline.std) or baseline.std < 0))
        ):
            raise _invalid("SPC_BASELINE_INVALID", "SPC baseline has invalid sample statistics")
        if baseline.frozen_at is not None:
            if baseline.n < 2 or baseline.mean is None or baseline.std is None:
                raise _invalid("SPC_BASELINE_INVALID", "A frozen SPC baseline requires observations, a mean, and a standard deviation")
            return replace(baseline, frozen_at=_utc_time(baseline.frozen_at, "SPC_BASELINE_INVALID"))

    frozen = _freeze(points, min_n)
    if frozen is not None:
        return frozen
    if baseline is not None and baseline.n >= len(points):
        if baseline.n >= min_n:
            raise _invalid("SPC_BASELINE_INVALID", "SPC baseline reached its sample size without a frozen cutoff")
        return baseline
    return SpcBaseline(len(points), None, None, None)


def _triggered_rules(cusum: float, streak: int, h_sigma: float) -> tuple[RuleName, ...]:
    rules: list[RuleName] = []
    if cusum > h_sigma:
        rules.append("CUSUM_UPPER")
    if streak >= FIVE_RISING_COUNT:
        rules.append("FIVE_RISING")
    return tuple(rules)


def _warning(inspection_id: str, index: int, cusum: float, streak: int, h_sigma: float) -> dict | None:
    rules = _triggered_rules(cusum, streak, h_sigma)
    if not rules:
        return None
    reasons = {
        "CUSUM_UPPER": f"upper CUSUM {cusum:.4g}σ > {h_sigma:.4g}σ",
        "FIVE_RISING": "five consecutive strictly rising scores",
    }
    return {
        "inspection_id": inspection_id,
        "at_index": index,
        "rules": list(rules),
        "message": f"Suspected drift ({' and '.join(reasons[name] for name in rules)})",
    }


def _z_score(score: float, baseline: SpcBaseline) -> float:
    z = (score - baseline.mean) / baseline.std
    if not isfinite(z):
        raise _invalid("SPC_BASELINE_INVALID", "SPC normalization exceeds the finite numeric range")
    return z


@dataclass
class _Monitor:
    cusum: float = 0.0
    streak: int = 0
    previous_score: float | None = None

    def update(self, score: float, z: float, rule: SpcRule) -> None:
        self.cusum = max(0.0, self.cusum + z - rule.k_sigma)
        if not isfinite(self.cusum):
            raise _invalid("SPC_BASELINE_INVALID", "SPC accumulation exceeds the finite numeric range")
        self.streak = self.streak + 1 if self.previous_score is not None and score > self.previous_score else 1
        self.previous_score = score


def compute_series(points: list[SpcPoint], baseline: SpcBaseline | None, rule: SpcRule) -> Series:
    """Calculate normalized points and drift warnings using a supplied frozen baseline when available."""
    _validate_rule(rule)
    ordered = _ordered_points(points)
    baseline = _resolve_baseline(ordered, baseline, rule.min_n)
    status = "COLLECTING"
    monitoring_start = len(ordered)
    if baseline.frozen_at is not None and baseline.n >= rule.min_n:
        status = "LOW_VARIANCE" if baseline.std == 0 or baseline.std < rule.min_std else "READY"
        # All observations before the cutoff are calibration-period points. At a tied cutoff,
        # consume the available portion of the frozen sample in deterministic ID order.
        earlier = bisect_left(ordered, baseline.frozen_at, key=attrgetter("t"))
        through_cutoff = bisect_right(ordered, baseline.frozen_at, key=attrgetter("t"))
        monitoring_start = max(earlier, min(baseline.n, through_cutoff))

    warnings, series_points = [], []
    monitor = _Monitor()
    for index, point in enumerate(ordered):
        z, point_cusum = None, None
        if status == "READY":
            z = _z_score(point.score, baseline)
            if index >= monitoring_start:
                monitor.update(point.score, z, rule)
                warning = _warning(point.inspection_id, index, monitor.cusum, monitor.streak, rule.h_sigma)
                if warning is not None:
                    warnings.append(warning)
            point_cusum = monitor.cusum
        series_points.append({
            "inspection_id": point.inspection_id,
            "t": _iso_z(point.t),
            "score": point.score,
            "z": z,
            "cusum": point_cusum,
            "synthetic": point.synthetic,
        })

    return Series(
        baseline={
            "status": status,
            "n": baseline.n,
            "mean": baseline.mean,
            "std": baseline.std,
            "frozen_at": _iso_z(baseline.frozen_at),
        },
        rule={
            "type": "CUSUM_UPPER",
            "k_sigma": rule.k_sigma,
            "h_sigma": rule.h_sigma,
            "min_n": rule.min_n,
            "also": ["FIVE_RISING"],
        },
        points=series_points,
        warnings=warnings,
    )


def _has_id(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def _event_order(event: InvestigationEvent) -> tuple[datetime, int, str]:
    if isinstance(event, InvestigationGap):
        return event.t, 0, event.observation_id
    return event.t, 1, event.inspection_id


def _investigation_events(events: Sequence[InvestigationEvent]) -> list[InvestigationEvent]:
    if not isinstance(events, Sequence) or isinstance(events, (str, bytes)):
        raise _invalid("SPC_INPUT_INVALID", "Investigation requires a sequence of events")
    observations: dict[str, InvestigationEvent] = {}
    inspection_ids: set[str] = set()
    for event in events:
        if not isinstance(event, (InvestigationObservation, InvestigationGap)) or not _has_id(event.observation_id):
            raise _invalid("SPC_INPUT_INVALID", "Investigation events require a nonempty observation ID")
        event = replace(event, t=_utc_time(event.t, "SPC_INPUT_INVALID"))
        if isinstance(event, InvestigationObservation):
            if (
                not _has_id(event.inspection_id)
                or any(value is not None and not _has_id(value) for value in (event.unit_id, event.profile_id))
                or (event.reference_sha256 is not None and not _is_sha256(event.reference_sha256))
                or (event.score is not None and not _valid_score(event.score))
                or type(event.calibrated) is not bool
                or type(event.provenance_verified) is not bool
                or (event.routine is not None and type(event.routine) is not bool)
                or event.evidence_source not in (None, "LIVE_CAPTURE", "PRERECORDED_EVAL", "SYNTHETIC_TEST")
            ):
                raise _invalid("SPC_INPUT_INVALID", "Invalid observation metadata or score for SPC investigation")
        elif not _has_id(event.reason):
            raise _invalid("SPC_INPUT_INVALID", "A declared investigation gap requires a reason")
        previous = observations.get(event.observation_id)
        if previous == event:
            continue
        if previous is not None:
            raise _invalid("SPC_INPUT_INVALID", "Conflicting events share an observation ID")
        if isinstance(event, InvestigationObservation):
            if event.inspection_id in inspection_ids:
                raise _invalid("SPC_INPUT_INVALID", "An inspection ID maps to multiple observations")
            inspection_ids.add(event.inspection_id)
        observations[event.observation_id] = event
    return sorted(observations.values(), key=_event_order)


def _exclusion(event: InvestigationObservation, context: InvestigationContext, primary_units: set[str]) -> IssueCode | None:
    if event.evidence_source in ("PRERECORDED_EVAL", "SYNTHETIC_TEST"):
        return "NON_PRODUCTION_SOURCE"
    if event.routine is False:
        return "REINSPECTION"
    if event.routine is None:
        return "ROUTINE_STATUS_UNKNOWN"
    if event.unit_id is None:
        return "UNIT_ID_MISSING"
    if event.unit_id in primary_units:
        return "UNIT_ALREADY_OBSERVED"
    # Reserve a known routine attempt even if it produced unusable evidence.
    primary_units.add(event.unit_id)
    if event.profile_id is None or event.reference_sha256 is None:
        return "CALIBRATION_CONTEXT_MISSING"
    if (event.profile_id, event.reference_sha256) != (context.profile_id, context.reference_sha256):
        return "CALIBRATION_CONTEXT_CHANGED"
    if not event.calibrated:
        return "UNCALIBRATED"
    if event.evidence_source != "LIVE_CAPTURE" or not event.provenance_verified:
        return "CAPTURE_PROVENANCE_UNVERIFIED"
    if event.score is None:
        return "SCORE_MISSING"
    return None


def investigate(
    context: InvestigationContext,
    events: Sequence[InvestigationEvent],
    target_inspection_id: str,
    rule: SpcRule,
) -> Investigation:
    """Explain a review-only candidate from a committed inspection prefix for one part/station/method.

    Use inspection record time and inspection ID, with declared gaps first at tied times.
    For a replay, pass the original context, rule and immutable event prefix. Historical chart
    points and baselines lack provenance and serial membership for this versioned analysis.
    """
    _validate_rule(rule)
    if (
        not isinstance(context, InvestigationContext)
        or not all(_has_id(value) for value in (context.part_id, context.station_id, context.method_version, context.profile_id, target_inspection_id))
        or not _is_sha256(context.reference_sha256)
    ):
        raise _invalid("SPC_INPUT_INVALID", "Investigation requires a chart context, calibrated reference identity, and target inspection ID")

    issues = [InvestigationIssue("PRODUCTION_COVERAGE_UNKNOWN"), InvestigationIssue("BASELINE_STABILITY_UNVERIFIED")]
    ordered = _investigation_events(events)
    target_index = next((index for index, event in enumerate(ordered)
                         if isinstance(event, InvestigationObservation) and event.inspection_id == target_inspection_id), None)
    prefix = [] if target_index is None else ordered[:target_index + 1]
    if target_index is None:
        issues.append(InvestigationIssue("TARGET_NOT_FOUND", inspection_id=target_inspection_id))
    primary_units: set[str] = set()
    reference: list[InvestigationObservation] = []
    baseline = None
    monitor = _Monitor()
    excursion: list[InvestigationMeasurement] = []
    rising: list[InvestigationMeasurement] = []
    previous_zero = None
    trigger = None

    for event in prefix:
        if isinstance(event, InvestigationGap):
            code = "DECLARED_GAP"
            issues.append(InvestigationIssue(code, event.observation_id, detail=event.reason))
        else:
            code = _exclusion(event, context, primary_units)
            if code is not None:
                issues.append(InvestigationIssue(code, event.observation_id, event.inspection_id))
        if code is not None:
            if code in ("NON_PRODUCTION_SOURCE", "REINSPECTION", "UNIT_ALREADY_OBSERVED"):
                continue
            if baseline is None or code == "CALIBRATION_CONTEXT_CHANGED":
                reference.clear()
                baseline = None
            monitor = _Monitor()
            excursion.clear()
            rising.clear()
            previous_zero = None
            continue

        assert isinstance(event, InvestigationObservation) and event.score is not None
        if baseline is None:
            reference.append(event)
            if len(reference) == rule.min_n:
                baseline = _sample_baseline([cast(float, row.score) for row in reference], event.t)
            continue
        if baseline.std == 0 or baseline.std < rule.min_std:
            continue

        z = _z_score(event.score, baseline)
        monitor.update(event.score, z, rule)
        measured = InvestigationMeasurement(event, z, monitor.cusum)
        if monitor.cusum > 0:
            excursion.append(measured)
        else:
            excursion.clear()
            previous_zero = event.observation_id
        if monitor.streak == 1:
            rising.clear()
        rising.append(measured)
        if event.inspection_id == target_inspection_id:
            trigger = measured

    cusum = None
    five_rising = None
    rules: tuple[RuleName, ...] = ()
    if trigger is not None:
        rules = _triggered_rules(monitor.cusum, monitor.streak, rule.h_sigma)
        if "CUSUM_UPPER" in rules:
            cusum = CusumEvidence(excursion[0].observation.observation_id, previous_zero, tuple(excursion))
        if "FIVE_RISING" in rules:
            five_rising = FiveRisingEvidence(rising[0].observation.observation_id, tuple(rising))
    if baseline is None:
        stats = SpcBaseline(len(reference), None, None, None)
        baseline_status = "COLLECTING"
    else:
        stats = baseline
        baseline_status = "LOW_VARIANCE" if baseline.std == 0 or baseline.std < rule.min_std else "READY"
    status = "INSUFFICIENT_EVIDENCE" if trigger is None else "SIGNAL" if rules else "NO_SIGNAL"
    return Investigation(context, target_inspection_id, status, InvestigationBaseline(baseline_status, stats, tuple(reference)),
                         rule, tuple(issues), trigger, rules, cusum, five_rising)


CONTAINMENT_RECOMMENDATION_VERSION = "spc-containment-1"
RecommendationAction = Literal["RECONCILE_GENEALOGY", "REVIEW_HISTORICAL_CONTAINMENT", "INVESTIGATE_ONGOING_EXPOSURE"]


@dataclass(frozen=True)
class LotGenealogy:
    lot_id: str
    part_id: str | None
    line_id: str | None


@dataclass(frozen=True)
class UnitGenealogy:
    unit_id: str
    lot_id: str | None


@dataclass(frozen=True)
class ExposureEvidence:
    evidence_id: str
    basis: Literal["PROCESS_RECORD", "QUALITY_INVESTIGATION"]


@dataclass(frozen=True)
class LotExposureLink:
    lot_id: str
    evidence: ExposureEvidence


@dataclass(frozen=True)
class OngoingExposureLink:
    part_id: str
    line_id: str
    evidence: ExposureEvidence


@dataclass(frozen=True)
class FourMChange:
    event_id: str
    line_id: str
    effective_at: datetime
    category: Literal["MAN", "MACHINE", "MATERIAL", "METHOD"]


@dataclass(frozen=True)
class ContainmentGenealogy:
    """Opus supplies the resolved station-to-line mapping and a bounded genealogy read.

    Null means unknown. Exposure links cite independently checked evidence of a shared
    exposure. A date, common heat, nearby 4M record or PASS alone cannot establish a link.
    FourM records are preselected investigation leads, and carry no exposure assertion.
    """

    line_id: str | None
    lots: tuple[LotGenealogy, ...] = ()
    units: tuple[UnitGenealogy, ...] = ()
    lot_exposure: tuple[LotExposureLink, ...] = ()
    ongoing_exposure: tuple[OngoingExposureLink, ...] = ()
    four_m: tuple[FourMChange, ...] = ()


@dataclass(frozen=True)
class ConfirmedObservation:
    observation_id: str
    inspection_id: str
    unit_id: str
    rules: tuple[RuleName, ...]


@dataclass(frozen=True)
class ProposedLotReview:
    lot_id: str
    recorded_unit_ids: tuple[str, ...]
    basis: tuple[Literal["SIGNAL_CONTRIBUTOR", "INDEPENDENT_EXPOSURE_LINK"], ...]
    supporting_inspection_ids: tuple[str, ...]
    independent_evidence: tuple[ExposureEvidence, ...]


@dataclass(frozen=True)
class ScopeFinding:
    entity_type: Literal["UNIT", "LOT", "FOUR_M"]
    entity_id: str
    reason: Literal[
        "UNIT_LOT_UNKNOWN", "UNIT_LOT_CONFLICT", "LOT_NOT_IN_ROSTER", "LOT_CONTEXT_UNKNOWN", "LOT_GENEALOGY_CONFLICT",
        "LOT_EVIDENCE_CONTEXT_CONFLICT", "LOT_EXPOSURE_UNCONFIRMED", "OUTSIDE_CHART_PART_OR_LINE", "FOUR_M_RECORD_CONFLICT",
    ]
    recorded_lot_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ContainmentRecommendation:
    target_inspection_id: str
    context: InvestigationContext
    line_id: str | None
    investigation_status: Literal["SIGNAL", "NO_SIGNAL", "INSUFFICIENT_EVIDENCE"]
    investigation_issues: tuple[InvestigationIssue, ...]
    status: Literal["REVIEW_RECOMMENDED", "SCOPE_UNRESOLVED", "NO_SIGNAL", "INSUFFICIENT_EVIDENCE"]
    confirmed_observations: tuple[ConfirmedObservation, ...] = ()
    proposed_lots: tuple[ProposedLotReview, ...] = ()
    unresolved: tuple[ScopeFinding, ...] = ()
    excluded: tuple[ScopeFinding, ...] = ()
    related_four_m: tuple[FourMChange, ...] = ()
    ongoing_evidence: tuple[ExposureEvidence, ...] = ()
    limitations: tuple[str, ...] = ("PRODUCTION_CHRONOLOGY_UNAVAILABLE", "TOTAL_EXPOSURE_UNKNOWN")
    actions: tuple[RecommendationAction, ...] = ()
    qualification_version: str = INVESTIGATION_QUALIFICATION_VERSION
    recommendation_version: str = CONTAINMENT_RECOMMENDATION_VERSION


def _confirmed_observations(investigation: Investigation) -> tuple[ConfirmedObservation, ...]:
    observations: dict[str, InvestigationObservation] = {}
    contribution: dict[str, set[RuleName]] = defaultdict(set)
    evidence_by_rule: dict[RuleName, CusumEvidence | FiveRisingEvidence | None] = {
        "CUSUM_UPPER": investigation.cusum, "FIVE_RISING": investigation.five_rising,
    }
    for rule in investigation.rules:
        evidence = evidence_by_rule[rule]
        if evidence is None:
            raise _invalid("SPC_INPUT_INVALID", "Qualified investigation is missing the evidence for a triggered rule")
        for measurement in evidence.contributors:
            observation = measurement.observation
            prior = observations.setdefault(observation.inspection_id, observation)
            if observation != prior or observation.unit_id is None:
                raise _invalid("SPC_INPUT_INVALID", "Qualified investigation contains inconsistent contributor identity")
            contribution[observation.inspection_id].add(rule)
    trigger = investigation.trigger
    if trigger is None or trigger.observation.inspection_id != investigation.target_inspection_id or (
        observations.get(investigation.target_inspection_id) != trigger.observation
    ):
        raise _invalid("SPC_INPUT_INVALID", "Qualified investigation is missing its triggering observation")
    return tuple(
        ConfirmedObservation(row.observation_id, row.inspection_id, cast(str, row.unit_id),
                             tuple(rule for rule in investigation.rules if rule in contribution[row.inspection_id]))
        for row in sorted(observations.values(), key=attrgetter("t", "inspection_id"))
    )


def _evidence_order(evidence: set[ExposureEvidence]) -> tuple[ExposureEvidence, ...]:
    return tuple(sorted(evidence, key=attrgetter("evidence_id", "basis")))


def _validate_genealogy(genealogy: ContainmentGenealogy, contextual_ids: set[str]) -> None:
    required_ids = (
        *(lot.lot_id for lot in genealogy.lots),
        *(unit.unit_id for unit in genealogy.units),
        *(link.lot_id for link in genealogy.lot_exposure),
        *(value for link in genealogy.ongoing_exposure for value in (link.part_id, link.line_id)),
        *(value for event in genealogy.four_m for value in (event.event_id, event.line_id)),
    )
    optional_ids = (
        genealogy.line_id, *(value for lot in genealogy.lots for value in (lot.part_id, lot.line_id)),
        *(unit.lot_id for unit in genealogy.units),
    )
    if not all(map(_has_id, required_ids)) or any(value is not None and not _has_id(value) for value in optional_ids):
        raise _invalid("SPC_INPUT_INVALID", "Genealogy requires nonempty IDs. Only null denotes an unknown mapping.")
    basis_by_id: dict[str, str] = {}
    for link in (*genealogy.lot_exposure, *genealogy.ongoing_exposure):
        evidence = link.evidence
        if not _has_id(evidence.evidence_id) or evidence.basis not in ("PROCESS_RECORD", "QUALITY_INVESTIGATION"):
            raise _invalid("SPC_INPUT_INVALID", "Independent exposure evidence requires a record ID and evidence basis")
        if evidence.evidence_id in contextual_ids:
            raise _invalid("SPC_INPUT_INVALID", "A chart inspection or 4M record alone cannot corroborate exposure")
        if basis_by_id.setdefault(evidence.evidence_id, evidence.basis) != evidence.basis:
            raise _invalid("SPC_INPUT_INVALID", "An independent exposure record has conflicting evidence bases")


def recommend_containment(investigation: Investigation, genealogy: ContainmentGenealogy) -> ContainmentRecommendation:
    """Propose a human review from PR #31 evidence and caller-supplied genealogy.

    A recorded inspection confirms an observation, not a defective unit. Known
    memberships support precautionary whole-lot review. The returned members
    are a partial roster and neither inspection order nor 4M time bounds production.
    """
    status = "NO_SIGNAL" if investigation.status == "NO_SIGNAL" else "INSUFFICIENT_EVIDENCE"
    result = ContainmentRecommendation(
        investigation.target_inspection_id, investigation.context, genealogy.line_id, investigation.status,
        investigation.issues, status, qualification_version=investigation.qualification_version,
    )
    if investigation.qualification_version != INVESTIGATION_QUALIFICATION_VERSION:
        return replace(result, status="INSUFFICIENT_EVIDENCE",
                       limitations=(*result.limitations, "INVESTIGATION_VERSION_UNSUPPORTED"))
    if investigation.status != "SIGNAL":
        return result
    confirmed = _confirmed_observations(investigation)
    _validate_genealogy(genealogy, {
        *(row.inspection_id for row in confirmed), *(row.inspection_id for row in investigation.baseline.observations),
        *(row.event_id for row in genealogy.four_m),
    })
    lot_records: dict[str, set[LotGenealogy]] = defaultdict(set)
    memberships: dict[str, set[str | None]] = defaultdict(set)
    related: dict[str, set[FourMChange]] = defaultdict(set)
    independent: dict[str, set[ExposureEvidence]] = defaultdict(set)
    for lot in genealogy.lots:
        lot_records[lot.lot_id].add(lot)
    for unit in genealogy.units:
        memberships[unit.unit_id].add(unit.lot_id)
    for event in genealogy.four_m:
        related[event.event_id].add(replace(event, effective_at=_utc_time(event.effective_at, "SPC_INPUT_INVALID")))
    for link in genealogy.lot_exposure:
        independent[link.lot_id].add(link.evidence)

    unresolved, excluded, proposed = [], [], []
    members: dict[str, list[str]] = defaultdict(list)
    observed: dict[str, set[str]] = defaultdict(set)
    cited_unit_ids = {row.unit_id for row in confirmed}
    disputed_lots: set[str] = set()
    for unit_id in sorted(memberships.keys() | cited_unit_ids):
        lot_ids = memberships.get(unit_id, set())
        if len(lot_ids) > 1:
            recorded_lot_ids = tuple(sorted(lot for lot in lot_ids if lot is not None))
            unresolved.append(ScopeFinding("UNIT", unit_id, "UNIT_LOT_CONFLICT", recorded_lot_ids))
            if unit_id in cited_unit_ids:
                disputed_lots.update(recorded_lot_ids)
        elif not lot_ids or lot_ids == {None}:
            unresolved.append(ScopeFinding("UNIT", unit_id, "UNIT_LOT_UNKNOWN"))
        else:
            lot_id = cast(str, next(iter(lot_ids)))
            members[lot_id].append(unit_id)
    unit_to_lot = {unit_id: lot_id for lot_id, unit_ids in members.items() for unit_id in unit_ids}
    for row in confirmed:
        if row.unit_id in unit_to_lot:
            observed[unit_to_lot[row.unit_id]].add(row.inspection_id)

    part_id, line_id = investigation.context.part_id, genealogy.line_id
    for lot_id in sorted(lot_records.keys() | members.keys() | independent.keys() | disputed_lots):
        rows = lot_records.get(lot_id, set())
        if not rows:
            unresolved.append(ScopeFinding("LOT", lot_id, "LOT_NOT_IN_ROSTER"))
            continue
        if len(rows) > 1:
            unresolved.append(ScopeFinding("LOT", lot_id, "LOT_GENEALOGY_CONFLICT"))
            continue
        lot = next(iter(rows))
        if line_id is None or lot.line_id is None or lot.part_id is None:
            unresolved.append(ScopeFinding("LOT", lot_id, "LOT_CONTEXT_UNKNOWN"))
        elif (lot.part_id, lot.line_id) != (part_id, line_id):
            if lot_id in observed or lot_id in independent or lot_id in disputed_lots:
                unresolved.append(ScopeFinding("LOT", lot_id, "LOT_EVIDENCE_CONTEXT_CONFLICT"))
            else:
                excluded.append(ScopeFinding("LOT", lot_id, "OUTSIDE_CHART_PART_OR_LINE"))
        elif lot_id in observed or lot_id in independent:
            basis: list[Literal["SIGNAL_CONTRIBUTOR", "INDEPENDENT_EXPOSURE_LINK"]] = []
            if lot_id in observed:
                basis.append("SIGNAL_CONTRIBUTOR")
            if lot_id in independent:
                basis.append("INDEPENDENT_EXPOSURE_LINK")
            proposed.append(ProposedLotReview(
                lot_id, tuple(members[lot_id]), tuple(basis), tuple(sorted(observed[lot_id])), _evidence_order(independent[lot_id]),
            ))
        else:
            unresolved.append(ScopeFinding("LOT", lot_id, "LOT_EXPOSURE_UNCONFIRMED"))

    changes = []
    for event_id, rows in sorted(related.items()):
        if not any(line_id == row.line_id for row in rows):
            continue
        if len(rows) > 1:
            unresolved.append(ScopeFinding("FOUR_M", event_id, "FOUR_M_RECORD_CONFLICT"))
        else:
            changes.append(next(iter(rows)))
    ongoing = _evidence_order({link.evidence for link in genealogy.ongoing_exposure
                               if (link.part_id, link.line_id) == (part_id, line_id)})
    limitations = result.limitations
    if line_id is None:
        limitations = (*limitations, "STATION_LINE_MAPPING_UNKNOWN")
    actions: list[RecommendationAction] = ["RECONCILE_GENEALOGY"]
    if proposed:
        actions.append("REVIEW_HISTORICAL_CONTAINMENT")
    if ongoing:
        actions.append("INVESTIGATE_ONGOING_EXPOSURE")
    return replace(
        result, status="REVIEW_RECOMMENDED" if proposed else "SCOPE_UNRESOLVED", confirmed_observations=confirmed,
        proposed_lots=tuple(proposed), unresolved=tuple(unresolved), excluded=tuple(excluded),
        related_four_m=tuple(sorted(changes, key=attrgetter("effective_at", "event_id"))),
        ongoing_evidence=ongoing, limitations=limitations, actions=tuple(actions),
    )
