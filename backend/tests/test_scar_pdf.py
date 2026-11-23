import base64
import re
import zlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from hashlib import sha256

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core import errors
from app.core.auth import Principal, current_principal
from app.db.session import get_db
from app.features.scar_pdf import service
from app.features.scar_pdf.router import router
from app.ports import EvidenceRef, ReportItem, ReportSection, ReportSnapshot, ports

pytestmark = pytest.mark.nodb

CREATED = datetime(2026, 11, 23, 15, 41, 12, tzinfo=timezone(timedelta(hours=5, minutes=30)))
AS_OF = datetime(2026, 11, 23, 10, tzinfo=UTC)
COUNTS = {"affected_units": 125, "held_packages": 5, "at_plant": 85, "loaded_not_departed": 20, "in_transit": 13, "delivered": 7}
DIGEST = sha256(b"SYNTHETIC_TEST inspection evidence").hexdigest()
REFERENCE = EvidenceRef("INSPECTION", "insp-example", "Synthetic inspection file", DIGEST)
TITLES = ("Team", "Problem", "Containment", "Root cause", "Corrective actions", "Validation", "Prevention", "Closure")


def _snapshot(**changes):
    items = {
        "D2": (ReportItem("FACT", "A mixed lot was held for review.", (REFERENCE,)),),
        "D4": (ReportItem("HYPOTHESIS", "Tool wear is a possible cause.", (REFERENCE,)),),
        "D6": (ReportItem("PROPOSED", "Repeat the validation inspection.", (REFERENCE,)),),
        "D8": (ReportItem("PENDING_HUMAN", "Manager review is pending."),),
    }
    sections = tuple(ReportSection(f"D{i}", title, items.get(f"D{i}", ())) for i, title in enumerate(TITLES, start=1))
    return replace(ReportSnapshot(
        "report-001", "revision-001", "DRAFT", "RECALL", "recall-001", "template-v1",
        CREATED, AS_OF, COUNTS, "SYNTHETIC_TEST", sections,
    ), **changes)


def _with_items(snapshot, key, *items):
    return replace(snapshot, sections=tuple(
        replace(section, items=items) if section.key == key else section for section in snapshot.sections
    ))


def _streams(pdf):
    # Inspect direct-length streams emitted by the locked ReportLab renderer.
    # Production PDF parsing is outside the module contract and needs no new dependency.
    for match in re.finditer(rb"/Filter (?P<filter>[^\r\n]+?) /Length (?P<size>[0-9]+)\s*>>\s*stream\n", pdf):
        data = pdf[match.end():match.end() + int(match["size"])]
        if b"/ASCII85Decode" in match["filter"]:
            data = base64.a85decode(data, adobe=True)
        if b"/FlateDecode" in match["filter"]:
            data = zlib.decompress(data)
        yield data


def _pages(pdf):
    pages = [stream for stream in _streams(pdf) if b"(AUTOQ-TRACE) Tj" in stream]
    assert len(pages) == len(re.findall(rb"/Type /Page\b", pdf)) > 0
    return pages


def test_report_bytes_are_deterministic_and_creation_metadata_uses_snapshot_time():
    snapshot = _snapshot()
    rendered = service.render_pdf(snapshot)
    assert rendered == service.render_pdf(snapshot)
    assert rendered.startswith(b"%PDF-") and rendered.rstrip().endswith(b"%%EOF")
    assert b"/CreationDate (D:20261123101112+00'00')" in rendered
    assert b"/ModDate (D:20261123101112+00'00')" in rendered


def test_report_preserves_section_order_scope_counts_and_references():
    rendered = b"\n".join(_pages(service.render_pdf(_snapshot())))
    positions = [rendered.index(f"D{i} {title}".encode()) for i, title in enumerate(TITLES, start=1)]
    assert positions == sorted(positions)
    for value in (
        b"report-001", b"revision-001", b"RECALL / recall-001", b"2026-11-23T10:00:00Z", b"template-v1",
        b"SYNTHETIC_TEST", b"At plant", b"Loaded, awaiting departure", b"In transit", b"Delivered",
        b"(125) Tj", b"(85) Tj", b"(20) Tj", b"(13) Tj", b"(7) Tj", b"(5) Tj",
        b"(Units) Tj", b"(Packages) Tj", b"FACT", b"HYPOTHESIS", b"PROPOSED", b"PENDING_HUMAN",
        b"INSPECTION", b"insp-example", b"Synthetic inspection file", DIGEST[:12].encode(),
    ):
        assert value in rendered
    assert DIGEST.encode() not in rendered
    assert rendered.count(b"PENDING HUMAN REVIEW") == 3


def test_fact_with_well_formed_evidence_clears_banner_even_without_a_file_digest():
    snapshot = _snapshot(status="APPROVED", approved_by="manager-001", approved_at=CREATED)
    snapshot = _with_items(snapshot, "D4", ReportItem("FACT", "Root cause verified during review.", (replace(REFERENCE, sha256=None),)))
    rendered = b"\n".join(_pages(service.render_pdf(snapshot)))
    assert b"(DRAFT) Tj" not in rendered
    assert b"manager-001" in rendered
    assert b"Root cause verified during review." in rendered
    assert rendered.count(b"PENDING HUMAN REVIEW") == 2


def test_fact_without_evidence_cannot_clear_pending_review():
    snapshot = _with_items(_snapshot(), "D4", ReportItem("FACT", "The fact has no evidence reference."))
    text = b"\n".join(_pages(service.render_pdf(snapshot)))
    assert text.count(b"PENDING HUMAN REVIEW") == 3


def test_large_single_item_table_paginates_all_references_and_keeps_watermark_visible():
    evidence = tuple(EvidenceRef(
        "INSPECTION", f"evidence-{i:03d}", "X" * 10000 + " LabelTail" if i == 101 else f"Record {i}", DIGEST,
    ) for i in range(200))
    snapshot = _with_items(_snapshot(), "D2", ReportItem("FACT", "X" * 14000 + " ItemTail", evidence))
    pages = _pages(service.render_pdf(snapshot))
    text = b"\n".join(pages)
    assert len(pages) >= 5
    assert all(b"(DRAFT) Tj" in page for page in pages)
    assert any(b"Evidence type" in page and page.index(b"Evidence type") < page.index(b"(DRAFT) Tj") for page in pages)
    assert text.count(b"Evidence type") > 1
    assert b"LabelTail" in text and b"ItemTail" in text
    for i in range(200):
        assert f"(evidence-{i:03d}) Tj".encode() in text


def test_user_text_is_escaped_and_embedded_font_preserves_accents():
    reference = replace(REFERENCE, label="café & <b>literal label</b>")
    snapshot = _with_items(_snapshot(), "D2", ReportItem(
        "FACT", "<link href='https://example.invalid/'>literal link</link> & details", (reference,),
    ))
    pdf = service.render_pdf(snapshot)
    rendered = b"\n".join(_pages(pdf))
    # ReportLab emits separate Tj commands around escaped markup characters.
    literals = b"".join(re.findall(rb"\(([^()\r\n]*)\) Tj", rendered))
    assert b"<link href='https://example.invalid/'>literal link</link> & details" in literals
    assert b"<b>literal label</b>" in literals
    assert b"/Annots" not in pdf and b"/URI" not in pdf
    assert b"/FontFile2" in pdf
    assert any(b"<00E9>" in stream for stream in _streams(pdf))


@pytest.mark.parametrize("changes", [
    {"status": "REVIEWED"}, {"source_type": "UNKNOWN"}, {"evidence_source": "PRODUCTION"},
    {"created_at": CREATED.replace(tzinfo=None)}, {"counts": {**COUNTS, "at_plant": True}},
    {"counts": {**COUNTS, "in_transit": -1}}, {"counts": {"affected_units": 30}}, {"sections": ()},
])
def test_malformed_snapshot_raises_typed_422(changes):
    with pytest.raises(errors.ApiError) as failure:
        service.render_pdf(_snapshot(**changes))
    assert (failure.value.status, failure.value.code) == (422, "REPORT_SNAPSHOT_INVALID")
    assert failure.value.details["component"] == "scar_pdf"
    assert failure.value.details["remediation"]


def test_unrenderable_glyph_or_malformed_evidence_never_produces_a_partial_pdf():
    cases = [
        replace(REFERENCE, label="Synthetic label \U0001f4e6"),
        replace(REFERENCE, sha256="not-a-digest"),
    ]
    for reference in cases:
        snapshot = _with_items(_snapshot(), "D2", ReportItem("FACT", "Evidence supplied.", (reference,)))
        with pytest.raises(errors.ApiError) as failure:
            service.render_pdf(snapshot)
        assert (failure.value.status, failure.value.code) == (422, "REPORT_SNAPSHOT_INVALID")


def test_renderer_failure_raises_typed_503(monkeypatch):
    def unavailable(*args, **kwargs):
        raise RuntimeError("Test renderer failure")

    monkeypatch.setattr(service.BaseDocTemplate, "build", unavailable)
    with pytest.raises(errors.ApiError) as failure:
        service.render_pdf(_snapshot())
    assert (failure.value.status, failure.value.code) == (503, "REPORT_RENDER_FAILED")


def _client(persistence, principal=None):
    app = FastAPI()
    errors.install(app)
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[ports] = lambda: persistence
    app.dependency_overrides[get_db] = lambda: None
    if principal is not None:
        app.dependency_overrides[current_principal] = lambda: principal
    return TestClient(app)


def test_authenticated_download_records_exact_rendered_bytes_and_sanitizes_filename():
    snapshot = _snapshot(id='report/unsafe"\r\nInjected: café', revision_id="rev/2")
    principal = Principal("u1", "ravi", "Ravi", ("operator",))
    calls = []

    class FakePort:
        def load_snapshot(self, db, report_id, actor):
            assert db is None and actor is principal
            assert report_id == "report-001"
            calls.append("load")
            return snapshot

        def record_pdf(self, db, snapshot_argument, data, actor):
            assert db is None and actor is principal and snapshot_argument is snapshot
            assert data.startswith(b"%PDF-")
            calls.append(data)
            return sha256(data).hexdigest()

    with _client(FakePort(), principal) as client:
        response = client.get("/api/v1/reports/report-001/pdf")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert re.fullmatch(r'attachment; filename="8D-[A-Za-z0-9._-]+\.pdf"', response.headers["content-disposition"])
    assert "injected" not in response.headers
    assert response.headers["cache-control"] == "private, no-store"
    assert calls == ["load", response.content]


def test_download_requires_authentication_and_never_records_invalid_snapshot():
    class FakePort:
        def __init__(self):
            self.loads = 0

        def load_snapshot(self, db, report_id, actor):
            self.loads += 1
            return _snapshot(sections=())

        def record_pdf(self, *args):
            pytest.fail("Invalid snapshots cannot produce a PDF record")

    port = FakePort()
    with _client(port) as client:
        denied = client.get("/api/v1/reports/report-001/pdf")
    assert denied.status_code == 401 and port.loads == 0
    with _client(port, Principal("u1", "ravi", "Ravi", ("operator",))) as client:
        malformed = client.get("/api/v1/reports/report-001/pdf")
    assert malformed.status_code == 422 and port.loads == 1
    assert malformed.json()["error"]["code"] == "REPORT_SNAPSHOT_INVALID"
