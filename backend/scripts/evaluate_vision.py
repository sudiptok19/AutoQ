"""Evaluate held-out phone captures with the checkout's backend environment.

By default, read fixtures/images/{inspection,seal}/manifest.json in the checkout
and write datasets/vision_eval.json. Supply --inspection-manifest and/or
--seal-manifest to select sets explicitly. A single-set run updates its section
in a compatible report and preserves the other section. Use --output for a new
report. Relative command-line paths are resolved from the working directory.

Each manifest is {"images": [...], "thresholds": {...}}. Each entry has a
relative file path inside the manifest directory, label, device, lighting,
distance (positive number or description with units), session_id, and
evidence_source ("PRERECORDED_EVAL"). Record these from actual phone captures.
Inspection requires one golden and at least one each of good, defect, blurry,
glare, and misaligned, captured in sessions separate from the golden.
Seal requires one enroll and at least one each of intact_recapture, torn,
repositioned, and bad_light from a patterned seal across a physical box seam.

Choose thresholds before scoring the held-out captures. Thresholds are
optional and provisional. An optional thresholds.roi is [x, y, width, height],
normalized to [0, 1] for inspection or measured in original reference pixels
after orientation correction for seals. The report describes offline
measurements. It grants no production calibration or release decisions.
When reference admission fails, the report preserves the failure and counts
the probes as not evaluated. A standalone check can still assess each input.
Photo hashes use original bytes. Manifest hashes normalize line endings to LF.
For the original physical captures, datasets/vision_eval_protocol.json records
the v2 input-admission baseline and additional offline profiles to evaluate
alongside the default profiles.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import tempfile
from collections import Counter
from dataclasses import asdict, dataclass
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING, Callable

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
sys.path.insert(0, str(BACKEND))

if TYPE_CHECKING:
    from app.core.errors import ApiError


@dataclass(frozen=True)
class Capture:
    path: Path
    label: str
    device: str
    lighting: str
    distance: str | int | float
    session_id: str

    def provenance(self, directory: Path, sha256: str) -> dict:
        return {
            "file": self.path.relative_to(directory).as_posix(),
            "label": self.label,
            "device": self.device,
            "lighting": self.lighting,
            "distance": self.distance,
            "session_id": self.session_id,
            "sha256": sha256,
            "evidence_source": SOURCE,
        }


@dataclass(frozen=True)
class Measurement:
    outcome: str
    score: float | None
    reasons: list[str]
    explanation: str | None = None
    metrics: dict | None = None
    quality_gate: dict | None = None
    classification: str | None = None
    comparison_state: str = "FAILED"
    artifacts: dict[str, bool] | None = None


SOURCE = "PRERECORDED_EVAL"
REPORT_VERSION = 3
PROTOCOL = ROOT / "datasets" / "vision_eval_protocol.json"
LABELS = {
    "inspection": ("golden", "good", "defect", "blurry", "glare", "misaligned"),
    "seal": ("enroll", "intact_recapture", "torn", "repositioned", "bad_light"),
}
INSPECTION_THRESHOLDS = {
    "min_w": 320, "min_h": 240, "blur_min": 80.0, "glare_max": 0.12,
    "min_inliers": 24, "min_inlier_ratio": 0.55, "min_overlap": 0.82,
    "min_coverage": 0.12, "hold": 0.45, "reject": 0.70, "roi": (0, 0, 1, 1),
}
DEFINITIONS = {
    "n": "Number of probe captures. The reference is excluded.",
    "accepted": "Inspection difference below hold, or a seal result of INTACT.",
    "flagged": "Inspection difference at or above hold, or a seal result of SUSPICIOUS.",
    "unverifiable": "No decision due to capture quality, registration, or ambiguous seal similarity.",
    "not_evaluated": "Comparison skipped because the reference could not be admitted. Any standalone assessment applies only to the individual image.",
    "false_accepts": "Accepted probes labelled defect, blurry, glare, misaligned, torn, repositioned, or bad_light.",
    "false_rejects": "Flagged probes labelled good or intact_recapture. Unverifiable probes are counted separately.",
}
INPUT_METADATA_DEFINITION = (
    "Byte lengths and JPEG frame boundaries are read directly from the original files. "
    "JPEG trailing bytes are counted after the primary image's structural end-of-image marker. "
    "Declared width and height are read from the image header without decoding pixels or applying orientation."
)
COMPARISON_STATES = {
    "SCORED": "A comparison produced a numeric score and an accepted or flagged outcome.",
    "AMBIGUOUS": "The seal API returned UNVERIFIABLE with a measured similarity.",
    "FAILED": "An input, quality, or registration failure left the score unavailable.",
    "SKIPPED": "The reference was unusable and the probe comparison did not run.",
}
PERFORMANCE_DEFINITION = (
    "Coverage uses all probe captures as the denominator. Error rates use attempted probes "
    "from the corresponding negative or positive labels. UNVERIFIABLE is counted separately "
    "from false rejects. Score ranges include only comparisons with numeric scores."
)


@dataclass(frozen=True)
class Method:
    version: str
    score_name: str
    thresholds: dict
    input_limits: dict
    reference_assessment: dict
    self_check: dict
    check_probe: Callable[[bytes], dict]
    measure: Callable[[bytes], Measurement]


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def input_metadata(data: bytes) -> dict:
    from PIL import Image

    metadata = {"size_bytes": len(data)}
    if data.startswith(b"\xff\xd8"):
        from app.features.component_vision._jpeg import InvalidJpeg, jpeg_frame

        try:
            frame = jpeg_frame(data)
        except InvalidJpeg:
            metadata["jpeg_trailing_bytes"] = None
        else:
            return {
                **metadata, "format": "JPEG",
                "declared_width": frame.width, "declared_height": frame.height,
                "declared_pixels": frame.width * frame.height,
                "jpeg_trailing_bytes": len(data) - frame.end,
            }
    try:
        with Image.open(BytesIO(data), formats=("JPEG", "PNG")) as header:
            width, height = header.size
            metadata.update(format=header.format, declared_width=width, declared_height=height,
                            declared_pixels=width * height)
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError):
        # A readable header is optional diagnostic data; image admission remains with the service.
        pass
    return metadata


def input_error(exc: ApiError) -> dict:
    return {"code": exc.code, "message": exc.message, "status": exc.status, "details": exc.details}


def artifacts(aligned: bytes | None, diff: bytes | None) -> dict[str, bool]:
    return {"aligned_png": aligned is not None, "diff_png": diff is not None}


def record_comparison(data: bytes, measure: Callable[[bytes], Measurement]) -> dict:
    from app.core.errors import ApiError

    try:
        measurement = measure(data)
    except ApiError as exc:
        if exc.code != "INVALID_IMAGE":
            raise
        reasons = [exc.code, *exc.details.get("reason_codes", [])]
        failure = Measurement("UNVERIFIABLE", None, reasons, exc.message)
        return {**asdict(failure), "error": input_error(exc)}
    return asdict(measurement)


def assess_input(operation: str, check: Callable[[], dict]) -> dict:
    from app.core.errors import ApiError

    try:
        assessment = check()
    except ApiError as exc:
        if exc.code != "INVALID_IMAGE":
            raise
        assessment = {
            "passed": False,
            "reasons": [exc.code, *exc.details.get("reason_codes", [])],
            "error": input_error(exc),
        }
    return {"check": operation, **assessment}


def read_manifest(path: Path, kind: str) -> tuple[Capture, list[Capture], dict, str]:
    raw = path.read_bytes()
    try:
        manifest = json.loads(raw.decode("utf-8-sig"))
    except ValueError as exc:
        raise ValueError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(manifest, dict) or not isinstance(manifest.get("images"), list):
        raise ValueError(f"{path} requires an images array")
    captures = []
    for index, row in enumerate(manifest["images"], start=1):
        entry = f"{path} image {index}"
        if not isinstance(row, dict):
            raise ValueError(f"{entry} must be an object")
        if row.get("evidence_source") != SOURCE:
            raise ValueError(f"{entry} requires {SOURCE} provenance for a physical capture")
        for field in ("file", "label", "device", "lighting", "session_id"):
            if not isinstance(row.get(field), str) or not row[field].strip():
                raise ValueError(f"{entry} requires {field}")
        if row["label"] not in LABELS[kind]:
            raise ValueError(f"{entry} has unknown {kind} label {row['label']!r}")
        distance = row.get("distance")
        described_distance = isinstance(distance, str) and bool(distance.strip())
        numeric_distance = type(distance) in (int, float) and 0 < distance < math.inf
        if not (described_distance or numeric_distance):
            raise ValueError(f"{entry} requires a positive capture distance or description")
        filename = Path(row["file"])
        photo = (path.parent / filename).resolve()
        if filename.anchor or not photo.is_relative_to(path.parent):
            raise ValueError(f"{entry} must use a path relative to its capture directory")
        if not photo.is_file():
            raise FileNotFoundError(f"{entry} file is missing: {photo}")
        captures.append(Capture(photo, row["label"], row["device"], row["lighting"], distance, row["session_id"].strip()))
    required = LABELS[kind]
    counts = Counter(capture.label for capture in captures)
    if counts[required[0]] != 1 or any(counts[label] == 0 for label in required[1:]):
        raise ValueError(f"{path} requires one {required[0]} and at least one capture of each of {', '.join(required[1:])}")
    reference = next(c for c in captures if c.label == required[0])
    probes = [c for c in captures if c is not reference]
    if kind == "inspection" and any(c.session_id == reference.session_id for c in probes):
        raise ValueError(f"{path} requires held-out captures from a different session than the reference")
    overrides = manifest.get("thresholds", {})
    if not isinstance(overrides, dict):
        raise ValueError(f"{path} thresholds must be an object")
    return reference, probes, overrides, digest(raw.replace(b"\r\n", b"\n"))


def inspection_method(reference: bytes, overrides: dict) -> Method:
    from app.features.component_vision import service
    from app.ports import VisionProfile

    unknown = overrides.keys() - INSPECTION_THRESHOLDS.keys()
    if unknown:
        raise ValueError(f"Unknown inspection threshold fields: {sorted(unknown)}")
    thresholds = INSPECTION_THRESHOLDS | overrides
    # Check configuration even when admission ends early on an invalid reference.
    service._quality_thresholds(thresholds)
    service._registration(thresholds)
    hold = service._number(thresholds, "hold", 0.45, 0, 1)
    reject = service._number(thresholds, "reject", 0.70, 0, 1)
    if hold >= reject:
        raise ValueError("The hold threshold must be below the reject threshold")
    profile = VisionProfile(
        id="held-out-eval", part_id="eval", station_id="eval", method_version=service.METHOD_VERSION,
        calibration_version=1, calibrated=True, reference_sha256=digest(reference), thresholds=thresholds,
    )

    def check_reference() -> dict:
        service.validate_reference(reference, thresholds)
        return {"passed": True, "reasons": []}

    def probe_quality(probe: bytes) -> dict:
        gate = service.quality_gate(probe, thresholds)
        return {"passed": gate["passed"], "quality_gate": gate, "reasons": gate["failures"]}

    def measure(probe: bytes) -> Measurement:
        evidence = service.evaluate(probe, reference, profile, evidence_source=SOURCE)
        score = evidence.raw_score
        if score is None:
            outcome = "UNVERIFIABLE"
        elif score >= hold:
            outcome = "FLAGGED"
        else:
            outcome = "ACCEPTED"
        return Measurement(
            outcome, score, evidence.reason_codes,
            metrics=evidence.analysis, quality_gate=evidence.quality_gate,
            comparison_state="FAILED" if score is None else "SCORED",
            artifacts=artifacts(evidence.aligned_png, evidence.diff_png),
        )

    limits = {
        "max_bytes": service.MAX_IMAGE_BYTES,
        "max_jpeg_pixels": service.MAX_IMAGE_PIXELS,
        "max_png_pixels": service.MAX_PNG_PIXELS,
        "max_side": service.MAX_IMAGE_SIDE,
        "max_full_decode_pixels": service._MAX_FULL_DECODE_PIXELS,
        "max_reduced_jpeg_side": service.MAX_DECODED_SIDE,
        "max_working_side": service._WORK_EDGE,
    }
    admission = assess_input("GOLDEN_ADMISSION", check_reference)
    self_check = {"check": "GOLDEN_SELF_COMPARISON", **record_comparison(reference, measure)}
    if self_check["quality_gate"] is not None:
        admission["quality_gate"] = self_check["quality_gate"]
    if admission["passed"]:
        admission.update(passed=self_check["score"] is not None, reasons=self_check["reasons"])
    return Method(
        service.METHOD_VERSION, "raw_score", thresholds, limits, admission, self_check,
        lambda probe: assess_input("INSPECTION_QUALITY_GATE", lambda: probe_quality(probe)), measure,
    )


def seal_method(reference: bytes, overrides: dict) -> Method:
    from app.features.seal_vision import service
    from app.ports import SealProfile

    defaults = SealProfile(method_version=service.METHOD_VERSION)
    thresholds = asdict(defaults)
    del thresholds["method_version"]
    unknown = overrides.keys() - thresholds.keys()
    if unknown:
        raise ValueError(f"Unknown seal threshold fields: {sorted(unknown)}")
    thresholds.update(overrides)
    roi = thresholds["roi"]
    if roi is not None:
        if not isinstance(roi, list) or len(roi) != 4 or any(type(value) is not int for value in roi):
            raise ValueError("Seal ROI must be an array of four reference pixel values")
        if min(roi[:2]) < 0 or min(roi[2:]) <= 0:
            raise ValueError("Seal ROI requires a nonnegative origin and positive width and height")
        thresholds["roi"] = tuple(roi)
    profile = SealProfile(method_version=service.METHOD_VERSION, **thresholds)
    # The enrollment ROI is defined in the reference frame. A standalone
    # assessment of a probe has no corresponding coordinates.
    probe_profile = SealProfile(method_version=service.METHOD_VERSION, **(thresholds | {"roi": None}))
    outcomes = {"INTACT": "ACCEPTED", "SUSPICIOUS": "FLAGGED", "UNVERIFIABLE": "UNVERIFIABLE"}

    def enrollment(data: bytes, selected_profile: SealProfile) -> dict:
        evidence = service.assess_reference(data, selected_profile, evidence_source=SOURCE)
        return {
            "passed": evidence.usable,
            "reasons": evidence.reason_codes,
            "quality_gate": evidence.quality_gate,
            "keypoints": evidence.keypoints,
        }

    def measure(probe: bytes) -> Measurement:
        evidence = service.compare(reference, probe, profile, evidence_source=SOURCE)
        score = evidence.metrics["ssim"]
        state = "SCORED"
        if evidence.result == "UNVERIFIABLE":
            state = "FAILED" if score is None else "AMBIGUOUS"
        return Measurement(
            outcome=outcomes[evidence.result],
            score=score,
            reasons=evidence.quality_gate["failures"],
            explanation=evidence.explanation,
            metrics=evidence.metrics,
            quality_gate=evidence.quality_gate,
            classification=evidence.result,
            comparison_state=state,
            artifacts=artifacts(evidence.aligned_png, evidence.diff_png),
        )

    limits = {
        "max_bytes": service.MAX_IMAGE_BYTES,
        "max_jpeg_pixels": service.MAX_IMAGE_PIXELS,
        "max_png_pixels": service.MAX_PNG_PIXELS,
        "max_side": service.MAX_IMAGE_SIDE,
        "max_full_decode_pixels": service.MAX_FULL_DECODE_PIXELS,
        "max_reduced_jpeg_side": service.MAX_DECODED_SIDE,
        "max_working_side": service.WORKING_SIDE,
    }
    admission = assess_input("SEAL_ENROLLMENT", lambda: enrollment(reference, profile))
    admission["usable"] = admission["passed"]
    self_check = {"check": "SEAL_SELF_COMPARISON", **record_comparison(reference, measure)}
    return Method(
        service.METHOD_VERSION, "ssim", thresholds, limits, admission, self_check,
        lambda probe: assess_input("SEAL_WHOLE_FRAME_REFERENCE_ASSESSMENT", lambda: enrollment(probe, probe_profile)),
        measure,
    )


def evaluate_probe(method: Method, data: bytes) -> dict:
    if method.reference_assessment["passed"]:
        return record_comparison(data, method.measure)
    skipped = Measurement("NOT_EVALUATED", None, ["REFERENCE_UNUSABLE"], comparison_state="SKIPPED")
    return {**asdict(skipped), "standalone_assessment": method.check_probe(data)}


def fraction(count: int, denominator: int) -> dict:
    return {
        "count": count,
        "denominator": denominator,
        "ratio": count / denominator if denominator else None,
    }


def inspection_score_ordering(scores: dict[str, list[float]]) -> dict:
    good, defect = scores.get("good", []), scores.get("defect", [])
    highest_good = max(good, default=None)
    lowest_defect = min(defect, default=None)
    margin = lowest_defect - highest_good if highest_good is not None and lowest_defect is not None else None
    return {
        "good_scored": len(good),
        "defect_scored": len(defect),
        "max_good": highest_good,
        "min_defect": lowest_defect,
        "min_defect_minus_max_good": margin,
        "single_threshold_separates_scored_probes": margin > 0 if margin is not None else None,
    }


def summarize(kind: str, method: Method, results: list[dict], threshold_choice: dict) -> dict:
    conditions: dict[str, dict] = {}
    scores: dict[str, list[float]] = {}
    expected_accept = LABELS[kind][1]
    for result in results:
        label, outcome = result["label"], result["outcome"]
        good = label == expected_accept
        counts = conditions.setdefault(label, dict.fromkeys(DEFINITIONS, 0))
        counts["n"] += 1
        counts[outcome.lower()] += 1
        counts["false_accepts"] += int(not good and outcome == "ACCEPTED")
        counts["false_rejects"] += int(good and outcome == "FLAGGED")
        label_scores = scores.setdefault(label, [])
        if result["score"] is not None:
            label_scores.append(result["score"])
    totals = {field: sum(group[field] for group in conditions.values()) for field in DEFINITIONS}
    attempted = [result for result in results if result["outcome"] != "NOT_EVALUATED"]
    attempted_good = sum(result["label"] == expected_accept for result in attempted)
    n = totals["n"]
    report = {
        "method_version": method.version,
        "score_name": method.score_name,
        "thresholds": method.thresholds,
        "threshold_status": "PROVISIONAL",
        "threshold_choice": threshold_choice,
        "comparison_status": "COMPLETED" if method.reference_assessment["passed"] else "BLOCKED_REFERENCE",
        "input_limits": method.input_limits,
        "reference": {"admission": method.reference_assessment, "self_check": method.self_check},
        "conditions": conditions,
        "totals": totals,
        "performance": {
            "comparison_coverage": fraction(len(attempted), n),
            "numeric_score_coverage": fraction(sum(map(len, scores.values())), n),
            "decision_coverage": fraction(totals["accepted"] + totals["flagged"], n),
            "false_accept_rate": fraction(totals["false_accepts"], len(attempted) - attempted_good),
            "false_reject_rate": fraction(totals["false_rejects"], attempted_good),
            "comparison_states": dict(Counter(result["comparison_state"] for result in results)),
            "failure_reasons": dict(Counter(
                reason for result in attempted if result["comparison_state"] == "FAILED"
                for reason in result["reasons"]
            )),
            "score_ranges_by_condition": {
                label: {"n": len(values), "min": min(values, default=None), "max": max(values, default=None)}
                for label, values in scores.items()
            },
        },
        "results": results,
    }
    if kind == "inspection":
        report["performance"]["good_defect_score_ordering"] = inspection_score_ordering(scores)
    return report


def alternative_result(kind: str, before: dict, after: dict) -> dict:
    decision = ("outcome", "classification", "score", "reasons", "comparison_state")
    result = {
        "file": after["file"],
        "label": after["label"],
        "before": {field: before[field] for field in decision},
        **{field: after[field] for field in decision},
    }
    gate = after["quality_gate"] or {}
    compared_gate = before["quality_gate"] or {}
    gate_changes = {
        field: value for field, value in gate.items()
        if field not in {"thresholds", "reference"} and value != compared_gate.get(field)
    }
    if gate_changes:
        result["quality_gate_changes"] = gate_changes
    if kind == "seal" and after["metrics"] is not None:
        result["metrics"] = {field: value for field, value in after["metrics"].items() if field != "thresholds"}
    for field in ("explanation", "artifacts", "error", "standalone_assessment"):
        if after.get(field) is not None:
            result[field] = after[field]
    return result


def glare_measurements(results: list[dict]) -> dict:
    groups: dict[str, list[float]] = {"glare_label": [], "other_labels": []}
    for result in results:
        gate = result["quality_gate"]
        if gate is not None:
            group = "glare_label" if result["label"] == "glare" else "other_labels"
            groups[group].append(gate["glare_ratio"])
    return {
        group: {"n": len(values), "min": min(values, default=None), "max": max(values, default=None)}
        for group, values in groups.items()
    }


def evaluate_set(kind: str, path: Path, output: Path) -> dict:
    path = path.resolve()
    reference, probes, overrides, manifest_digest = read_manifest(path, kind)
    if output in {PROTOCOL, path, reference.path, *(capture.path for capture in probes)}:
        raise ValueError(f"Output would replace an input file: {output}")
    reference_bytes = reference.path.read_bytes()
    make_method = inspection_method if kind == "inspection" else seal_method
    configs = {"primary": overrides}
    profile_basis = "inspection evaluator defaults" if kind == "inspection" else "platform SealProfile defaults"
    choices = {"primary": {
        "basis": profile_basis,
        "manifest_overrides": overrides,
        "selection": "The evaluator applies the default thresholds and supplied overrides before comparing probes.",
    }}

    reference_digest = digest(reference_bytes)
    protocol = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    baseline = protocol["baseline"]
    before = baseline["sets"][kind]
    if not overrides and manifest_digest == before["manifest_sha256"] and reference_digest == before["reference_sha256"]:
        for name, profile in protocol["alternative_profiles"][kind].items():
            configs[name] = configs[profile["compare_to"]] | profile["overrides"]
            choices[name] = profile

    methods = {name: make_method(reference_bytes, config) for name, config in configs.items()}
    seen = {reference_digest}
    results: dict[str, list[dict]] = {name: [] for name in methods}
    for capture in probes:
        data = capture.path.read_bytes()
        sha = digest(data)
        if sha in seen:
            raise ValueError(f"Duplicate capture bytes in {path}: {capture.path.name}")
        seen.add(sha)
        result = capture.provenance(path.parent, sha)
        result["input_metadata"] = input_metadata(data)
        for name, method in methods.items():
            results[name].append({**result, **evaluate_probe(method, data)})

    provenance = {**reference.provenance(path.parent, reference_digest), "input_metadata": input_metadata(reference_bytes)}
    reports = {name: summarize(kind, method, results[name], choices[name]) for name, method in methods.items()}
    for name, summary in reports.items():
        if name != "primary":
            comparator = choices[name]["compare_to"]
            summary["reference"]["file"] = provenance["file"]
            del summary["input_limits"]  # Every profile uses the primary method's input admission limits.
            counts = summary["totals"]
            previous = reports[comparator]["totals"]
            summary["changes_from_comparator"] = {
                field: counts[field] - previous[field]
                for field in ("accepted", "flagged", "unverifiable", "false_accepts", "false_rejects")
            }
            summary["results"] = [
                alternative_result(kind, before_result, after)
                for before_result, after in zip(results[comparator], results[name], strict=True)
            ]

    report = reports.pop("primary")
    report["reference"] = {**provenance, **report["reference"]}
    report["manifest_sha256"] = manifest_digest
    capture_index = sorted((row["file"], row["sha256"]) for row in [provenance, *report["results"]])
    report["capture_set_sha256"] = digest(json.dumps(capture_index, separators=(",", ":")).encode("ascii"))

    if all(report[key] == before[key] for key in ("manifest_sha256", "capture_set_sha256")):
        metadata = [provenance["input_metadata"], *(result["input_metadata"] for result in report["results"])]
        report["admission_comparison"] = {
            "source": baseline["source"],
            "before": before,
            "after": {
                "reference_admitted": methods["primary"].reference_assessment["passed"],
                "comparison_status": report["comparison_status"],
                "totals": report["totals"],
            },
            "capture_diagnostics": {
                "jpeg_with_trailing_bytes": sum((entry.get("jpeg_trailing_bytes") or 0) > 0 for entry in metadata),
                "above_previous_pixel_limit": sum(
                    entry.get("declared_pixels", 0) > before["input_limits"]["max_pixels"] for entry in metadata
                ),
            },
        }
        if kind == "inspection":
            report["glare_ratio_by_condition_group"] = glare_measurements(report["results"])
        if reports:
            report["alternative_evaluations"] = reports
    return report


def write_report(path: Path, metrics: dict[str, dict]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_name(f".{path.name}.lock")
    try:
        lock.mkdir()
    except FileExistsError as exc:
        raise ValueError(f"Report is locked at {lock}. Wait for the other run or use a different --output.") from exc
    tmp = None
    try:
        report = {
            "report_version": REPORT_VERSION,
            "evidence_source": SOURCE,
            "definitions": DEFINITIONS,
            "input_metadata_definition": INPUT_METADATA_DEFINITION,
            "comparison_states": COMPARISON_STATES,
            "performance_definition": PERFORMANCE_DEFINITION,
        }
        if len(metrics) == 1 and path.exists():
            try:
                previous = json.loads(path.read_text(encoding="utf-8"))
            except ValueError as exc:
                raise ValueError(f"Report at {path} cannot be read as JSON. Choose a new --output.") from exc
            if not isinstance(previous, dict) or any(previous.get(key) != value for key, value in report.items()):
                raise ValueError(f"Report at {path} is incompatible. Choose a new --output or evaluate both sets.")
            report.update(previous)
        report.update(metrics)
        rendered = json.dumps(report, indent=2, allow_nan=False) + "\n"
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", dir=path.parent, delete=False) as output:
            tmp = Path(output.name)
            output.write(rendered)
            output.flush()
            os.fsync(output.fileno())
        os.replace(tmp, path)
        return rendered
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)
        lock.rmdir()


def fail(exc: Exception, stage: str, *, code: str = "VISION_EVAL_FAILED", remediation: str) -> int:
    print(json.dumps({
        "error_code": code,
        "component": "vision_eval",
        "stage": stage,
        "message": str(exc),
        "remediation": remediation,
    }), file=sys.stderr)
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--inspection-manifest", type=Path, help="select a component inspection capture manifest")
    parser.add_argument("--seal-manifest", type=Path, help="select a physical seal capture manifest")
    parser.add_argument(
        "--output", type=Path, default=ROOT / "datasets" / "vision_eval.json", help="destination JSON report",
    )
    args = parser.parse_args()
    manifests = {"inspection": args.inspection_manifest, "seal": args.seal_manifest}
    if not any(manifests.values()):
        manifests = {kind: ROOT / "fixtures" / "images" / kind / "manifest.json" for kind in LABELS}

    stage = "setup"
    try:
        from app.core.errors import ApiError
    except ImportError as exc:
        return fail(exc, stage, remediation="Use the backend Python environment.")

    try:
        output = args.output.resolve()
        metrics = {}
        for kind, path in manifests.items():
            if path is not None:
                stage = kind
                metrics[kind] = evaluate_set(kind, path, output)
        stage = "report"
        rendered = write_report(output, metrics)
    except ApiError as exc:
        remediation = exc.details.get("remediation", "Check the reference image and thresholds for the selected manifest.")
        return fail(exc, stage, code=exc.code, remediation=remediation)
    except ImportError as exc:
        return fail(exc, stage, remediation="Install the selected vision module and its dependencies in this checkout.")
    except (OSError, ValueError, TypeError) as exc:
        remediation = {
            "setup": "Check --output.",
            "report": "Check the destination report path or choose a new --output.",
        }.get(stage, "Check the selected manifest, capture files, and their provenance.")
        return fail(exc, stage, remediation=remediation)
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
