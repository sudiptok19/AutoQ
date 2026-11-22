from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core import errors
from app.core.auth import Principal, current_principal
from app.db.session import get_db
from app.features.spc.router import router
from app.features.spc.service import compute_series, freeze_baseline
from app.ports import SpcBaseline, SpcPoint, SpcRule, ports

pytestmark = pytest.mark.nodb

START = datetime(2026, 11, 22, 12, tzinfo=UTC)
RULE = SpcRule(k_sigma=0.5, h_sigma=1, min_n=3)
BASE_SCORES = [0.25, 0.5, 0.75]


def _synthetic(scores):
    return [SpcPoint(f"insp-{i:02d}", START + timedelta(seconds=i), score, True) for i, score in enumerate(scores)]


def test_hand_computed_cusum_crossing_uses_sample_standard_deviation():
    points = _synthetic([*BASE_SCORES, 0.625, 0.875, 1.0])
    baseline = freeze_baseline(points, RULE)
    assert baseline == SpcBaseline(3, 0.5, 0.25, points[2].t)
    series = compute_series(points, baseline, RULE)
    assert series.baseline == {"status": "READY", "n": 3, "mean": 0.5, "std": 0.25, "frozen_at": "2026-11-22T12:00:02Z"}
    assert series.rule == {"type": "CUSUM_UPPER", "k_sigma": 0.5, "h_sigma": 1, "min_n": 3, "also": ["FIVE_RISING"]}
    assert [p["z"] for p in series.points] == [-1, 0, 1, 0.5, 1.5, 2]
    assert [p["cusum"] for p in series.points] == [0, 0, 0, 0, 1, 2.5]
    assert all(p["synthetic"] for p in series.points)
    (warning,) = series.warnings
    assert (warning["at_index"], warning["inspection_id"], warning["rules"]) == (5, "insp-05", ["CUSUM_UPPER"])
    assert "suspected drift" in warning["message"].lower()


def test_stable_series_stays_below_warning_threshold():
    series = compute_series(_synthetic([*BASE_SCORES, *([0.5, 0.625, 0.375, 0.5] * 4)]), None, RULE)
    assert series.baseline["status"] == "READY"
    assert all(point["cusum"] == 0 for point in series.points)
    assert series.warnings == []


def test_cold_start_preserves_raw_scores_without_normalized_values():
    points = _synthetic([0.2, 0.4])
    assert freeze_baseline(points, RULE) is None
    series = compute_series(points, None, RULE)
    assert series.baseline == {"status": "COLLECTING", "n": 2, "mean": None, "std": None, "frozen_at": None}
    assert [point["score"] for point in series.points] == [0.2, 0.4]
    assert all(point["z"] is point["cusum"] is None for point in series.points)
    assert series.warnings == []


@pytest.mark.parametrize(("scores", "min_std"), [([0.5] * 3, 0), ([0.499, 0.5, 0.501], 0.005)])
def test_low_variance_leaves_normalization_unavailable(scores, min_std):
    rule = replace(RULE, min_std=min_std)
    points = _synthetic([*scores, 0.6, 0.7, 0.8, 0.9, 1.0])
    baseline = freeze_baseline(points, rule)
    assert baseline is not None
    series = compute_series(points, baseline, rule)
    assert series.baseline["status"] == "LOW_VARIANCE"
    assert series.baseline["std"] == baseline.std
    assert all(point["z"] is point["cusum"] is None for point in series.points)
    assert series.warnings == []


def test_deduplication_keeps_first_observation_then_normalizes_and_sorts_timestamps():
    first, second, third = _synthetic(BASE_SCORES)
    offset = timezone(timedelta(hours=5, minutes=30))
    third_with_offset = replace(third, t=third.t.astimezone(offset))
    later_duplicate_at_earlier_time = replace(third, t=START - timedelta(seconds=1), score=0.9)
    points = [third_with_offset, later_duplicate_at_earlier_time, second, first]
    series = compute_series(points, None, RULE)
    assert series == compute_series([first, second, third], None, RULE)
    assert [point["inspection_id"] for point in series.points] == [first.inspection_id, second.inspection_id, third.inspection_id]
    assert series.points[2]["t"] == "2026-11-22T12:00:02Z"


def test_five_rising_uses_only_post_baseline_points_and_resets_on_ties():
    series = compute_series(_synthetic([*BASE_SCORES, 0.1, 0.2, 0.3, 0.3, 0.4, 0.5, 0.6, 0.7]), None, replace(RULE, h_sigma=100))
    assert [(warning["at_index"], warning["rules"]) for warning in series.warnings] == [(10, ["FIVE_RISING"])]

    five_including_baseline = compute_series(_synthetic([*BASE_SCORES, 0.8, 0.9]), None, replace(RULE, h_sigma=100))
    assert five_including_baseline.warnings == []


def test_both_rules_produce_one_warning_for_the_inspection():
    points = _synthetic([*BASE_SCORES, 0.75, 0.8, 0.85, 0.9, 0.95])
    series = compute_series(points, None, replace(RULE, h_sigma=4))
    (warning,) = series.warnings
    assert (warning["at_index"], warning["inspection_id"], warning["rules"]) == (7, "insp-07", ["CUSUM_UPPER", "FIVE_RISING"])
    assert series.points[-1]["cusum"] == pytest.approx(4.5)


def test_persisted_baseline_handles_a_trimmed_history_without_refreezing_it():
    points = _synthetic([*BASE_SCORES, 0.875, 1.0])
    baseline = freeze_baseline(points, RULE)
    trimmed = compute_series(points[-2:], baseline, RULE)
    full = compute_series(points, baseline, RULE)
    assert trimmed.baseline == full.baseline
    assert trimmed.points == full.points[-2:]
    assert trimmed.warnings[0]["at_index"] == 1
    assert trimmed.warnings[0]["inspection_id"] == full.warnings[0]["inspection_id"]


def test_cutoff_timestamp_ties_use_deterministic_id_order_with_frozen_baseline():
    first, second, third, tied_post, later = _synthetic([*BASE_SCORES, 0.875, 1.0])
    second = replace(second, t=third.t)
    tied_post = replace(tied_post, t=third.t)
    unordered = [tied_post, second, first, third, later]
    baseline = freeze_baseline(unordered, RULE)
    assert baseline == SpcBaseline(3, 0.5, 0.25, third.t)
    series = compute_series(unordered, baseline, RULE)
    assert [point["cusum"] for point in series.points] == [0, 0, 0, 1, 2.5]
    assert series == compute_series(list(reversed(unordered)), None, RULE)

    trimmed = compute_series([tied_post, later], baseline, RULE)
    assert trimmed.baseline == series.baseline
    assert [point["cusum"] for point in trimmed.points] == [0, 1.5]


def test_persisted_sample_below_min_n_remains_collecting_even_when_stats_exist():
    points = _synthetic([*BASE_SCORES, 0.9, 1.0])
    baseline = freeze_baseline(points, RULE)
    series = compute_series(points, baseline, replace(RULE, min_n=4))
    assert series.baseline["status"] == "COLLECTING"
    assert series.baseline["n"] == 3
    assert all(point["z"] is point["cusum"] is None for point in series.points)


@pytest.mark.parametrize(
    "changes",
    [
        {"k_sigma": 0}, {"k_sigma": float("nan")}, {"k_sigma": True},
        {"h_sigma": -1}, {"h_sigma": float("inf")},
        {"min_n": 1}, {"min_n": 3.0},
        {"min_std": -0.01}, {"min_std": float("nan")}, {"min_std": float("inf")},
    ],
)
def test_nonfinite_or_invalid_rule_fails_closed(changes):
    rule = replace(RULE, **changes)
    for evaluate in (lambda: freeze_baseline([], rule), lambda: compute_series([], None, rule)):
        with pytest.raises(errors.ApiError) as error:
            evaluate()
        assert (error.value.status, error.value.code) == (422, "SPC_RULE_INVALID")


@pytest.mark.parametrize(
    "changes",
    [
        {"score": float("nan")}, {"score": float("inf")}, {"score": 1.1}, {"score": -0.1}, {"score": False},
        {"score": "0.4"}, {"t": START.replace(tzinfo=None)}, {"inspection_id": ""}, {"synthetic": 1},
    ],
)
def test_invalid_observations_are_explicit_errors(changes):
    points = _synthetic([0.4, 0.5])
    points[0] = replace(points[0], **changes)
    with pytest.raises(errors.ApiError) as error:
        compute_series(points, None, RULE)
    assert (error.value.status, error.value.code) == (422, "SPC_INPUT_INVALID")


@pytest.mark.parametrize("changes", [{"mean": float("nan")}, {"std": -1}, {"std": None}, {"frozen_at": START.replace(tzinfo=None)}])
def test_invalid_persisted_baseline_is_not_recomputed(changes):
    points = _synthetic(BASE_SCORES)
    baseline = replace(SpcBaseline(3, 0.5, 0.25, points[-1].t), **changes)
    with pytest.raises(errors.ApiError) as error:
        compute_series(points, baseline, RULE)
    assert error.value.code == "SPC_BASELINE_INVALID"


def _client(persistence, principal=None):
    app = FastAPI()
    errors.install(app)
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[ports] = lambda: persistence
    app.dependency_overrides[get_db] = lambda: None
    if principal is not None:
        app.dependency_overrides[current_principal] = lambda: principal
    return TestClient(app)


def test_series_route_uses_authenticated_context_and_only_persisted_alerts():
    principal = Principal("u1", "ravi", "Ravi", ("operator",))
    known = _synthetic([*BASE_SCORES, 0.875, 1.0])
    new_method = [replace(_synthetic([0.6])[0], inspection_id="new-method")]
    persisted_alerts = [{"id": "alert_stored", "at_index": 1, "message": "Suspected drift (recorded)"}]

    class FakePort:
        def load_spc_inputs(self, db, part_id, line_id, method_version, actor):
            assert db is None and actor is principal
            assert (part_id, line_id) == ("part1", "line1")
            if method_version == "gd-0.3":
                return known, freeze_baseline(known, RULE), RULE, persisted_alerts
            assert method_version == "gd-0.4"
            return new_method, None, RULE, []

    with _client(FakePort(), principal) as client:
        base_query = {"part_id": "part1", "line_id": "line1"}
        ready = client.get("/api/v1/spc/series", params={**base_query, "method_version": "gd-0.3"})
        cold = client.get("/api/v1/spc/series", params={**base_query, "method_version": "gd-0.4"})
        missing_method = client.get("/api/v1/spc/series", params=base_query)
    assert ready.status_code == cold.status_code == 200
    assert missing_method.status_code == 422
    expected = compute_series(known, None, RULE)
    assert expected.warnings
    assert ready.json() == {
        **base_query, "method_version": "gd-0.3", "baseline": expected.baseline, "rule": expected.rule,
        "points": expected.points, "alerts": persisted_alerts,
    }
    assert cold.json()["baseline"]["status"] == "COLLECTING"
    assert cold.json()["alerts"] == []
    assert [point["inspection_id"] for point in cold.json()["points"]] == ["new-method"]


def test_series_route_requires_authentication():
    class FakePort:
        def load_spc_inputs(self, *_args):
            pytest.fail("Unauthenticated callers may not load SPC inputs")

    with _client(FakePort()) as client:
        response = client.get("/api/v1/spc/series?part_id=part1&line_id=line1&method_version=gd-0.3")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"
