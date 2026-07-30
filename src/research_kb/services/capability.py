from __future__ import annotations

from collections.abc import Callable

from research_kb import __version__
from research_kb.contracts.versions import SUPPORTED_VERSION
from research_kb.errors import PARSE_ADAPTER_UNAVAILABLE, ResearchKBError
from research_kb.parse.pdfplumber_adapter import (
    PdfPlumberAdapter,
    PdfPlumberTextFlowAdapter,
    probe_pdfplumber_version,
)
from research_kb.parse.synthetic_text import SyntheticTextAdapter
from research_kb.workspace_validation import CURRENT_LAYOUT_CONTRACT_VERSION


PdfplumberProbe = Callable[[], str | None]


class CapabilityService:
    def __init__(self, *, pdfplumber_probe: PdfplumberProbe = probe_pdfplumber_version):
        self.pdfplumber_probe = pdfplumber_probe

    def show(self) -> dict[str, object]:
        pdf_version = self._pdfplumber_version()
        adapters = [
            *[
                {
                    "adapter": adapter.name,
                    "availability": "available" if pdf_version is not None else "dependency_missing",
                    "version": pdf_version,
                    "diagnostic_code": None if pdf_version is not None else PARSE_ADAPTER_UNAVAILABLE,
                }
                for adapter in (PdfPlumberAdapter, PdfPlumberTextFlowAdapter)
            ],
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
            "discovery_connectors": [
                {
                    "connector": "europe-pmc",
                    "availability": "available",
                    "network_required": True,
                }
            ],
            "mutation_record_kinds": sorted(
                (
                    "registry-paper",
                    "paper-card",
                    "evidence",
                    "review-queue",
                    "review-memory",
                    "question-mapping",
                    "step7-synthesis",
                    "step7-review-angle",
                    "step7-insight",
                    "step7-cross-view",
                )
            ),
            "read_commands": sorted(
                (
                    "capability show",
                    "discovery search",
                    "discovery list",
                    "discovery resolve",
                    "discovery show",
                    "guardian check",
                    "job list",
                    "job show",
                    "intake inspect",
                    "intake inspect-acquired",
                    "manuscript inspect",
                    "paper context",
                    "paper status",
                    "parse show",
                    "question list",
                    "question show",
                    "question render",
                    "review context",
                    "source list",
                    "source scan",
                    "identity list",
                    "step7 context",
                    "step7 render",
                )
            ),
            "write_commands": sorted(
                (
                    "guardian disposition",
                    "job cancel",
                    "job create",
                    "job recover",
                    "job transition",
                    "source copy",
                    "source associate",
                    "source observe",
                    "source reference",
                    "source relink",
                    "source select",
                    "identity correct",
                )
            ),
            "operational_record_kinds": sorted(
                (
                    "guardian-finding-disposition",
                    "guardian-report",
                    "pipeline-job-state",
                    "process-event",
                    "registry-identity-correction",
                    "source-asset-state",
                    "transaction-journal",
                )
            ),
            "features": {
                "real_pdf_parse": True,
                "stdin_json_handoff": True,
                "review_runtime": True,
                "step7_runtime": True,
                "on_demand_discovery": True,
                "approved_discovery_candidate_handoff": True,
                "explicit_oa_acquisition": True,
                "legal_oa_resolution": True,
                "manuscript_projection": True,
                "pipeline_jobs": True,
                "source_asset_runtime": True,
                "registry_identity_correction": True,
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
