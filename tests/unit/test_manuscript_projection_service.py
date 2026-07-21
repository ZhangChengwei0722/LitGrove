from __future__ import annotations

from importlib.metadata import version
from pathlib import Path

import pytest

from research_kb.errors import ResearchKBError
from research_kb.services.manuscript_projection import ManuscriptProjectionService
from research_kb.storage.json_io import file_sha256
from tests.docx_helpers import write_synthetic_docx
from tests.pdf_helpers import write_synthetic_pdf
from tests.runtime_helpers import make_runtime_workspace


def _tree_snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _docx_body() -> str:
    return """
<w:p>
  <w:pPr><w:pStyle w:val="Heading1"/></w:pPr>
  <w:r><w:t>Invented manuscript heading</w:t></w:r>
</w:p>
<w:p>
  <w:r><w:t>Invented visible text</w:t></w:r>
  <w:del><w:r><w:delText> deleted revision</w:delText></w:r></w:del>
  <w:r><w:tab/><w:t>with tab</w:t><w:br/><w:t>and break</w:t></w:r>
</w:p>
<w:tbl>
  <w:tr>
    <w:tc><w:p><w:r><w:t>Invented cell one</w:t></w:r></w:p></w:tc>
    <w:tc><w:p><w:r><w:t>Invented cell two</w:t></w:r></w:p></w:tc>
  </w:tr>
</w:tbl>
<w:p><w:r><w:t>Invented closing paragraph</w:t></w:r></w:p>
"""


def test_docx_projection_preserves_order_styles_tables_and_zero_write_boundary(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    source = write_synthetic_docx(
        layout.source_roots["alpha-sources"] / "draft.docx",
        body_xml=_docx_body(),
    )
    source_before = source.read_bytes()
    knowledge_before = _tree_snapshot(layout.knowledge_root)

    first = ManuscriptProjectionService(layout).inspect(source=source)
    second = ManuscriptProjectionService(layout).inspect(source=source)

    assert first == second
    assert first["status"] == "success"
    assert first["interface_version"] == "1.0"
    assert first["persistent_writes"] == 0
    assert first["document"]["format"] == "docx"
    assert first["document"]["source"] == {
        "root_id": "alpha-sources",
        "relative_path": "draft.docx",
    }
    assert first["document"]["source_fingerprint"] == {
        "algorithm": "sha256",
        "value": file_sha256(source),
    }
    assert first["document"]["parser"] == {
        "adapter": "ooxml-stdlib",
        "version": "1.0",
    }
    assert first["document"]["unit_kind"] == "paragraph"
    assert first["document"]["unit_count"] == 5
    assert first["document"]["extracted_character_count"] == sum(
        len(unit["text"]) for unit in first["units"]
    )
    assert first["document"]["coverage_limits"]
    assert [unit["locator"] for unit in first["units"]] == [
        "docx:paragraph:1",
        "docx:paragraph:2",
        "docx:paragraph:3",
        "docx:paragraph:4",
        "docx:paragraph:5",
    ]
    assert [unit["text"] for unit in first["units"]] == [
        "Invented manuscript heading",
        "Invented visible text\twith tab\nand break",
        "Invented cell one",
        "Invented cell two",
        "Invented closing paragraph",
    ]
    assert first["units"][0]["heading_level"] == 1
    assert first["units"][0]["style_id"] == "Heading1"
    assert first["units"][0]["style_name"] == "Heading 1"
    assert first["units"][0]["container"] == {
        "kind": "body",
        "table_index": None,
        "row_index": None,
        "cell_index": None,
    }
    assert first["units"][2]["container"] == {
        "kind": "table",
        "table_index": 1,
        "row_index": 1,
        "cell_index": 1,
    }
    assert first["units"][3]["container"]["cell_index"] == 2
    assert str(tmp_path) not in str(first)
    assert source.read_bytes() == source_before
    assert _tree_snapshot(layout.knowledge_root) == knowledge_before


def test_pdf_projection_reuses_exact_pdfplumber_identity_and_page_order(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    source = write_synthetic_pdf(
        layout.source_roots["alpha-sources"] / "draft.pdf",
        ["Invented first manuscript page.", "", "Invented third manuscript page."],
    )
    before = _tree_snapshot(layout.config.path.parent)

    report = ManuscriptProjectionService(layout).inspect(source=source)

    assert report["document"]["format"] == "pdf"
    assert report["document"]["parser"] == {
        "adapter": "pdfplumber",
        "version": version("pdfplumber"),
    }
    assert report["document"]["unit_kind"] == "pdf_page"
    assert report["document"]["unit_count"] == 3
    assert [unit["locator"] for unit in report["units"]] == [
        "pdf:page:1",
        "pdf:page:2",
        "pdf:page:3",
    ]
    assert report["units"][1]["text"] == ""
    assert all(unit["container"] == {
        "kind": "page",
        "table_index": None,
        "row_index": None,
        "cell_index": None,
    } for unit in report["units"])
    assert _tree_snapshot(layout.config.path.parent) == before


@pytest.mark.parametrize(
    ("case", "expected_code"),
    (
        ("relative", "RKBC-007"),
        ("outside", "RKBC-007"),
        ("unsupported_extension", "RKBC-035"),
        ("malformed_docx", "RKBC-035"),
        ("malformed_xml", "RKBC-035"),
        ("empty_docx", "RKBC-035"),
        ("blank_pdf", "RKBC-035"),
    ),
)
def test_manuscript_projection_rejects_unsafe_or_unsupported_sources(
    tmp_path: Path,
    case: str,
    expected_code: str,
) -> None:
    layout = make_runtime_workspace(tmp_path)
    root = layout.source_roots["alpha-sources"]
    if case == "relative":
        source = Path("draft.docx")
    elif case == "outside":
        source = write_synthetic_docx(tmp_path / "outside.docx", body_xml=_docx_body())
    elif case == "unsupported_extension":
        source = root / "draft.txt"
        source.write_text("Invented manuscript text.\n", encoding="utf-8", newline="\n")
    elif case == "malformed_docx":
        source = root / "malformed.docx"
        source.write_bytes(b"not an OOXML archive")
    elif case == "malformed_xml":
        source = write_synthetic_docx(root / "malformed-xml.docx", body_xml="<w:p>")
    elif case == "empty_docx":
        source = write_synthetic_docx(root / "empty.docx", body_xml="<w:p/>")
    else:
        source = write_synthetic_pdf(root / "blank.pdf", ["", ""])
    before = _tree_snapshot(layout.config.path.parent)

    with pytest.raises(ResearchKBError) as caught:
        ManuscriptProjectionService(layout).inspect(source=source)

    assert caught.value.diagnostic.code == expected_code
    assert str(tmp_path) not in caught.value.diagnostic.message
    assert _tree_snapshot(layout.config.path.parent) == before


def test_manuscript_projection_enforces_source_archive_unit_and_text_bounds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    layout = make_runtime_workspace(tmp_path)
    source = write_synthetic_docx(
        layout.source_roots["alpha-sources"] / "bounded.docx",
        body_xml=_docx_body(),
    )
    service = ManuscriptProjectionService(layout)

    monkeypatch.setattr("research_kb.services.manuscript_projection.MAX_SOURCE_BYTES", source.stat().st_size - 1)
    with pytest.raises(ResearchKBError) as caught:
        service.inspect(source=source)
    assert caught.value.diagnostic.code == "RKBC-030"

    monkeypatch.setattr("research_kb.services.manuscript_projection.MAX_SOURCE_BYTES", 64 * 1024 * 1024)
    monkeypatch.setattr("research_kb.manuscript.ooxml.MAX_ARCHIVE_ENTRIES", 2)
    with pytest.raises(ResearchKBError) as caught:
        service.inspect(source=source)
    assert caught.value.diagnostic.code == "RKBC-030"

    monkeypatch.setattr("research_kb.manuscript.ooxml.MAX_ARCHIVE_ENTRIES", 2_000)
    monkeypatch.setattr("research_kb.manuscript.ooxml.MAX_UNCOMPRESSED_BYTES", 1)
    with pytest.raises(ResearchKBError) as caught:
        service.inspect(source=source)
    assert caught.value.diagnostic.code == "RKBC-030"

    monkeypatch.setattr("research_kb.manuscript.ooxml.MAX_UNCOMPRESSED_BYTES", 128 * 1024 * 1024)
    monkeypatch.setattr("research_kb.services.manuscript_projection.MAX_PROJECTED_UNITS", 1)
    with pytest.raises(ResearchKBError) as caught:
        service.inspect(source=source)
    assert caught.value.diagnostic.code == "RKBC-030"

    monkeypatch.setattr("research_kb.services.manuscript_projection.MAX_PROJECTED_UNITS", 20_000)
    monkeypatch.setattr("research_kb.services.manuscript_projection.MAX_EXTRACTED_CHARACTERS", 1)
    with pytest.raises(ResearchKBError) as caught:
        service.inspect(source=source)
    assert caught.value.diagnostic.code == "RKBC-030"


def test_manuscript_projection_rejects_source_change_during_extraction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    layout = make_runtime_workspace(tmp_path)
    source = write_synthetic_docx(
        layout.source_roots["alpha-sources"] / "changing.docx",
        body_xml=_docx_body(),
    )
    initial = file_sha256(source)
    hashes = iter((initial, "f" * 64))
    monkeypatch.setattr(
        "research_kb.services.manuscript_projection.file_sha256",
        lambda _: next(hashes),
    )

    with pytest.raises(ResearchKBError) as caught:
        ManuscriptProjectionService(layout).inspect(source=source)

    assert caught.value.diagnostic.code == "RKBC-009"


def test_manuscript_pdf_preserves_missing_optional_dependency_diagnostic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    layout = make_runtime_workspace(tmp_path)
    source = write_synthetic_pdf(
        layout.source_roots["alpha-sources"] / "missing-extra.pdf",
        ["Invented manuscript page."],
    )

    def missing_dependency(name: str):
        raise ModuleNotFoundError(name)

    monkeypatch.setattr("research_kb.parse.pdfplumber_adapter.import_module", missing_dependency)
    with pytest.raises(ResearchKBError) as caught:
        ManuscriptProjectionService(layout).inspect(source=source)

    assert caught.value.diagnostic.code == "RKBC-028"
    assert caught.value.diagnostic.record_kind == "manuscript-projection"
    assert caught.value.diagnostic.record_id is None
