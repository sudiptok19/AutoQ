"""SYNTHETIC_TEST renders for tests and the demo seed. Every image made here must be recorded with
evidence_source=SYNTHETIC_TEST so the UI badges it; none of it is evidence about a physical part or seal."""
import cv2
import numpy as np


def render(kind: str = "good", seed: int = 1, wear: float = 0.0) -> bytes:
    """SYNTHETIC_TEST bracket on a fixture tray: fixed surface texture (machining marks, stamped text) so feature
    registration has something to lock onto, plus per-capture pose, exposure and sensor noise."""
    rng = np.random.default_rng(seed)
    tex = np.random.default_rng(7)                                    # the part's own (fixed) surface
    img = np.full((720, 960, 3), 58, np.uint8)
    for x in range(0, 960, 40):
        cv2.line(img, (x, 0), (x, 720), (70, 70, 70), 1)
    pts = np.array([[195, 180], [705, 165], [783, 240], [792, 495], [720, 561], [450, 570], [375, 450], [210, 450], [165, 393]], np.int32)
    cv2.fillPoly(img, [pts], (175, 180, 186))
    mask = np.zeros(img.shape[:2], np.uint8); cv2.fillPoly(mask, [pts], 255)
    marks = np.zeros_like(img)
    for _ in range(700):
        x, y = int(tex.integers(170, 790)), int(tex.integers(165, 570))
        cv2.circle(marks, (x, y), int(tex.integers(1, 4)), (int(tex.integers(90, 150)),) * 3, -1)
    for i in range(40):
        y = 190 + i * 9
        cv2.line(marks, (200, y), (780, y + int(tex.integers(-3, 4))), (150, 155, 160), 1)
    img = np.where(mask[..., None] > 0, np.where(marks > 0, marks, img), img)
    cv2.polylines(img, [pts], True, (90, 95, 100), 3)
    for (x, y, r) in [(262, 315, 45), (660, 262, 39), (675, 472, 39)]:
        cv2.circle(img, (x, y), r + 9, (120, 125, 130), 3)
        cv2.circle(img, (x, y), r, (30, 32, 35), -1)
    cv2.putText(img, "PN-44120 C L2611A", (430, 420), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (60, 62, 66), 2)
    if kind == "defect":
        cv2.line(img, (300, 330), (520, 380), (15, 15, 15), 9)
        cv2.line(img, (520, 380), (600, 430), (15, 15, 15), 7)
        cv2.fillPoly(img, [np.array([[690, 166], [783, 240], [700, 260]], np.int32)], (58, 58, 58))
    if wear > 0:                                                      # gradual tool wear: faint burr along the lower edge
        cv2.line(img, (380, 448), (380 + int(330 * min(wear, 1.0)), 566), (int(120 - 60 * min(wear, 1.0)),) * 3, max(1, int(6 * wear)))
    if kind != "golden":
        M = cv2.getRotationMatrix2D((480 + rng.uniform(-6, 6), 360 + rng.uniform(-6, 6)), float(rng.uniform(-2, 2)), float(rng.uniform(0.98, 1.02)))
        img = cv2.warpAffine(img, M, (960, 720), borderMode=cv2.BORDER_REPLICATE)
        img = cv2.convertScaleAbs(img, alpha=float(rng.uniform(0.95, 1.05)), beta=float(rng.uniform(-5, 5)))
    if kind == "blurry":
        img = cv2.GaussianBlur(img, (41, 41), 16)
    if kind == "glare":
        cv2.circle(img, (480, 360), 300, (255, 255, 255), -1)
    img = np.clip(img.astype(float) + rng.normal(0, 3, img.shape), 0, 255).astype(np.uint8)
    return cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 92])[1].tobytes()


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
