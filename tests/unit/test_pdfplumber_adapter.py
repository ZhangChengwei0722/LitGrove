from __future__ import annotations

from importlib.metadata import version
from pathlib import Path

import pytest

from research_kb.errors import ResearchKBError
from research_kb.parse.pdfplumber_adapter import PdfPlumberAdapter, PdfPlumberTextFlowAdapter
from research_kb.storage.json_io import file_sha256
from tests.pdf_helpers import write_synthetic_pdf, write_synthetic_two_column_pdf


PAPER_ID = "paper_a1111111-1111-4111-8111-111111111111"
PARSE_RUN_ID = "event_a1111111-1111-4111-8111-111111111111"


def _parse(adapter: PdfPlumberAdapter, source: Path) -> list[dict]:
    return list(adapter.parse(source, paper_id=PAPER_ID, parse_run_id=PARSE_RUN_ID))


def test_pdf_adapter_extracts_one_lf_normalized_row_per_page_and_reports_exact_version(tmp_path: Path) -> None:
    source = write_synthetic_pdf(
        tmp_path / "synthetic-study.PDF",
        ["Invented first line.\nInvented second line.", "Repeated invented response."],
    )
    source_before = file_sha256(source)
    adapter = PdfPlumberAdapter()

    pages = _parse(adapter, source)

    assert adapter.name == "pdfplumber"
    assert adapter.version == version("pdfplumber")
    assert [item["pdf_page"] for item in pages] == [1, 2]
    assert [item["printed_page"] for item in pages] == [None, None]
    assert [item["locator"] for item in pages] == ["page:1:text", "page:2:text"]
    assert "Invented first line." in pages[0]["text"]
    assert all("\r" not in item["text"] for item in pages)
    assert file_sha256(source) == source_before


def test_text_flow_adapter_preserves_synthetic_two_column_content_stream_order(tmp_path: Path) -> None:
    source = write_synthetic_two_column_pdf(
        tmp_path / "synthetic-two-column.pdf",
        ["Left column first.", "Left column second."],
        ["Right column first.", "Right column second."],
    )
    source_before = file_sha256(source)

    legacy_text = _parse(PdfPlumberAdapter(), source)[0]["text"]
    text_flow = PdfPlumberTextFlowAdapter()
    text_flow_text = _parse(text_flow, source)[0]["text"]

    assert legacy_text.index("Right column first.") < legacy_text.index("Left column second.")
    assert text_flow.name == "pdfplumber-text-flow"
    assert text_flow.version == version("pdfplumber")
    assert text_flow.extraction_options == {
        "x_tolerance": 1,
        "y_tolerance": 3,
        "layout": False,
        "use_text_flow": True,
    }
    assert text_flow_text.index("Left column second.") < text_flow_text.index("Right column first.")
    assert file_sha256(source) == source_before


def test_pdf_adapter_retains_blank_page_when_document_has_extractable_text(tmp_path: Path) -> None:
    source = write_synthetic_pdf(
        tmp_path / "blank-between-text.pdf",
        ["Invented page one.", "", "Invented page three."],
    )

    pages = _parse(PdfPlumberAdapter(), source)

    assert [item["pdf_page"] for item in pages] == [1, 2, 3]
    assert pages[1]["text"] == ""


@pytest.mark.parametrize(
    "case",
    ["all_blank", "bad_signature", "malformed_pdf", "wrong_extension", "encrypted"],
)
def test_pdf_adapter_rejects_unsupported_sources_with_bounded_diagnostic(tmp_path: Path, case: str) -> None:
    source = tmp_path / ("private-absolute-source.txt" if case == "wrong_extension" else "private-absolute-source.pdf")
    if case == "all_blank":
        write_synthetic_pdf(source, ["", ""])
    elif case == "bad_signature":
        source.write_bytes(b"not a PDF")
    elif case == "malformed_pdf":
        source.write_bytes(bytes((37, 80, 68, 70, 45)) + b"1.7\nmalformed invented bytes")
    elif case == "wrong_extension":
        write_synthetic_pdf(source, ["Invented text."])
    else:
        write_synthetic_pdf(source, ["Invented encrypted text."], password="fixture-password")

    with pytest.raises(ResearchKBError) as caught:
        _parse(PdfPlumberAdapter(), source)

    assert caught.value.diagnostic.code == "RKBC-029"
    assert str(source) not in caught.value.diagnostic.message
    assert "Invented" not in caught.value.diagnostic.message


def test_pdf_adapter_reports_missing_optional_dependency_without_import_time_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = write_synthetic_pdf(tmp_path / "dependency-boundary.pdf", ["Invented text."])

    def missing_dependency(name: str):
        raise ModuleNotFoundError(name)

    monkeypatch.setattr("research_kb.parse.pdfplumber_adapter.import_module", missing_dependency)

    with pytest.raises(ResearchKBError) as caught:
        _parse(PdfPlumberAdapter(), source)

    assert caught.value.diagnostic.code == "RKBC-028"
    assert "pdfplumber" in caught.value.diagnostic.message
