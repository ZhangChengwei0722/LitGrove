from __future__ import annotations

from collections.abc import Callable

from research_kb import __version__
from research_kb.agent_task_registry import PRIVACY_REGISTRY_VERSION
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
                    "adequacy gate",
                    "adequacy show",
                    "question list",
                    "question show",
                    "question render",
                    "obsidian status",
                    "obsidian render --dry-run",
                    "exchange export-preview",
                    "exchange import-preview",
                    "exchange list-imports",
                    "backup inspect",
                    "backup preview",
                    "maintenance archive-preview",
                    "maintenance list",
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
                    "adequacy assess",
                    "trunk advance",
                    "obsidian render",
                    "exchange export",
                    "exchange import",
                    "exchange recover",
                    "backup create",
                    "backup restore",
                    "maintenance archive",
                    "maintenance enqueue",
                )
            ),
            "operational_record_kinds": sorted(
                (
                    "guardian-finding-disposition",
                    "guardian-report",
                    "exchange-local-export-receipt",
                    "exchange-import-journal",
                    "exchange-import-receipt",
                    "pipeline-job-state",
                    "process-event",
                    "registry-identity-correction",
                    "source-asset-state",
                    "source-adequacy-profile",
                    "trusted-parse-authority",
                    "agent-task-state",
                    "transaction-journal",
                    "backup-local-receipt",
                    "restore-receipt",
                    "operational-archive-manifest",
                    "operational-archive-receipt",
                    "maintenance-work",
                )
            ),
            "features": {
                "discovery_application_service": True,
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
                "source_adequacy": True,
                "source_adequacy_resolution": True,
                "workspace_materialization": True,
                "workspace_adoption": True,
                "trusted_parse_authority": True,
                "supervised_pdf_parse": True,
                "trusted_parse_intake_application": True,
                "deterministic_trunk": True,
                "deterministic_intake_application": True,
                "agent_task_staging": True,
                "knowledge_query_agent_tasks": True,
                "reading_application": True,
                "reading_evidence_source_access": True,
                "research_organization_application": True,
                "organization_proposal_agent_tasks": True,
                "question_screening_agent_tasks": True,
                "research_synthesis_application": True,
                "research_synthesis_agent_tasks": True,
                "tag_application": True,
                "obsidian_generated_views": True,
                "exchange_source_free_export": True,
                "exchange_source_inclusive_export": True,
                "exchange_import": True,
                "backup_restore": True,
                "operational_maintenance": True,
                "lazy_stale_maintenance": True,
                "embedded_agent_runtime": False,
            },
            "agent_task_registry_version": PRIVACY_REGISTRY_VERSION,
        }

    def _pdfplumber_version(self) -> str | None:
        try:
            return self.pdfplumber_probe()
        except ResearchKBError as error:
            if error.diagnostic.code == PARSE_ADAPTER_UNAVAILABLE:
                return None
            raise


__all__ = ["CapabilityService"]
