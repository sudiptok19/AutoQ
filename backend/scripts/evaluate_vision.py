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


SOURCE = "PRERECORDED_EVAL"
REPORT_VERSION = 2
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
    "Byte lengths and JPEG marker sequences are read directly from the original files. "
    "Declared width and height are read from the image header without decoding pixels or applying orientation."
)


@dataclass(frozen=True)
class Method:
    version: str
    score_name: str
    thresholds: dict
    input_limits: dict
    reference_assessment: dict
    check_probe: Callable[[bytes], dict]
    measure: Callable[[bytes], Measurement]


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def input_metadata(data: bytes) -> dict:
    from PIL import Image

    metadata = {"size_bytes": len(data)}
    try:
        with Image.open(BytesIO(data), formats=("JPEG", "PNG")) as header:
            width, height = header.size
            metadata.update(declared_width=width, declared_height=height, declared_pixels=width * height)
            if header.format == "JPEG":
                marker = data.rfind(b"\xff\xd9")
                metadata["jpeg_bytes_after_last_eoi_sequence"] = len(data) - marker - 2 if marker != -1 else None
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError):
        # A readable header is optional diagnostic data; image admission remains with the service.
        pass
    return metadata


def input_error(exc: ApiError) -> dict:
    return {"code": exc.code, "message": exc.message, "status": exc.status, "details": exc.details}


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
        evidence = service.evaluate(reference, reference, profile, evidence_source=SOURCE)
        return {
            "passed": evidence.raw_score is not None,
            "quality_gate": evidence.quality_gate,
            "reasons": evidence.reason_codes,
        }

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
        return Measurement(outcome, score, evidence.reason_codes)

    limits = {"max_bytes": service.MAX_IMAGE_BYTES, "max_pixels": service._MAX_PIXELS, "max_side": service._MAX_EDGE}
    return Method(
        service.METHOD_VERSION, "raw_score", thresholds, limits,
        assess_input("GOLDEN_ADMISSION", check_reference),
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
        return Measurement(
            outcome=outcomes[evidence.result],
            score=evidence.metrics["ssim"],
            reasons=evidence.quality_gate["failures"],
            explanation=evidence.explanation,
            metrics=evidence.metrics,
        )

    limits = {"max_bytes": service.MAX_IMAGE_BYTES, "max_pixels": service.MAX_IMAGE_PIXELS, "max_side": service.MAX_IMAGE_SIDE}
    return Method(
        service.METHOD_VERSION, "ssim", thresholds, limits,
        assess_input("SEAL_ENROLLMENT", lambda: enrollment(reference, profile)),
        lambda probe: assess_input("SEAL_WHOLE_FRAME_REFERENCE_ASSESSMENT", lambda: enrollment(probe, probe_profile)),
        measure,
    )


def evaluate_set(kind: str, path: Path, output: Path) -> dict:
    from app.core.errors import ApiError

    path = path.resolve()
    reference, probes, overrides, manifest_digest = read_manifest(path, kind)
    if output in {path, reference.path, *(capture.path for capture in probes)}:
        raise ValueError(f"Output would replace an input file: {output}")
    reference_bytes = reference.path.read_bytes()
    make_method = inspection_method if kind == "inspection" else seal_method
    method = make_method(reference_bytes, overrides)
    admitted = method.reference_assessment["passed"]
    reference_digest = digest(reference_bytes)
    seen = {reference_digest}
    conditions = {}
    results = []
    for capture in probes:
        data = capture.path.read_bytes()
        sha = digest(data)
        if sha in seen:
            raise ValueError(f"Duplicate capture bytes in {path}: {capture.path.name}")
        seen.add(sha)
        result = capture.provenance(path.parent, sha)
        result["input_metadata"] = input_metadata(data)
        if admitted:
            try:
                measurement = method.measure(data)
            except ApiError as exc:
                if exc.code != "INVALID_IMAGE":
                    raise
                measurement = Measurement("UNVERIFIABLE", None, [exc.code], exc.message)
                result["error"] = input_error(exc)
        else:
            measurement = Measurement("NOT_EVALUATED", None, ["REFERENCE_UNUSABLE"])
            result["standalone_assessment"] = method.check_probe(data)
        outcome = measurement.outcome
        good = capture.label == LABELS[kind][1]
        counts = conditions.setdefault(capture.label, dict.fromkeys(DEFINITIONS, 0))
        counts["n"] += 1
        counts[outcome.lower()] += 1
        counts["false_accepts"] += int(not good and outcome == "ACCEPTED")
        counts["false_rejects"] += int(good and outcome == "FLAGGED")
        results.append({**result, **asdict(measurement)})
    return {
        "method_version": method.version,
        "score_name": method.score_name,
        "thresholds": method.thresholds,
        "threshold_status": "PROVISIONAL",
        "comparison_status": "COMPLETED" if admitted else "BLOCKED_REFERENCE",
        "input_limits": method.input_limits,
        "reference": {
            **reference.provenance(path.parent, reference_digest),
            "input_metadata": input_metadata(reference_bytes),
            "admission": method.reference_assessment,
        },
        "manifest_sha256": manifest_digest,
        "conditions": conditions,
        "totals": {field: sum(group[field] for group in conditions.values()) for field in DEFINITIONS},
        "results": results,
    }


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
