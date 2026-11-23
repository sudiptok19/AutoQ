"""INTERIM component-vision adapter (Opus) — replaced by Astra's A1 `component_vision.service.evaluate`
when it merges. Same signature and output shape as ASTRA_BRIEF §A1, distinct method_version so SPC
baselines never mix. Real OpenCV measurements; it never decides PASS/HOLD/REJECT (policy does)."""
from dataclasses import dataclass

import cv2
import numpy as np

from app.core.errors import ApiError
from app.ports import VisionProfile

METHOD_VERSION = "interim-gd-0.1"
W, H = 640, 480


@dataclass(frozen=True)
class VisionEvidence:
    quality_gate: dict
    analysis: dict
    reason_codes: list[str]
    aligned_png: bytes | None
    diff_png: bytes | None
    raw_score: float | None


def _decode(data: bytes) -> np.ndarray:
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR) if data else None
    if img is None:
        raise ApiError(422, "INVALID_IMAGE", "Image could not be decoded", {"component": "vision.interim"})
    return img


def _fit(img: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return cv2.resize(gray, (W, H), interpolation=cv2.INTER_AREA)


def quality_gate(image: bytes, thresholds: dict) -> dict:
    img = _decode(image)
    h, w = img.shape[:2]
    g = _fit(img)
    blur = float(cv2.Laplacian(g, cv2.CV_64F).var())
    glare = float((g >= 250).mean())
    t = {"blur_min": float(thresholds.get("blur_min", 80)), "glare_max": float(thresholds.get("glare_max", 0.12)),
         "min_w": int(thresholds.get("min_w", 320)), "min_h": int(thresholds.get("min_h", 240))}
    failures = []
    if w < t["min_w"] or h < t["min_h"]:
        failures.append("IMAGE_QUALITY_RESOLUTION")
    if blur < t["blur_min"]:
        failures.append("IMAGE_QUALITY_BLUR")
    if glare > t["glare_max"]:
        failures.append("IMAGE_QUALITY_GLARE")
    return {"passed": not failures, "blur_laplacian_var": round(blur, 1), "glare_ratio": round(glare, 4), "width": w, "height": h, "thresholds": t, "failures": failures}


def _png(a: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".png", a)
    if not ok:
        raise ApiError(500, "VISION_ENCODE_FAILED", "Could not encode derived evidence", {"component": "vision.interim"})
    return buf.tobytes()


def evaluate(image: bytes, reference: bytes | None, profile: VisionProfile | None) -> VisionEvidence:
    th = dict(profile.thresholds) if profile else {}
    gate = quality_gate(image, th)
    hold, reject = float(th.get("hold", 0.45)), float(th.get("reject", 0.70))
    analysis = {"method": "golden_diff", "method_version": METHOD_VERSION, "calibrated": bool(profile and profile.calibrated), "score": None,
                "hold_threshold": hold, "reject_threshold": reject, "threshold_tightened": bool(th.get("tightened")), "regions": []}
    if not gate["passed"]:
        return VisionEvidence(gate, analysis, list(gate["failures"]), None, None, None)
    if not profile or not profile.calibrated or reference is None:
        return VisionEvidence(gate, analysis, ["MODEL_UNCALIBRATED"], None, None, None)
    probe = cv2.GaussianBlur(_fit(_decode(image)), (5, 5), 0).astype(np.float32)
    ref = cv2.GaussianBlur(_fit(_decode(reference)), (5, 5), 0).astype(np.float32)
    (dx, dy), response = cv2.phaseCorrelate(ref, probe)
    if response < float(th.get("min_registration", 0.05)) or abs(dx) > 40 or abs(dy) > 40:
        return VisionEvidence(gate, analysis | {"registration": {"dx": round(dx, 2), "dy": round(dy, 2), "response": round(response, 3)}}, ["ALIGNMENT_FAILED"], None, None, None)
    aligned = cv2.warpAffine(probe, np.float32([[1, 0, -dx], [0, 1, -dy]]), (W, H), borderMode=cv2.BORDER_REPLICATE)
    diff = cv2.absdiff(aligned, ref)
    diff[:8, :] = diff[-8:, :] = 0
    diff[:, :8] = diff[:, -8:] = 0
    cells = []
    for y in range(0, H, 32):
        for x in range(0, W, 32):
            cells.append((min(1.0, float(diff[y:y + 32, x:x + 32].mean()) / 28.0), x, y))
    score = round(max(c[0] for c in cells), 3)
    regions = [{"x": x, "y": y, "w": 32, "h": 32, "score": round(s, 2)} for s, x, y in sorted(cells, reverse=True)[:6] if s > 0.35]
    heat = cv2.applyColorMap(np.clip(diff * 5, 0, 255).astype(np.uint8), cv2.COLORMAP_INFERNO)
    overlay = cv2.addWeighted(cv2.cvtColor(aligned.astype(np.uint8), cv2.COLOR_GRAY2BGR), 0.55, heat, 0.45, 0)
    reasons = ["SCORE_ABOVE_REJECT"] if score >= reject else ["SCORE_ABOVE_HOLD"] if score >= hold else []
    analysis |= {"score": score, "regions": regions, "registration": {"dx": round(dx, 2), "dy": round(dy, 2), "response": round(response, 3)}}
    return VisionEvidence(gate, analysis, reasons, _png(aligned.astype(np.uint8)), _png(overlay), score)
