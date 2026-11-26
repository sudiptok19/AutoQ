"""SPC calculations for one part, station, and inspection method version."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
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
