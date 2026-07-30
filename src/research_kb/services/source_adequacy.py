from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from research_kb.bundle import load_workspace_entries, records_of_kind, validate_workspace_entries
from research_kb.catalog.models import canonical_digest
from research_kb.contracts.validator import validate_record
from research_kb.errors import (
    GROUNDING_MISMATCH,
    INVALID_AUTHORITY,
    SCHEMA_VALIDATION_FAILED,
    UNRESOLVED_REFERENCE,
    Diagnostic,
    ResearchKBError,
)
from research_kb.identifiers import Namespace, allocate_id, validate_id
from research_kb.process_events import timestamp
from research_kb.services._pipeline_authority import require_job_authority
from research_kb.source_adequacy import (
    ASSESSMENT_RULE_VERSION,
    OPERATION_CAPABILITY,
    OPERATION_REGISTRY_VERSION,
    apply_user_decision,
    build_machine_assessment,
    collect_parse_snapshot_state,
    collect_source_snapshot_state,
    profile_freshness,
    required_capability,
)
from research_kb.source_resolution import observe_paper_source
from research_kb.storage.json_io import file_sha256, read_jsonl, serialize_jsonl
from research_kb.storage.transactions import TransactionManager, TransactionResult
from research_kb.workspace import WorkspaceLayout


IdAllocator = Callable[[Namespace], str]


@dataclass(frozen=True, slots=True)
class SourceAdequacyMutationResult:
    profile: dict[str, Any]
    transaction: TransactionResult | None


class SourceAdequacyService:
    def __init__(
        self,
        layout: WorkspaceLayout,
        *,
        transaction_manager: TransactionManager | None = None,
        id_allocator: IdAllocator = allocate_id,
    ):
        self.layout = layout
        self.transactions = transaction_manager or TransactionManager(layout)
        self.id_allocator = id_allocator

    def assess(
        self,
        *,
        paper_id: str,
        job_id: str,
        requested_operation: str,
        actor: str = "cli",
        basis_profile_id: str | None = None,
        user_decision: Mapping[str, Any] | None = None,
    ) -> SourceAdequacyMutationResult:
        paper_id = validate_id(paper_id, Namespace.PAPER)
        job_id = validate_id(job_id, Namespace.JOB)
        if requested_operation not in OPERATION_CAPABILITY:
            raise _request_error(None, "/requested_operation", "requested operation is not registered")
        if actor not in {"cli", "user"}:
            raise ResearchKBError(
                Diagnostic(
                    INVALID_AUTHORITY,
                    "source-adequacy-profile",
                    None,
                    "/assessed_by",
                    "P3 Source Adequacy rejects Agent-authored profile mutations",
                )
            )
        require_job_authority(self.layout, job_id, "assess_source_adequacy")
        entries = load_workspace_entries(self.layout)
        validate_workspace_entries(entries)
        paper = next(
            (item for item in records_of_kind(entries, "registry-paper") if item["paper_id"] == paper_id),
            None,
        )
        if paper is None:
            raise ResearchKBError(
                Diagnostic(UNRESOLVED_REFERENCE, "registry-paper", paper_id, "/paper_id", "paper is not registered")
            )
        source_before = observe_paper_source(self.layout, entries, paper)
        if source_before.state != "current":
            raise ResearchKBError(
                Diagnostic(
                    GROUNDING_MISMATCH,
                    "source-adequacy-profile",
                    paper_id,
                    "/source_snapshots",
                    "Source Adequacy requires a current main source manifestation",
                )
            )
        pages = [
            item
            for item in records_of_kind(entries, "parsed-page")
            if item["paper_id"] == paper_id
        ]
        source_state = collect_source_snapshot_state(self.layout, entries, paper)
        parse_state = collect_parse_snapshot_state(self.layout, pages)
        observations, capabilities, limitations, actions = build_machine_assessment(
            source_state,
            parse_state,
        )
        basis_profile = self._basis_profile(
            entries,
            basis_profile_id=basis_profile_id,
            paper_id=paper_id,
            requested_operation=requested_operation,
            source_snapshots=source_state.snapshots,
            parse_snapshot=parse_state.snapshot,
        )
        normalized_decision = self._normalize_user_decision(
            actor=actor,
            decision=user_decision,
            basis_profile=basis_profile,
        )
        if normalized_decision is not None:
            capabilities = apply_user_decision(capabilities, observations, normalized_decision)
            if normalized_decision["decision"] == "remediation_required":
                actions = sorted({*actions, "follow_user_remediation"})

        profiles = records_of_kind(entries, "source-adequacy-profile")
        intent = {
            "job_id": job_id,
            "paper_id": paper_id,
            "requested_operation": requested_operation,
            "basis_profile": None if basis_profile is None else {
                "profile_id": basis_profile["profile_id"],
                "profile_digest": canonical_digest(basis_profile),
            },
            "source_snapshots": list(source_state.snapshots),
            "parse_snapshot": parse_state.snapshot,
            "user_decision": _decision_intent(normalized_decision),
        }
        existing = next((item for item in profiles if _profile_intent(item) == intent), None)
        if existing is not None:
            return SourceAdequacyMutationResult(existing, None)

        profile_id = self.id_allocator(Namespace.SOURCE_ADEQUACY)
        validate_id(profile_id, Namespace.SOURCE_ADEQUACY)
        if profile_id in {item["profile_id"] for item in profiles}:
            raise ResearchKBError(
                Diagnostic(
                    SCHEMA_VALIDATION_FAILED,
                    "source-adequacy-profile",
                    profile_id,
                    "/profile_id",
                    "allocated Source Adequacy profile ID is already in use",
                )
            )
        now = timestamp(self.transactions.clock)
        profile = {
            "schema_version": "1.0",
            "profile_id": profile_id,
            "basis_profile": intent["basis_profile"],
            "workspace_id": self.layout.workspace_id,
            "paper_id": paper_id,
            "job_id": job_id,
            "requested_operation": requested_operation,
            "operation_registry_version": OPERATION_REGISTRY_VERSION,
            "source_snapshots": list(source_state.snapshots),
            "parse_snapshot": parse_state.snapshot,
            "assessment_rule_version": ASSESSMENT_RULE_VERSION,
            "assessed_by": "user" if normalized_decision is not None else "cli",
            "assessed_at": now,
            "machine_observations": observations,
            "agent_assessment": None,
            "user_decision": normalized_decision,
            "capabilities": capabilities,
            "known_limitations": limitations,
            "recommended_actions": actions,
        }
        if "fixture_origin" in paper:
            profile["fixture_origin"] = paper["fixture_origin"]
        diagnostics = validate_record("source-adequacy-profile", profile, actor="stored")
        if diagnostics:
            raise ResearchKBError(diagnostics[0])
        proposed = [*profiles, profile]
        target = self.layout.source_adequacy_path
        target_before = file_sha256(target)

        def validate_stability() -> None:
            current_entries = load_workspace_entries(self.layout)
            current_paper = next(
                item for item in records_of_kind(current_entries, "registry-paper") if item["paper_id"] == paper_id
            )
            if observe_paper_source(self.layout, current_entries, current_paper) != source_before:
                raise ResearchKBError(
                    Diagnostic(
                        GROUNDING_MISMATCH,
                        "source-adequacy-profile",
                        profile_id,
                        "/source_snapshots",
                        "source manifestation changed during assessment",
                    )
                )
            current_pages = [
                item for item in records_of_kind(current_entries, "parsed-page") if item["paper_id"] == paper_id
            ]
            if collect_parse_snapshot_state(self.layout, current_pages).snapshot != parse_state.snapshot:
                raise ResearchKBError(
                    Diagnostic(
                        GROUNDING_MISMATCH,
                        "source-adequacy-profile",
                        profile_id,
                        "/parse_snapshot",
                        "active parse changed during assessment",
                    )
                )

        def validate_temp(path: Path) -> None:
            validate_stability()
            temporary = read_jsonl(
                path,
                record_kind="source-adequacy-profile",
                missing_ok=False,
                id_field="profile_id",
            )
            candidate_entries = load_workspace_entries(
                self.layout,
                overrides={target: [("source-adequacy-profile", item) for item in temporary]},
            )
            validate_workspace_entries(candidate_entries)

        transaction = self.transactions.promote_bytes(
            target=target,
            content=serialize_jsonl(proposed),
            target_store="source_adequacy",
            operation="source_adequacy_assess",
            actor=actor,
            input_refs=[paper_id, job_id, *([] if basis_profile is None else [basis_profile["profile_id"]])],
            output_refs=[profile_id],
            validator=validate_temp,
            post_replace_validator=validate_stability,
            expected_before_sha256=target_before,
            job_id=job_id,
        )
        return SourceAdequacyMutationResult(profile, transaction)

    def show(self, *, paper_id: str, requested_operation: str | None = None) -> dict[str, Any]:
        paper_id = validate_id(paper_id, Namespace.PAPER)
        if requested_operation is not None and requested_operation not in OPERATION_CAPABILITY:
            raise _request_error(None, "/requested_operation", "requested operation is not registered")
        entries = load_workspace_entries(self.layout)
        validate_workspace_entries(entries)
        profiles = [
            item
            for item in records_of_kind(entries, "source-adequacy-profile")
            if item["paper_id"] == paper_id
            and (requested_operation is None or item["requested_operation"] == requested_operation)
        ]
        items = [self._projection(entries, item) for item in sorted(profiles, key=_profile_sort_key)]
        return {
            "status": "success",
            "interface_version": "1.0",
            "paper_id": paper_id,
            "requested_operation": requested_operation,
            "count": len(items),
            "items": items,
        }

    def gate(self, *, paper_id: str, requested_operation: str) -> dict[str, Any]:
        paper_id = validate_id(paper_id, Namespace.PAPER)
        capability = required_capability(requested_operation)
        entries = load_workspace_entries(self.layout)
        validate_workspace_entries(entries)
        profiles = sorted(
            (
                item
                for item in records_of_kind(entries, "source-adequacy-profile")
                if item["paper_id"] == paper_id and item["requested_operation"] == requested_operation
            ),
            key=_profile_sort_key,
            reverse=True,
        )
        selected = next(
            (item for item in profiles if profile_freshness(self.layout, entries, item)["state"] == "current"),
            None,
        )
        if selected is None:
            stale = profiles[0] if profiles else None
            return {
                "status": "blocked",
                "paper_id": paper_id,
                "requested_operation": requested_operation,
                "required_capability": capability,
                "profile_id": None if stale is None else stale["profile_id"],
                "freshness": "absent" if stale is None else "stale_upstream",
                "capability_status": None,
                **_wait_route(
                    requested_operation,
                    None,
                    stale=stale is not None,
                    profile=stale,
                ),
            }
        decision = selected["capabilities"][capability]
        allowed = decision["status"] == "yes"
        return {
            "status": "allowed" if allowed else "blocked",
            "paper_id": paper_id,
            "requested_operation": requested_operation,
            "required_capability": capability,
            "profile_id": selected["profile_id"],
            "freshness": "current",
            "capability_status": decision["status"],
            **(
                {"pipeline_status": None, "wait_reason": None}
                if allowed
                else _wait_route(
                    requested_operation,
                    decision["status"],
                    stale=False,
                    profile=selected,
                )
            ),
        }

    def reusable_profile(
        self,
        *,
        paper_id: str,
        requested_operation: str,
    ) -> dict[str, Any] | None:
        result = self.gate(paper_id=paper_id, requested_operation=requested_operation)
        if result["freshness"] != "current" or result["profile_id"] is None:
            return None
        return next(
            item
            for item in read_jsonl(
                self.layout.source_adequacy_path,
                record_kind="source-adequacy-profile",
                id_field="profile_id",
            )
            if item["profile_id"] == result["profile_id"]
        )

    def _projection(self, entries: list[tuple[str, dict[str, Any]]], profile: dict[str, Any]) -> dict[str, Any]:
        return {
            "profile_id": profile["profile_id"],
            "basis_profile_id": None if profile["basis_profile"] is None else profile["basis_profile"]["profile_id"],
            "job_id": profile["job_id"],
            "requested_operation": profile["requested_operation"],
            "freshness": profile_freshness(self.layout, entries, profile),
            "source_roles": sorted({item["role"] for item in profile["source_snapshots"]}),
            "parse_ref": profile["parse_snapshot"]["active_parse_ref"],
            "parser": {
                "adapter": profile["parse_snapshot"]["parser_identity"]["adapter_id"],
                "version": profile["parse_snapshot"]["parser_identity"]["version"],
            },
            "capabilities": profile["capabilities"],
            "known_limitations": profile["known_limitations"],
            "recommended_actions": profile["recommended_actions"],
            "assessed_by": profile["assessed_by"],
            "assessed_at": profile["assessed_at"],
        }

    def _basis_profile(
        self,
        entries: list[tuple[str, dict[str, Any]]],
        *,
        basis_profile_id: str | None,
        paper_id: str,
        requested_operation: str,
        source_snapshots: tuple[dict[str, Any], ...],
        parse_snapshot: dict[str, Any],
    ) -> dict[str, Any] | None:
        if basis_profile_id is None:
            return None
        basis_profile_id = validate_id(basis_profile_id, Namespace.SOURCE_ADEQUACY)
        profile = next(
            (item for item in records_of_kind(entries, "source-adequacy-profile") if item["profile_id"] == basis_profile_id),
            None,
        )
        if profile is None:
            raise ResearchKBError(
                Diagnostic(UNRESOLVED_REFERENCE, "source-adequacy-profile", basis_profile_id, "/basis_profile", "basis profile does not exist")
            )
        if (
            profile["paper_id"] != paper_id
            or profile["requested_operation"] != requested_operation
            or profile["source_snapshots"] != list(source_snapshots)
            or profile["parse_snapshot"] != parse_snapshot
            or profile_freshness(self.layout, entries, profile)["state"] != "current"
        ):
            raise ResearchKBError(
                Diagnostic(
                    GROUNDING_MISMATCH,
                    "source-adequacy-profile",
                    basis_profile_id,
                    "/basis_profile",
                    "basis profile does not match the current assessment snapshot",
                )
            )
        return profile

    def _normalize_user_decision(
        self,
        *,
        actor: str,
        decision: Mapping[str, Any] | None,
        basis_profile: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if decision is None:
            if basis_profile is not None:
                raise _request_error(basis_profile["profile_id"], "/user_decision", "basis profile requires a user decision")
            return None
        if actor != "user":
            raise ResearchKBError(
                Diagnostic(INVALID_AUTHORITY, "source-adequacy-profile", None, "/user_decision", "user decision requires actor user")
            )
        if basis_profile is None:
            raise _request_error(None, "/basis_profile", "user decision requires a basis profile")
        if set(decision) - {"decision", "capabilities", "reason"}:
            raise _request_error(None, "/user_decision", "user decision contains unsupported fields")
        decision_name = decision.get("decision")
        capabilities = decision.get("capabilities")
        reason = decision.get("reason")
        if decision_name not in {"accept_uncertainty", "remediation_required"}:
            raise _request_error(None, "/user_decision/decision", "user decision is invalid")
        if not isinstance(capabilities, list) or not capabilities or not all(item in OPERATION_CAPABILITY.values() for item in capabilities):
            raise _request_error(None, "/user_decision/capabilities", "user decision capabilities are invalid")
        if len(capabilities) != len(set(capabilities)):
            raise _request_error(None, "/user_decision/capabilities", "user decision capabilities must be unique")
        if not isinstance(reason, str) or not reason:
            raise _request_error(None, "/user_decision/reason", "user decision reason is required")
        return {
            "actor": "user",
            "decision": decision_name,
            "capabilities": sorted(capabilities),
            "reason": reason,
            "decided_at": timestamp(self.transactions.clock),
        }


def _profile_intent(profile: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "job_id": profile["job_id"],
        "paper_id": profile["paper_id"],
        "requested_operation": profile["requested_operation"],
        "basis_profile": profile["basis_profile"],
        "source_snapshots": profile["source_snapshots"],
        "parse_snapshot": profile["parse_snapshot"],
        "user_decision": _decision_intent(profile["user_decision"]),
    }


def _decision_intent(decision: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if decision is None:
        return None
    return {key: decision[key] for key in ("actor", "decision", "capabilities", "reason")}


def _profile_sort_key(profile: Mapping[str, Any]) -> tuple[str, str]:
    return str(profile["assessed_at"]), str(profile["profile_id"])


def _wait_route(
    requested_operation: str,
    status: str | None,
    *,
    stale: bool,
    profile: Mapping[str, Any] | None,
) -> dict[str, str]:
    if stale:
        return {"pipeline_status": "waiting_source", "wait_reason": "source_adequacy_stale"}
    if requested_operation == "supplementary_analysis":
        return {"pipeline_status": "waiting_source", "wait_reason": "supplement_missing"}
    observations = {
        item["code"]: item["status"]
        for item in ([] if profile is None else profile.get("machine_observations", []))
    }
    if observations.get("text_presence") == "fail":
        return {"pipeline_status": "waiting_user", "wait_reason": "ocr_required"}
    if observations.get("page_coverage") in {"fail", "uncertain"}:
        return {"pipeline_status": "waiting_source", "wait_reason": "source_incomplete"}
    if observations.get("locator_reproducibility") == "fail":
        return {"pipeline_status": "waiting_user", "wait_reason": "reparse_required"}
    if requested_operation in {"figure_table_evidence", "formula_layout_analysis"}:
        return {"pipeline_status": "waiting_user", "wait_reason": "layout_parse_required"}
    if observations.get("parser_identity_matches") == "uncertain":
        return {"pipeline_status": "waiting_user", "wait_reason": "reparse_required"}
    if status == "uncertain":
        return {"pipeline_status": "waiting_user", "wait_reason": "source_adequacy_uncertain"}
    return {"pipeline_status": "waiting_source", "wait_reason": "source_adequacy_inadequate"}


def _request_error(record_id: str | None, path: str, message: str) -> ResearchKBError:
    return ResearchKBError(
        Diagnostic(SCHEMA_VALIDATION_FAILED, "source-adequacy-profile", record_id, path, message)
    )


__all__ = ["SourceAdequacyMutationResult", "SourceAdequacyService"]
