from __future__ import annotations

from pathlib import Path
from typing import Any

from research_kb.errors import (
    MANUSCRIPT_SOURCE_UNSUPPORTED,
    PARSE_ADAPTER_UNAVAILABLE,
    PARSE_SOURCE_UNSUPPORTED,
    Diagnostic,
    ResearchKBError,
)
from research_kb.parse.pdfplumber_adapter import PdfPlumberAdapter


PDF_COVERAGE_LIMITS = [
    "ocr_not_performed",
    "layout_not_inferred",
    "figures_tables_and_images_not_interpreted",
    "supplements_not_loaded",
]


class PdfManuscriptAdapter:
    name = "pdfplumber"

    def project(self, source: Path) -> dict[str, Any]:
        adapter = PdfPlumberAdapter()
        try:
            version = adapter.version
            pages = adapter.parse(
                source,
                paper_id="manuscript_projection",
                parse_run_id="manuscript_projection",
            )
        except ResearchKBError as error:
            if error.diagnostic.code == PARSE_ADAPTER_UNAVAILABLE:
                raise _unavailable() from error
            if error.diagnostic.code != PARSE_SOURCE_UNSUPPORTED:
                raise
            raise _unsupported("manuscript PDF has no supported extractable page text") from error
        return {
            "parser": {"adapter": self.name, "version": version},
            "unit_kind": "pdf_page",
            "coverage_limits": list(PDF_COVERAGE_LIMITS),
            "units": [
                {
                    "unit_index": page["pdf_page"],
                    "locator": f"pdf:page:{page['pdf_page']}",
                    "text": page["text"],
                    "heading_level": None,
                    "style_id": None,
                    "style_name": None,
                    "container": {
                        "kind": "page",
                        "table_index": None,
                        "row_index": None,
                        "cell_index": None,
                    },
                }
                for page in pages
            ],
        }


def _unsupported(message: str) -> ResearchKBError:
    return ResearchKBError(
        Diagnostic(
            MANUSCRIPT_SOURCE_UNSUPPORTED,
            "manuscript-projection",
            None,
            "/source",
            message,
        )
    )


def _unavailable() -> ResearchKBError:
    return ResearchKBError(
        Diagnostic(
            PARSE_ADAPTER_UNAVAILABLE,
            "manuscript-projection",
            None,
            "/document/parser",
            "PDF manuscript projection requires the research-kb-core[pdf] optional dependency",
        )
    )


__all__ = ["PdfManuscriptAdapter"]
