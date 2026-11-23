"""SYNTHETIC_TEST seal renders: patterned tape across a box seam, recaptured with pose/lighting noise."""
import cv2
import numpy as np


def render_seal(kind: str = "enroll", seed: int = 0) -> bytes:
    rng = np.random.default_rng(seed)
    img = np.zeros((720, 960, 3), np.uint8)
    img[:] = (67, 106, 139)                                       # cardboard
    img = np.clip(img + rng.normal(0, 6, img.shape), 0, 255).astype(np.uint8)
    cv2.line(img, (0, 360), (960, 360), (40, 70, 95), 3)          # box seam
    tape = np.full((200, 760, 3), 225, np.uint8)
    prng = np.random.default_rng(99)                              # the seal's own (fixed) pattern
    for _ in range(900):
        cx, cy, r = int(prng.integers(0, 760)), int(prng.integers(0, 200)), int(prng.integers(2, 8))
        col = (140, 151, 10) if prng.random() > 0.5 else (51, 41, 31)
        cv2.circle(tape, (cx, cy), r, col, -1)
    cv2.putText(tape, "AQ VOID IF BROKEN 4471", (120, 112), cv2.FONT_HERSHEY_SIMPLEX, 1.3, (31, 41, 51), 3)
    if kind == "torn":
        pts = np.array([[0, 0], [300, 0], [250, 90], [140, 130], [0, 110]], np.int32)
        cv2.fillPoly(tape, [pts], (139, 106, 67))
        tape[40:150, 260:380] = 225
    img[260:460, 100:860] = tape
    if kind != "enroll":
        ang = float(rng.uniform(-3, 3)); sc = float(rng.uniform(0.97, 1.03))
        M = cv2.getRotationMatrix2D((480 + rng.uniform(-8, 8), 360 + rng.uniform(-8, 8)), ang, sc)
        img = cv2.warpAffine(img, M, (960, 720), borderMode=cv2.BORDER_REPLICATE)
        img = cv2.convertScaleAbs(img, alpha=float(rng.uniform(0.92, 1.08)), beta=float(rng.uniform(-8, 8)))
        img = np.clip(img + rng.normal(0, 4, img.shape), 0, 255).astype(np.uint8)
    if kind == "bad_light":
        img = cv2.GaussianBlur((img * 0.18).astype(np.uint8), (21, 21), 8)
    return cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 92])[1].tobytes()
