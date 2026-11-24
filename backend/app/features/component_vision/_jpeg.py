"""Primary-frame JPEG structure and bounded decoding for vision evidence."""

from dataclasses import dataclass
from io import BytesIO
from struct import error as StructError, unpack_from

import cv2
import numpy as np
from numpy.typing import NDArray
from PIL import Image, ImageOps


class InvalidJpeg(ValueError):
    """The primary image is malformed or uses an unsupported JPEG encoding."""


class JpegDecoderUnavailable(RuntimeError):
    """The strict native JPEG decoder cannot be loaded or initialized."""


@dataclass(frozen=True)
class JpegFrame:
    width: int
    height: int
    end: int  # Exclusive offset of the main image's EOI.


_SOF = {0xC0, 0xC1, 0xC2}  # Baseline, extended sequential, and progressive DCT.
_SEGMENT = _SOF | {0xC4, 0xDA, 0xDB, 0xDD, 0xFE} | set(range(0xE0, 0xF0))
_MAX_SCANS = 128
_COLOR_REDUCTIONS = (
    (2, cv2.IMREAD_REDUCED_COLOR_2),
    (4, cv2.IMREAD_REDUCED_COLOR_4),
    (8, cv2.IMREAD_REDUCED_COLOR_8),
)
_ORIENTATION_TRANSFORMS = {
    2: lambda pixels: cv2.flip(pixels, 1),
    3: lambda pixels: cv2.rotate(pixels, cv2.ROTATE_180),
    4: lambda pixels: cv2.flip(pixels, 0),
    5: cv2.transpose,
    6: lambda pixels: cv2.rotate(pixels, cv2.ROTATE_90_CLOCKWISE),
    7: lambda pixels: cv2.flip(cv2.transpose(pixels), -1),
    8: lambda pixels: cv2.rotate(pixels, cv2.ROTATE_90_COUNTERCLOCKWISE),
}


def _after_scan(data: bytes, start: int) -> int:
    """Skip entropy bytes, stuffed FF bytes and restart markers in a scan."""
    offset = start
    while (marker_start := data.find(b"\xff", offset)) != -1:
        offset = marker_start + 1
        while offset < len(data) and data[offset] == 0xFF:
            offset += 1
        if offset == len(data):
            break
        marker = data[offset]
        if marker == 0 or 0xD0 <= marker <= 0xD7:
            offset += 1
            continue
        if marker_start == start:
            raise InvalidJpeg("JPEG scan contains no image data")
        return marker_start
    raise InvalidJpeg("JPEG is missing its end-of-image marker")


def jpeg_frame(data: bytes) -> JpegFrame:
    """Read marker lengths and the scan stream up to the primary image's EOI."""
    if not data.startswith(b"\xff\xd8\xff"):
        raise InvalidJpeg("JPEG is missing its start-of-image marker")
    width = height = scans = 0
    components: set[int] = set()
    dc_components: set[int] = set()
    offset = 2
    while offset < len(data):
        if data[offset] != 0xFF:
            raise InvalidJpeg("JPEG contains data outside an image scan")
        while offset < len(data) and data[offset] == 0xFF:
            offset += 1
        if offset == len(data):
            break
        marker = data[offset]
        offset += 1
        if marker == 0xD9:
            if not scans or dc_components != components:
                raise InvalidJpeg("JPEG is missing image scans")
            return JpegFrame(width, height, offset)
        if marker not in _SEGMENT or offset + 2 > len(data):
            raise InvalidJpeg("JPEG contains an invalid or unsupported marker")
        length = unpack_from(">H", data, offset)[0]
        end = offset + length
        if length < 2 or end > len(data):
            raise InvalidJpeg("JPEG contains an incomplete segment")

        if marker in _SOF:
            if components or length < 8:
                raise InvalidJpeg("JPEG requires one valid frame header")
            precision, height, width, count = unpack_from(">BHHB", data, offset + 2)
            if precision != 8 or count not in (1, 3, 4) or length != 8 + 3 * count or min(width, height) == 0:
                raise InvalidJpeg("JPEG contains an invalid or unsupported frame header")
            components = set(data[offset + 8:end:3])
            if len(components) != count:
                raise InvalidJpeg("JPEG contains duplicate frame components")
        elif marker == 0xDA:
            scans += 1
            if scans > _MAX_SCANS or not components or length < 6:
                raise InvalidJpeg("JPEG contains an invalid scan header")
            count = data[offset + 2]
            if not 1 <= count <= len(components) or length != 6 + 2 * count:
                raise InvalidJpeg("JPEG contains an invalid scan header")
            selected = set(data[offset + 3:end - 3:2])
            if len(selected) != count or not selected <= components:
                raise InvalidJpeg("JPEG contains invalid scan components")
            if data[end - 3] == 0 and data[end - 1] >> 4 == 0:
                dc_components.update(selected)
            end = _after_scan(data, end)
        offset = end
    raise InvalidJpeg("JPEG is missing its end-of-image marker")


def _validate_entropy(payload: memoryview, frame: JpegFrame) -> None:
    try:
        from simplejpeg import decode_jpeg as strict_decode
    except (ImportError, OSError, RuntimeError, ValueError) as exc:
        raise JpegDecoderUnavailable from exc

    # Decode the entire entropy stream while limiting the validation raster.
    # The legacy pixel path below preserves calibrated measurements.
    try:
        preview = strict_decode(
            payload, colorspace="Gray", min_width=1, min_height=1, min_factor=8, strict=True,
        )
    except ValueError as exc:
        raise InvalidJpeg("JPEG scan could not be decoded completely") from exc
    except RuntimeError as exc:
        raise JpegDecoderUnavailable from exc
    if preview.shape != ((frame.height + 7) // 8, (frame.width + 7) // 8, 1):
        raise InvalidJpeg("JPEG could not be decoded at its declared dimensions")


def decode_jpeg(
    data: bytes, frame: JpegFrame, *, max_edge: int | None = None, grayscale: bool = False,
) -> tuple[NDArray[np.uint8], tuple[int, int]]:
    """Decode the primary image and report full dimensions in display orientation."""
    payload = memoryview(data)[:frame.end]
    _validate_entropy(payload, frame)
    dimensions = (frame.width, frame.height)
    try:
        if max_edge is not None and max(dimensions) > max_edge:
            for factor, flags in _COLOR_REDUCTIONS:
                reduced = tuple((size + factor - 1) // factor for size in dimensions)
                if max(reduced) <= max_edge:
                    break
            else:
                raise InvalidJpeg("JPEG exceeds the supported decode dimensions")
            with Image.open(BytesIO(payload), formats=("JPEG",)) as source:
                orientation = source.getexif().get(274, 1)
            pixels = cv2.imdecode(np.frombuffer(payload, np.uint8), flags | cv2.IMREAD_IGNORE_ORIENTATION)
            if pixels is None or pixels.shape != (*reduced[::-1], 3):
                raise InvalidJpeg("JPEG could not be decoded at its declared dimensions")
            transform = _ORIENTATION_TRANSFORMS.get(orientation)
            if transform is not None:
                pixels = transform(pixels)
            if orientation in (5, 6, 7, 8):
                dimensions = dimensions[::-1]
            if grayscale:
                pixels = cv2.cvtColor(pixels, cv2.COLOR_BGR2GRAY)
        elif grayscale:
            with Image.open(BytesIO(payload), formats=("JPEG",)) as source:
                if source.size != dimensions:
                    raise InvalidJpeg("JPEG could not be decoded at its declared dimensions")
                oriented = ImageOps.exif_transpose(source)
                pixels = np.asarray(oriented.convert("L"))
                dimensions = oriented.size
        else:
            pixels = cv2.imdecode(np.frombuffer(payload, np.uint8), cv2.IMREAD_COLOR)
            if pixels is None or pixels.shape[:2] not in (dimensions[::-1], dimensions):
                raise InvalidJpeg("JPEG could not be decoded at its declared dimensions")
            dimensions = (pixels.shape[1], pixels.shape[0])
    except (cv2.error, OSError, SyntaxError, EOFError, StructError, TypeError, Image.DecompressionBombError) as exc:
        raise InvalidJpeg("JPEG could not be decoded") from exc
    return pixels, dimensions
