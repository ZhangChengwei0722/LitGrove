from __future__ import annotations

from collections.abc import Callable

from research_kb import __version__
from research_kb.contracts.versions import SUPPORTED_VERSION
from research_kb.errors import PARSE_ADAPTER_UNAVAILABLE, ResearchKBError
from research_kb.parse.pdfplumber_adapter import probe_pdfplumber_version
from research_kb.parse.synthetic_text import SyntheticTextAdapter
from research_kb.workspace_validation import CURRENT_LAYOUT_CONTRACT_VERSION


PdfplumberProbe = Callable[[], str | None]


class CapabilityService:
    def __init__(self, *, pdfplumber_probe: PdfplumberProbe = probe_pdfplumber_version):
        self.pdfplumber_probe = pdfplumber_probe

    def show(self) -> dict[str, object]:
        pdf_version = self._pdfplumber_version()
        adapters = [
            {
                "adapter": "pdfplumber",
                "availability": "available" if pdf_version is not None else "dependency_missing",
                "version": pdf_version,
                "diagnostic_code": None if pdf_version is not None else PARSE_ADAPTER_UNAVAILABLE,
            },
            {
                "adapter": SyntheticTextAdapter.name,
                "availability": "available",
                "version": SyntheticTextAdapter.version,
                "diagnostic_code": None,
            },
        ]
        adapters.sort(key=lambda item: item["adapter"])
        return {
            "status": "success",
            "interface_version": "1.0",
            "core": {
                "version": __version__,
                "contract_versions": [SUPPORTED_VERSION],
                "layout_versions": [CURRENT_LAYOUT_CONTRACT_VERSION],
            },
            "parse_adapters": adapters,
            "mutation_record_kinds": sorted(
                ("registry-paper", "paper-card", "evidence", "review-queue", "question-mapping")
            ),
            "read_commands": sorted(
                (
                    "capability show",
                    "guardian check",
                    "paper context",
                    "paper status",
                    "parse show",
                    "question list",
                    "question show",
                    "question render",
                )
            ),
            "features": {
                "real_pdf_parse": True,
                "stdin_json_handoff": True,
                "review_runtime": False,
                "step7_runtime": False,
            },
        }

    def _pdfplumber_version(self) -> str | None:
        try:
            return self.pdfplumber_probe()
        except ResearchKBError as error:
            if error.diagnostic.code == PARSE_ADAPTER_UNAVAILABLE:
                return None
            raise


__all__ = ["CapabilityService"]
