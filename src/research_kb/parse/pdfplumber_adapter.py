from __future__ import annotations

from importlib import import_module
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path
from typing import Any

from research_kb.errors import (
    PARSE_ADAPTER_UNAVAILABLE,
    PARSE_SOURCE_UNSUPPORTED,
    Diagnostic,
    ResearchKBError,
)


EXTRACTION_OPTIONS = {
    "x_tolerance": 3,
    "y_tolerance": 3,
    "layout": False,
}
PDF_SIGNATURE = bytes((37, 80, 68, 70, 45))


class PdfPlumberAdapter:
    name = "pdfplumber"

    @property
    def version(self) -> str:
        try:
            return package_version("pdfplumber")
        except PackageNotFoundError as error:
            raise _unavailable_error(None) from error

    def parse(
        self,
        source: Path,
        *,
        paper_id: str,
        parse_run_id: str,
    ) -> list[dict[str, Any]]:
        del parse_run_id
        pdfplumber = _load_pdfplumber(paper_id)
        _ = self.version
        if source.suffix.lower() != ".pdf" or not source.is_file():
            raise _unsupported_error(paper_id, "registered source is not a regular PDF file")
        try:
            with source.open("rb") as stream:
                signature = stream.read(5)
        except OSError as error:
            raise _unsupported_error(paper_id, "registered PDF source cannot be read") from error
        if signature != PDF_SIGNATURE:
            raise _unsupported_error(paper_id, "registered source does not have a PDF signature")

        try:
            with pdfplumber.open(source) as document:
                pages = [
                    {
                        "pdf_page": page_number,
                        "printed_page": None,
                        "text": _normalize_text(page.extract_text(**EXTRACTION_OPTIONS) or ""),
                        "locator": f"page:{page_number}:text",
                    }
                    for page_number, page in enumerate(document.pages, start=1)
                ]
        except ResearchKBError:
            raise
        except Exception as error:
            raise _unsupported_error(paper_id, "registered PDF source cannot be parsed") from error

        if not pages:
            raise _unsupported_error(paper_id, "registered PDF source contains no pages")
        if all(not page["text"].strip() for page in pages):
            raise _unsupported_error(paper_id, "registered PDF source has no extractable page text")
        return pages


def probe_pdfplumber_version() -> str:
    _load_pdfplumber(None)
    try:
        return package_version("pdfplumber")
    except PackageNotFoundError as error:
        raise _unavailable_error(None) from error


def _load_pdfplumber(paper_id: str | None) -> Any:
    try:
        return import_module("pdfplumber")
    except (ModuleNotFoundError, ImportError) as error:
        raise _unavailable_error(paper_id) from error


def _normalize_text(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n")


def _unavailable_error(paper_id: str | None) -> ResearchKBError:
    return ResearchKBError(
        Diagnostic(
            PARSE_ADAPTER_UNAVAILABLE,
            "parsed-page",
            paper_id,
            "/parser/adapter",
            "pdfplumber adapter requires the research-kb-core[pdf] optional dependency",
        )
    )


def _unsupported_error(paper_id: str, message: str) -> ResearchKBError:
    return ResearchKBError(
        Diagnostic(
            PARSE_SOURCE_UNSUPPORTED,
            "parsed-page",
            paper_id,
            "/source_ref",
            message,
        )
    )
