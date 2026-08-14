from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from research_kb.application import APPLICATION_SERVICE_INTERFACE_VERSION
from research_kb.trusted_parse_authority import TrustedParseAuthorityPreview


ADAPTER_NAME = "pdfplumber-text-flow"
ALLOWED_OPERATION = "parse_run"
ROUTE_SUFFIXES = frozenset({"primary", "review", "review_mixed", "undecided"})
AUTHORITY_PREFIX = "trusted_parse_authority_"
EXECUTION_PREFIX = "trusted_parse_execution_"
RECONCILE_PREFIX = "trusted_parse_reconcile_"
TRUSTED_PREFIXES = (AUTHORITY_PREFIX, EXECUTION_PREFIX, RECONCILE_PREFIX)
LIMITED_TRUST_WARNING = "local_pdf_parser_isolation_is_not_a_hostile_document_sandbox"


@dataclass(frozen=True, slots=True)
class TrustedParseIntakePreparation:
    session_option_id: str
    workspace_id: str
    job_id: str
    job_state_id: str
    job_state_digest: str
    route_suffix: str
    paper_id: str
    source_ref: dict[str, str]
    source_sha256: str
    source_name: str
    source_size_bytes: int
    parser: dict[str, str]
    parser_profile_id: str
    policy_version: str
    allowed_operation: str
    expires_at: str
    authority_preview: TrustedParseAuthorityPreview
    authority_committed: bool
    correlated_authority_event_id: str | None
    parsed_page_state: str
    correlated_parse_event_id: str | None
    preparation_digest: str

    def public_projection(self) -> dict[str, Any]:
        return {
            "status": "success",
            "interface_version": APPLICATION_SERVICE_INTERFACE_VERSION,
            "paper_id": self.paper_id,
            "source": {
                "display_name": self.source_name,
                "size_bytes": self.source_size_bytes,
                "identity_status": "current",
            },
            "parser": dict(self.parser),
            "parser_profile_id": self.parser_profile_id,
            "policy_version": self.policy_version,
            "allowed_operation": self.allowed_operation,
            "expires_at": self.expires_at,
            "limited_trust_warning": LIMITED_TRUST_WARNING,
            "supervised_reparse_required": self.parsed_page_state == "supervised_reparse_required",
            "aggregate_preview_digest": self.preparation_digest,
            "persistent_writes": 0,
        }


@dataclass(frozen=True, slots=True)
class TrustedParseIntakeResult:
    outcome: str
    parse_run_id: str | None
    result: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.result,
            "trusted_parse_outcome": self.outcome,
            "parse_run_id": self.parse_run_id,
        }


__all__ = [
    "TrustedParseIntakePreparation",
    "TrustedParseIntakeResult",
]
