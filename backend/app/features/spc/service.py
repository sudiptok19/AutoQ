"""SPC calculations for one part, station, and inspection method version."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from math import isfinite
from operator import attrgetter
from statistics import mean, stdev

from app.core.errors import ApiError
from app.ports import SpcBaseline, SpcPoint, SpcRule


@dataclass(frozen=True)
class Series:
    baseline: dict
    rule: dict
    points: list[dict]
    warnings: list[dict]


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
    # Sample standard deviation uses n - 1 degrees of freedom.
    return SpcBaseline(min_n, float(mean(scores)), stdev(scores), points[min_n - 1].t)


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


def _warning(inspection_id: str, index: int, cusum: float, streak: int, h_sigma: float) -> dict | None:
    rules, reasons = [], []
    if cusum > h_sigma:
        rules.append("CUSUM_UPPER")
        reasons.append(f"upper CUSUM {cusum:.4g}σ > {h_sigma:.4g}σ")
    if streak >= 5:
        rules.append("FIVE_RISING")
        reasons.append("five consecutive strictly rising scores")
    if not rules:
        return None
    return {
        "inspection_id": inspection_id,
        "at_index": index,
        "rules": rules,
        "message": f"Suspected drift ({' and '.join(reasons)})",
    }


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
    cusum, streak, previous_score = 0.0, 0, None
    for index, point in enumerate(ordered):
        z, point_cusum = None, None
        if status == "READY":
            z = (point.score - baseline.mean) / baseline.std
            if not isfinite(z):
                raise _invalid("SPC_BASELINE_INVALID", "SPC normalization exceeds the finite numeric range")
            if index >= monitoring_start:
                cusum = max(0.0, cusum + z - rule.k_sigma)
                if not isfinite(cusum):
                    raise _invalid("SPC_BASELINE_INVALID", "SPC accumulation exceeds the finite numeric range")
                streak = streak + 1 if previous_score is not None and point.score > previous_score else 1
                previous_score = point.score
                warning = _warning(point.inspection_id, index, cusum, streak, rule.h_sigma)
                if warning is not None:
                    warnings.append(warning)
            point_cusum = cusum
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
