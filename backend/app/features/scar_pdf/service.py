"""Render a frozen 8D evidence snapshot as a deterministic SCAR PDF."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from functools import cache, partial
from html import escape
from io import BytesIO
from pathlib import Path

import reportlab
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import BaseDocTemplate, CondPageBreak, Frame, LongTable, PageTemplate, Paragraph, Spacer, TableStyle

from app.core.errors import ApiError
from app.ports import EvidenceRef, ReportItem, ReportSection, ReportSnapshot

_SECTION_KEYS = tuple(f"D{i}" for i in range(1, 9))
_HUMAN_REVIEW = {"D4", "D6", "D8"}
_ITEM_KINDS = ("FACT", "HYPOTHESIS", "PROPOSED", "PENDING_HUMAN")
_COUNT_ROWS = (
    ("affected_units", "Affected units", "Units"),
    ("held_packages", "Held packages", "Packages"),
    ("at_plant", "At plant", "Units"),
    ("loaded_not_departed", "Loaded, awaiting departure", "Units"),
    ("in_transit", "In transit", "Units"),
    ("delivered", "Delivered", "Units"),
)
_DIGEST = re.compile(r"[0-9a-fA-F]{64}")
_DISALLOWED_CONTROLS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

_MARGIN = 44
_INK = colors.HexColor("#172736")
_SUBTLE = colors.HexColor("#4F6270")
_ACCENT = colors.HexColor("#116562")
_RULE = colors.HexColor("#D8E2E6")
_TINT = colors.HexColor("#F0F5F6")
_REGULAR_FONT = "AutoQ-Vera"
_BOLD_FONT = "AutoQ-Vera-Bold"

_BODY = ParagraphStyle("Body", fontName=_REGULAR_FONT, fontSize=10, leading=14, textColor=_INK, spaceAfter=6, splitLongWords=1)
_TITLE = ParagraphStyle("Title", parent=_BODY, fontName=_BOLD_FONT, fontSize=30, leading=34, spaceAfter=14)
_HEADING = ParagraphStyle(
    "Heading", parent=_BODY, fontName=_BOLD_FONT, fontSize=14, leading=19, textColor=_ACCENT, spaceBefore=11, spaceAfter=5
)
_TAG = ParagraphStyle("Tag", parent=_BODY, fontName=_BOLD_FONT, fontSize=8, leading=11, textColor=_SUBTLE, keepWithNext=True, spaceAfter=2)
_PENDING = ParagraphStyle(
    "Pending", parent=_TAG, textColor=colors.HexColor("#713D08"), backColor=colors.HexColor("#FFF0D6"),
    borderPadding=4, spaceBefore=3, spaceAfter=11, keepWithNext=False,
)
_CELL = ParagraphStyle("Cell", parent=_BODY, fontSize=9, leading=12, spaceAfter=0)
_HEADER_CELL = ParagraphStyle("Header cell", parent=_CELL, fontName=_BOLD_FONT, textColor=colors.white)
_COUNT = ParagraphStyle("Count", parent=_CELL, alignment=TA_RIGHT)


def _snapshot_error(message: str) -> ApiError:
    return ApiError(
        422, "REPORT_SNAPSHOT_INVALID", message,
        {"component": "scar_pdf", "remediation": "Check the frozen report revision and its evidence references."},
    )


@cache
def _font_glyphs() -> frozenset[int]:
    directory = Path(reportlab.__file__).resolve().parent / "fonts"
    regular = TTFont(_REGULAR_FONT, str(directory / "Vera.ttf"))
    bold = TTFont(_BOLD_FONT, str(directory / "VeraBd.ttf"))
    pdfmetrics.registerFont(regular)
    pdfmetrics.registerFont(bold)
    pdfmetrics.registerFontFamily(_REGULAR_FONT, normal=_REGULAR_FONT, bold=_BOLD_FONT)
    return frozenset(code for code, glyph in regular.face.charToGlyph.items() if glyph and bold.face.charToGlyph.get(code))


def _text(value: object) -> bool:
    if not isinstance(value, str) or not value.strip() or _DISALLOWED_CONTROLS.search(value):
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    glyphs = _font_glyphs()
    for char in value:
        if char not in "\t\r\n" and ord(char) not in glyphs:
            raise _snapshot_error(f"Report text contains unsupported character U+{ord(char):04X}")
    return True


def _utc_time(value: object, field: str) -> datetime:
    try:
        if isinstance(value, datetime) and value.utcoffset() is not None:
            return value.astimezone(UTC)
    except (ValueError, OverflowError) as exc:
        raise _snapshot_error(f"Report {field} must have a valid timestamp") from exc
    raise _snapshot_error(f"Report {field} must have a time zone")


def _validate_evidence(ref: EvidenceRef) -> None:
    if not isinstance(ref, EvidenceRef) or not all(_text(value) for value in (ref.type, ref.id, ref.label)):
        raise _snapshot_error("Each evidence reference requires a type, ID, and label")
    if ref.sha256 is not None and (not isinstance(ref.sha256, str) or not _DIGEST.fullmatch(ref.sha256)):
        raise _snapshot_error("Evidence SHA-256 must be a 64-character hexadecimal digest")


def _validate_section(section: ReportSection) -> None:
    if not _text(section.title) or not isinstance(section.items, (tuple, list)):
        raise _snapshot_error(f"Report section {section.key} requires a title and an item sequence")
    for item in section.items:
        if not isinstance(item, ReportItem) or item.kind not in _ITEM_KINDS or not _text(item.text):
            raise _snapshot_error(f"Report section {section.key} contains an invalid item")
        if not isinstance(item.evidence, (tuple, list)):
            raise _snapshot_error(f"Report section {section.key} requires an evidence reference sequence")
        for ref in item.evidence:
            _validate_evidence(ref)


def _validate_snapshot(snapshot: ReportSnapshot) -> None:
    if not isinstance(snapshot, ReportSnapshot):
        raise _snapshot_error("Expected a frozen report snapshot")
    if not all(_text(value) for value in (snapshot.id, snapshot.revision_id, snapshot.source_id, snapshot.generator)):
        raise _snapshot_error("Report identification, source ID, and generator are required")
    if snapshot.status not in ("DRAFT", "APPROVED") or snapshot.source_type not in ("RECALL", "FOUR_M", "ALERT"):
        raise _snapshot_error("Report status or source type is invalid")
    if snapshot.evidence_source not in ("LIVE_CAPTURE", "PRERECORDED_EVAL", "SYNTHETIC_TEST", "MIXED"):
        raise _snapshot_error("Report evidence source is invalid")
    _utc_time(snapshot.created_at, "created_at")
    _utc_time(snapshot.as_of, "as_of")
    if snapshot.approved_at is not None:
        _utc_time(snapshot.approved_at, "approved_at")
    if snapshot.approved_by is not None and not _text(snapshot.approved_by):
        raise _snapshot_error("Report approver must be nonempty text")
    if not isinstance(snapshot.counts, dict) or snapshot.counts.keys() != {key for key, _, _ in _COUNT_ROWS} or any(
        type(value) is not int or value < 0 for value in snapshot.counts.values()
    ):
        raise _snapshot_error("Report counts require all six impact fields as nonnegative integers")
    if not isinstance(snapshot.sections, (tuple, list)) or len(snapshot.sections) != len(_SECTION_KEYS) or any(
        not isinstance(section, ReportSection) or section.key != key for section, key in zip(snapshot.sections, _SECTION_KEYS)
    ):
        raise _snapshot_error("Report must have one section for each step in D1 through D8 order")
    for section in snapshot.sections:
        _validate_section(section)


def _markup(text: str) -> str:
    return escape(text.replace("\r\n", "\n").replace("\r", "\n")).replace("\n", "<br/>")


def _paragraph(text: str, style: ParagraphStyle = _BODY) -> Paragraph:
    return Paragraph(_markup(text), style)


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _table(rows: list[list[Paragraph]], widths: list[float]) -> LongTable:
    return LongTable(rows, colWidths=widths, repeatRows=1, splitInRow=1, hAlign="LEFT", style=TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, _TINT]),
        ("BACKGROUND", (0, 0), (-1, 0), _ACCENT),
        ("LINEBELOW", (0, -1), (-1, -1), 0.5, _RULE),
    ]))


def _impact(counts: dict[str, int], width: float) -> LongTable:
    rows = [[_paragraph(label, _HEADER_CELL) for label in ("Scope", "Count", "Measure")]]
    rows.extend([
        _paragraph(label, _CELL), _paragraph(str(counts[key]), _COUNT), _paragraph(measure, _CELL),
    ] for key, label, measure in _COUNT_ROWS)
    return _table(rows, [width - 172, 78, 94])


def _references(evidence: tuple[EvidenceRef, ...], width: float) -> LongTable:
    rows = [[_paragraph(label, _HEADER_CELL) for label in ("Evidence type", "Evidence ID", "Label", "SHA-256 prefix")]]
    rows.extend([
        _paragraph(ref.type, _CELL),
        _paragraph(ref.id, _CELL),
        _paragraph(ref.label, _CELL),
        _paragraph(ref.sha256[:12] if ref.sha256 else "", _CELL),
    ] for ref in evidence)
    return _table(rows, [90, 105, width - 285, 90])


def _story(snapshot: ReportSnapshot, width: float) -> list:
    story = [_paragraph("8D / SCAR report", _TITLE)]
    metadata = [
        ("Status", snapshot.status),
        ("Report ID", snapshot.id),
        ("Revision ID", snapshot.revision_id),
        ("Source", f"{snapshot.source_type} / {snapshot.source_id}"),
        ("As of", _timestamp(snapshot.as_of)),
        ("Created at", _timestamp(snapshot.created_at)),
        ("Generator", snapshot.generator),
        ("Evidence source", snapshot.evidence_source),
    ]
    if snapshot.approved_by is not None:
        metadata.append(("Approved by", snapshot.approved_by))
    if snapshot.approved_at is not None:
        metadata.append(("Approved at", _timestamp(snapshot.approved_at)))
    story.extend(Paragraph(f"<b>{label}</b> &nbsp; {_markup(value)}", _BODY) for label, value in metadata)
    story.extend([CondPageBreak(65), _paragraph("Impact counts", _HEADING), _impact(snapshot.counts, width)])

    for section in snapshot.sections:
        story.extend([CondPageBreak(62), _paragraph(f"{section.key}  {section.title}", _HEADING)])
        if section.key in _HUMAN_REVIEW and not any(item.kind == "FACT" and item.evidence for item in section.items):
            story.append(_paragraph("PENDING HUMAN REVIEW", _PENDING))
        if not section.items:
            story.append(_paragraph("No items recorded."))
        for item in section.items:
            story.extend([_paragraph(item.kind, _TAG), _paragraph(item.text)])
            if item.evidence:
                story.extend([_references(item.evidence, width), Spacer(1, 9)])
    return story


def _page(canvas: Canvas, document: BaseDocTemplate, *, draft: bool, pdf_date: str) -> None:
    width, height = document.pagesize
    if document.page == 1:
        canvas.setDateFormatter(lambda *_: pdf_date)
    canvas.saveState()
    if draft:
        canvas.saveState()
        canvas.translate(width / 2, height / 2)
        canvas.rotate(37)
        canvas.setFont(_BOLD_FONT, 88)
        canvas.setFillColor(_SUBTLE)
        canvas.setFillAlpha(0.19)
        canvas.drawCentredString(0, -26, "DRAFT")
        canvas.restoreState()
    canvas.setFont(_BOLD_FONT, 9)
    canvas.setFillColor(_ACCENT)
    canvas.drawString(_MARGIN, height - 32, "AUTOQ-TRACE")
    canvas.setStrokeColor(_RULE)
    canvas.line(_MARGIN, height - 42, width - _MARGIN, height - 42)
    canvas.line(_MARGIN, 40, width - _MARGIN, 40)
    canvas.setFont(_REGULAR_FONT, 8)
    canvas.setFillColor(_SUBTLE)
    canvas.drawString(_MARGIN, 25, "8D / SCAR")
    canvas.drawRightString(width - _MARGIN, 25, f"Page {document.page}")
    canvas.restoreState()


def render_pdf(snapshot: ReportSnapshot) -> bytes:
    """Render all sections with ReportLab using the frozen UTC creation timestamp."""
    try:
        _validate_snapshot(snapshot)
        buffer = BytesIO()
        pdf = BaseDocTemplate(
            buffer, pagesize=A4, leftMargin=_MARGIN, rightMargin=_MARGIN, topMargin=59, bottomMargin=54,
            title="8D / SCAR report", author="AutoQ-Trace", creator="AutoQ-Trace",
            subject="Frozen corrective action evidence snapshot", invariant=True, pageCompression=1,
        )
        utc = snapshot.created_at.astimezone(UTC)
        pdf_date = f"D:{utc.year:04d}{utc.month:02d}{utc.day:02d}{utc.hour:02d}{utc.minute:02d}{utc.second:02d}+00'00'"
        page = partial(_page, draft=snapshot.status == "DRAFT", pdf_date=pdf_date)
        frame = Frame(pdf.leftMargin, pdf.bottomMargin, pdf.width, pdf.height)
        pdf.addPageTemplates(PageTemplate(frames=[frame], onPageEnd=page))
        # The default frame adds 6 pt padding on each side.
        pdf.build(_story(snapshot, pdf.width - 12))
        data = buffer.getvalue()
        if not data.startswith(b"%PDF-") or not data.rstrip().endswith(b"%%EOF"):
            raise ValueError("ReportLab did not write a complete PDF")
        return data
    except ApiError:
        raise
    except Exception as exc:
        raise ApiError(
            503, "REPORT_RENDER_FAILED", "The report PDF could not be rendered",
            {"component": "scar_pdf", "remediation": "Retry the download or contact the report service operator."},
        ) from exc
