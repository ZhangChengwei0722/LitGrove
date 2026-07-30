from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from research_kb.acquisition_paths import acquisition_destination, local_inbox_destination
from research_kb.bundle import BundleEntry, load_workspace_entries, records_of_kind
from research_kb.contracts.validator import validate_bundle, validate_record
from research_kb.errors import (
    GROUNDING_MISMATCH,
    INCOMPLETE_TRANSACTION,
    INPUT_TOO_LARGE,
    INVALID_AUTHORITY,
    PATH_ESCAPE,
    SCHEMA_VALIDATION_FAILED,
    SNAPSHOT_MISMATCH,
    Diagnostic,
    ResearchKBError,
)
from research_kb.identifiers import Namespace, allocate_id
from research_kb.pipeline_jobs import current_pipeline_states, pipeline_job_chain_diagnostics
from research_kb.process_events import timestamp
from research_kb.review_memory_provenance import build_active_parse_index, review_memory_freshness
from research_kb.services.question_mapping import mapping_freshness_diagnostics
from research_kb.source_assets import (
    current_source_asset_heads,
    source_asset_chain_diagnostics,
    source_asset_projection,
)
from research_kb.source_resolution import inspect_source_ref, observe_paper_source
from research_kb.source_adequacy import profile_freshness
from research_kb.step7_support import STEP7_RECORD_KINDS, candidate_freshness
from research_kb.storage.json_io import file_sha256, read_json_document, read_jsonl, serialize_jsonl
from research_kb.storage.transactions import TransactionManager, TransactionResult, build_journal_event
from research_kb.workspace import WorkspaceLayout


MAX_GUARDIAN_INBOX_ENTRIES = 1_000


@dataclass(frozen=True, slots=True)
class GuardianResult:
    report: dict[str, Any]
    transaction: TransactionResult | None = None


class GuardianService:
    def __init__(
        self,
        layout: WorkspaceLayout,
        *,
        transaction_manager: TransactionManager | None = None,
    ):
        self.layout = layout
        self.transactions = transaction_manager or TransactionManager(layout)

    def check(self, *, write_report: bool = False) -> GuardianResult:
        diagnostics: list[Diagnostic] = []
        entries: list[BundleEntry] = []
        try:
            entries = load_workspace_entries(self.layout)
        except ResearchKBError as error:
            diagnostics.append(error.diagnostic)
        else:
            diagnostics.extend(
                validate_bundle(
                    {"records": [{"kind": kind, "record": record} for kind, record in entries]},
                    actor="stored",
                )
            )
            diagnostics.extend(self._source_diagnostics(entries))
            diagnostics.extend(self._acquisition_diagnostics(entries))
            diagnostics.extend(self._local_source_intake_diagnostics(entries))
            for kind, mapping in entries:
                if kind == "question-mapping" and not validate_record(
                    "question-mapping", mapping, actor="stored"
                ):
                    diagnostics.extend(mapping_freshness_diagnostics(mapping, entries))
                elif kind == "review-memory" and not validate_record(
                    "review-memory", mapping, actor="stored"
                ):
                    diagnostics.extend(review_memory_freshness_diagnostics(mapping, entries))
                elif kind in STEP7_RECORD_KINDS and not validate_record(
                    kind, mapping, actor="stored"
                ):
                    diagnostics.extend(step7_freshness_diagnostics(kind, mapping, entries))
        diagnostics.extend(self._canonical_path_diagnostics())
        process_events = [record for kind, record in entries if kind == "process-event"]
        diagnostics.extend(self._adequacy_diagnostics(entries, process_events))
        diagnostics.extend(self._transaction_diagnostics(process_events))
        diagnostics.extend(self._job_event_diagnostics(entries, process_events))
        diagnostics.extend(self._source_event_diagnostics(entries, process_events))
        diagnostics = _deduplicate(diagnostics)
        defined_ids = _defined_ids(entries)
        findings = [_finding_from_diagnostic(item, defined_ids) for item in diagnostics]
        report = {
            "schema_version": "1.0",
            "guardian_report_id": allocate_id(Namespace.GUARDIAN_REPORT),
            "workspace_id": self.layout.workspace_id,
            "status": status_for_findings(findings),
            "findings": findings,
            "created_at": timestamp(self.transactions.clock),
        }
        report_diagnostics = validate_record("guardian-report", report, actor="stored")
        if report_diagnostics:
            raise ResearchKBError(report_diagnostics[0])
        transaction = self._write_report(report) if write_report else None
        return GuardianResult(report=report, transaction=transaction)

    def _source_diagnostics(self, entries: list[BundleEntry]) -> list[Diagnostic]:
        diagnostics: list[Diagnostic] = []
        source_states = [record for kind, record in entries if kind == "source-asset-state"]
        if any(validate_record("source-asset-state", state, actor="stored") for state in source_states):
            return diagnostics
        if source_asset_chain_diagnostics(source_states):
            return diagnostics
        heads = current_source_asset_heads(source_states)
        projections = {
            item["source_asset_id"]: item for item in source_asset_projection(source_states)
        }
        explicit_main_papers: set[str] = set()
        for head in heads:
            if head["paper_id"] is None:
                diagnostics.append(
                    Diagnostic(
                        INCOMPLETE_TRANSACTION,
                        "source-asset-state",
                        head["source_asset_state_id"],
                        "/paper_id",
                        "source asset is not yet associated with a Registry paper",
                        severity="warning",
                    )
                )
            elif head["asset_role"] == "main_pdf":
                explicit_main_papers.add(head["paper_id"])
            observed = inspect_source_ref(
                self.layout,
                root_id=head["source_ref"]["root_id"],
                relative_path=head["source_ref"]["relative_path"],
            )
            projection = projections[head["source_asset_id"]]
            if (
                projection["source_currentness"] != "current"
                or observed.availability != "available"
                or observed.live_sha256 != head["source_fingerprint"]["value"]
            ):
                diagnostics.append(
                    Diagnostic(
                        GROUNDING_MISMATCH,
                        "source-asset-state",
                        head["source_asset_state_id"],
                        "/source_fingerprint",
                        "current Source Asset manifestation is stale, unavailable or no longer matches its receipt: "
                        f"projected={projection['source_currentness']}; observed={observed.availability}",
                    )
                )
        for kind, paper in entries:
            if kind != "registry-paper":
                continue
            paper_id = paper["paper_id"]
            if paper_id in explicit_main_papers:
                continue
            try:
                observation = observe_paper_source(self.layout, entries, paper)
            except ResearchKBError as error:
                diagnostics.append(error.diagnostic)
                continue
            if observation.state != "current":
                diagnostics.append(
                    Diagnostic(
                        GROUNDING_MISMATCH,
                        "source-asset-state" if observation.source_asset_state_id is not None else "registry-paper",
                        observation.source_asset_state_id or paper_id,
                        "/source_fingerprint",
                        f"current paper source is not reusable: {observation.state}",
                    )
                )
        return diagnostics

    def _source_event_diagnostics(
        self,
        entries: list[BundleEntry],
        process_events: list[dict[str, Any]],
    ) -> list[Diagnostic]:
        source_states = [record for kind, record in entries if kind == "source-asset-state"]
        corrections = [record for kind, record in entries if kind == "registry-identity-correction"]
        job_states = [record for kind, record in entries if kind == "pipeline-job-state"]
        if (
            any(validate_record("source-asset-state", state, actor="stored") for state in source_states)
            or any(validate_record("registry-identity-correction", item, actor="stored") for item in corrections)
            or any(validate_record("pipeline-job-state", state, actor="stored") for state in job_states)
        ):
            return []
        if source_asset_chain_diagnostics(source_states):
            return []
        try:
            jobs = {state["job_id"]: state for state in current_pipeline_states(job_states)}
        except ResearchKBError:
            return []
        valid_events = [
            event
            for event in process_events
            if not validate_record("process-event", event, actor="stored")
        ]
        reason_operations = {
            "reference_registered": "register_by_reference",
            "copied_into_local_inbox": "copy_into_local_inbox",
            "paper_associated": "associate_source_asset",
            "same_digest_relink": "same_digest_relink",
            "changed_bytes_observed": "observe_source",
            "source_available": "observe_source",
            "source_missing": "observe_source",
            "source_inaccessible": "observe_source",
            "source_relink_required": "observe_source",
        }
        event_operations = {
            "reference_registered": "source_asset_register_reference",
            "copied_into_local_inbox": "source_asset_copy_into_local_inbox",
            "paper_associated": "source_asset_associate",
            "same_digest_relink": "source_asset_relink",
            "changed_bytes_observed": "source_asset_observe",
            "source_available": "source_asset_observe",
            "source_missing": "source_asset_observe",
            "source_inaccessible": "source_asset_observe",
            "source_relink_required": "source_asset_observe",
        }
        diagnostics: list[Diagnostic] = []
        for state in source_states:
            reason = state["reason"]
            job = jobs.get(state["job_id"])
            if job is not None and reason_operations[reason] not in job["authority_snapshot"]["granted_operations"]:
                diagnostics.append(
                    Diagnostic(
                        INVALID_AUTHORITY,
                        "source-asset-state",
                        state["source_asset_state_id"],
                        "/job_id",
                        "Source Asset state is not covered by its Pipeline Job authority",
                    )
                )
            matching = [
                event
                for event in valid_events
                if event.get("result") == "success"
                and event.get("job_id") == state["job_id"]
                and event.get("operation") == event_operations[reason]
                and state["source_asset_state_id"] in event.get("output_refs", [])
                and state["source_asset_id"] in event.get("output_refs", [])
            ]
            if len(matching) != 1:
                diagnostics.append(
                    Diagnostic(
                        INCOMPLETE_TRANSACTION,
                        "source-asset-state",
                        state["source_asset_state_id"],
                        "/job_id",
                        f"Source Asset state must have exactly one correlated success event; found {len(matching)}",
                    )
                )
        for correction in corrections:
            job = jobs.get(correction["job_id"])
            if job is not None and "registry_identity_correction" not in job["authority_snapshot"]["granted_operations"]:
                diagnostics.append(
                    Diagnostic(
                        INVALID_AUTHORITY,
                        "registry-identity-correction",
                        correction["correction_id"],
                        "/job_id",
                        "Registry identity correction is not covered by its Pipeline Job authority",
                    )
                )
            matching = [
                event
                for event in valid_events
                if event.get("result") == "success"
                and event.get("job_id") == correction["job_id"]
                and event.get("operation") == "registry_identity_correction"
                and correction["correction_id"] in event.get("output_refs", [])
            ]
            if len(matching) != 1:
                diagnostics.append(
                    Diagnostic(
                        INCOMPLETE_TRANSACTION,
                        "registry-identity-correction",
                        correction["correction_id"],
                        "/job_id",
                        f"Registry identity correction must have exactly one correlated success event; found {len(matching)}",
                    )
                )
        return diagnostics

    def _local_source_intake_diagnostics(self, entries: list[BundleEntry]) -> list[Diagnostic]:
        diagnostics: list[Diagnostic] = []
        try:
            binding = local_inbox_destination(self.layout, "guardian-placeholder.pdf")
        except ResearchKBError:
            return diagnostics
        source_refs: set[tuple[str, str]] = set()
        for kind in ("registry-paper", "source-asset-state"):
            for record in records_of_kind(entries, kind):
                source_ref = record.get("source_ref")
                if not isinstance(source_ref, dict):
                    continue
                root_id = source_ref.get("root_id")
                relative_path = source_ref.get("relative_path")
                if isinstance(root_id, str) and isinstance(relative_path, str):
                    source_refs.add((root_id, relative_path))
        paths: list[Path] = []
        try:
            with os.scandir(binding.inbox) as iterator:
                for entry in iterator:
                    if len(paths) >= MAX_GUARDIAN_INBOX_ENTRIES:
                        diagnostics.append(
                            Diagnostic(
                                INPUT_TOO_LARGE,
                                "local-source-intake",
                                None,
                                "/local_inbox",
                                "Guardian local_inbox inspection reached its 1,000-entry bound",
                                severity="warning",
                            )
                        )
                        break
                    paths.append(Path(entry.path))
        except OSError:
            diagnostics.append(
                Diagnostic(
                    SCHEMA_VALIDATION_FAILED,
                    "local-source-intake",
                    None,
                    "/local_inbox",
                    "Guardian could not inspect local_inbox",
                )
            )
            return diagnostics
        for path in sorted(paths, key=lambda item: item.name.casefold()):
            if path.name.startswith(".research-kb-copy-job_") and path.name.endswith(".part.pdf"):
                diagnostics.append(
                    Diagnostic(
                        INCOMPLETE_TRANSACTION,
                        "local-source-intake",
                        None,
                        "/local_inbox",
                        "operation-pattern inbox copy partial remains in local_inbox",
                    )
                )
                continue
            if path.name.startswith("job_") and path.name.endswith(".pdf"):
                source_ref = (
                    binding.root_id,
                    path.relative_to(self.layout.source_roots[binding.root_id]).as_posix(),
                )
                if source_ref not in source_refs:
                    diagnostics.append(
                        Diagnostic(
                            INCOMPLETE_TRANSACTION,
                            "local-source-intake",
                            None,
                            "/local_inbox",
                            "job-named inbox source exists without a Source Asset receipt",
                        )
                    )
            try:
                metadata = os.lstat(path)
            except OSError:
                continue
            if path.is_symlink() or not stat.S_ISREG(metadata.st_mode) or getattr(metadata, "st_nlink", 1) != 1:
                diagnostics.append(
                    Diagnostic(
                        PATH_ESCAPE,
                        "local-source-intake",
                        None,
                        "/local_inbox",
                        "unsafe or ambiguous inbox entry cannot enter source intake",
                        severity="warning",
                    )
                )
        return diagnostics

    def _acquisition_diagnostics(self, entries: list[BundleEntry]) -> list[Diagnostic]:
        diagnostics: list[Diagnostic] = []
        candidates = [record for kind, record in entries if kind == "discovery-candidate"]
        valid_inbox = False
        signature = bytes((37, 80, 68, 70, 45))
        for candidate in candidates:
            candidate_id = candidate["candidate_id"]
            try:
                destination = acquisition_destination(self.layout, candidate_id)
            except ResearchKBError:
                if candidate.get("acquisition_status") == "acquired":
                    diagnostics.append(
                        Diagnostic(
                            GROUNDING_MISMATCH,
                            "discovery-candidate",
                            candidate_id,
                            "/acquisition_receipt/source_ref",
                            "acquired source is not addressable through the exact local_inbox contract",
                        )
                    )
                continue
            valid_inbox = True
            if candidate.get("acquisition_status") == "not_started":
                if os.path.lexists(destination.final_path):
                    diagnostics.append(
                        Diagnostic(
                            INCOMPLETE_TRANSACTION,
                            "discovery-candidate",
                            candidate_id,
                            "/acquisition_status",
                            "candidate-named acquisition source exists without an acquired receipt",
                        )
                    )
                continue

            receipt = candidate.get("acquisition_receipt")
            mismatch = not isinstance(receipt, dict) or receipt.get(
                "source_ref"
            ) != destination.source_ref.to_dict()
            if not mismatch:
                try:
                    current = os.lstat(destination.final_path)
                    mismatch = (
                        not stat.S_ISREG(current.st_mode)
                        or destination.final_path.is_symlink()
                        or current.st_size != receipt["content_size_bytes"]
                        or file_sha256(destination.final_path)
                        != receipt["source_fingerprint"]["value"]
                    )
                    if not mismatch:
                        with destination.final_path.open("rb") as stream:
                            mismatch = stream.read(len(signature)) != signature
                except (OSError, KeyError, TypeError):
                    mismatch = True
            if mismatch:
                diagnostics.append(
                    Diagnostic(
                        GROUNDING_MISMATCH,
                        "discovery-candidate",
                        candidate_id,
                        "/acquisition_receipt/source_fingerprint",
                        "acquired source is missing, changed or outside its exact receipt target",
                    )
                )

        if valid_inbox:
            for _ in sorted(
                self.layout.local_inbox.glob(
                    ".research-kb-acquire-event_*.part.pdf"
                ),
                key=lambda item: item.name,
            ):
                diagnostics.append(
                    Diagnostic(
                        INCOMPLETE_TRANSACTION,
                        "discovery-acquisition",
                        None,
                        "/local_inbox",
                        "operation-pattern acquisition partial remains in local_inbox",
                    )
                )
        return diagnostics

    def _canonical_path_diagnostics(self) -> list[Diagnostic]:
        diagnostics: list[Diagnostic] = []
        paths = [
            self.layout.registry_path,
            self.layout.source_assets_path,
            self.layout.identity_corrections_path,
            self.layout.review_queue_path,
            self.layout.process_events_path,
            self.layout.pipeline_jobs_path,
            self.layout.guardian_reports_path,
            self.layout.guardian_finding_dispositions_path,
            self.layout.question_mappings_path,
            self.layout.discovery_candidates_path,
            *(self.layout.step7_store_path(kind) for kind in STEP7_RECORD_KINDS),
        ]
        for directory, pattern in (
            (self.layout.knowledge_root / "parse" / "by_paper", "*.pages.jsonl"),
            (self.layout.knowledge_root / "paper_cards" / "by_paper", "*.card.json"),
            (self.layout.knowledge_root / "evidence" / "by_paper", "*.evidence.jsonl"),
            (self.layout.knowledge_root / "review_memories" / "by_paper", "*.review.json"),
        ):
            if directory.exists():
                paths.extend(directory.glob(pattern))
        for path in paths:
            if not path.exists():
                continue
            try:
                self.layout.ensure_writable_target(path)
            except ResearchKBError:
                diagnostics.append(
                    Diagnostic(PATH_ESCAPE, "workspace", self.layout.workspace_id, str(path), "canonical target resolves outside knowledge_root")
                )
        return diagnostics

    def _job_event_diagnostics(
        self,
        entries: list[BundleEntry],
        process_events: list[dict[str, Any]],
    ) -> list[Diagnostic]:
        diagnostics: list[Diagnostic] = []
        states = [record for kind, record in entries if kind == "pipeline-job-state"]
        state_ids = {state["state_id"] for state in states}
        for state in states:
            matching = [
                event
                for event in process_events
                if event.get("job_id") == state["job_id"]
                and state["state_id"] in event.get("output_refs", [])
                and event.get("operation") in {"pipeline_job_create", "pipeline_job_transition"}
                and event.get("result") == "success"
            ]
            if len(matching) != 1:
                diagnostics.append(
                    Diagnostic(
                        INCOMPLETE_TRANSACTION,
                        "pipeline-job-state",
                        state["state_id"],
                        "/state_id",
                        f"Pipeline Job state must have exactly one correlated success event; found {len(matching)}",
                    )
                )
        for event in process_events:
            if event.get("job_id") is None:
                continue
            correlated_states = set(event.get("output_refs", [])) & state_ids
            if (
                event.get("operation") in {"pipeline_job_create", "pipeline_job_transition"}
                and event.get("result") == "success"
                and len(correlated_states) != 1
            ):
                diagnostics.append(
                    Diagnostic(
                        INCOMPLETE_TRANSACTION,
                        "process-event",
                        event["event_id"],
                        "/output_refs",
                        "correlated Pipeline Job event must reference exactly one Job state",
                    )
                )
        return diagnostics

    def _adequacy_diagnostics(
        self,
        entries: list[BundleEntry],
        process_events: list[dict[str, Any]],
    ) -> list[Diagnostic]:
        diagnostics: list[Diagnostic] = []
        profiles = [
            record
            for kind, record in entries
            if kind == "source-adequacy-profile"
            and not validate_record("source-adequacy-profile", record, actor="stored")
        ]
        if not profiles:
            return diagnostics
        job_states = [record for kind, record in entries if kind == "pipeline-job-state"]
        if (
            any(validate_record("pipeline-job-state", state, actor="stored") for state in job_states)
            or pipeline_job_chain_diagnostics(job_states)
        ):
            return diagnostics
        try:
            job_heads = {item["job_id"]: item for item in current_pipeline_states(job_states)}
        except ResearchKBError:
            return diagnostics
        latest: dict[tuple[str, str], dict[str, Any]] = {}
        for profile in profiles:
            key = (profile["paper_id"], profile["requested_operation"])
            existing = latest.get(key)
            if existing is None or (profile["assessed_at"], profile["profile_id"]) > (
                existing["assessed_at"],
                existing["profile_id"],
            ):
                latest[key] = profile
            matching_events = [
                event
                for event in process_events
                if event.get("operation") == "source_adequacy_assess"
                and event.get("result") == "success"
                and event.get("job_id") == profile["job_id"]
                and profile["profile_id"] in event.get("output_refs", [])
            ]
            if len(matching_events) != 1:
                diagnostics.append(
                    Diagnostic(
                        INCOMPLETE_TRANSACTION,
                        "source-adequacy-profile",
                        profile["profile_id"],
                        "/job_id",
                        f"Source Adequacy profile must have exactly one correlated success event; found {len(matching_events)}",
                    )
                )
            job = job_heads.get(profile["job_id"])
            if job is not None and "assess_source_adequacy" not in job["authority_snapshot"]["granted_operations"]:
                diagnostics.append(
                    Diagnostic(
                        INVALID_AUTHORITY,
                        "source-adequacy-profile",
                        profile["profile_id"],
                        "/job_id",
                        "owning Pipeline Job does not grant Source Adequacy assessment",
                    )
                )
            if job is not None and profile["profile_id"] not in job["output_refs"]:
                diagnostics.append(
                    Diagnostic(
                        INCOMPLETE_TRANSACTION,
                        "source-adequacy-profile",
                        profile["profile_id"],
                        "/profile_id",
                        "Source Adequacy profile is committed but not yet consumed by its Pipeline Job",
                        severity="warning",
                    )
                )
        for profile in latest.values():
            freshness = profile_freshness(self.layout, entries, profile)
            if freshness["state"] == "stale_upstream":
                diagnostics.append(
                    Diagnostic(
                        SNAPSHOT_MISMATCH,
                        "source-adequacy-profile",
                        profile["profile_id"],
                        "/profile_id",
                        "latest Source Adequacy profile is stale for its recorded source or parse snapshot",
                        severity="warning",
                    )
                )
        return diagnostics

    def _transaction_diagnostics(self, process_events: list[dict[str, Any]]) -> list[Diagnostic]:
        diagnostics: list[Diagnostic] = []
        if not self.layout.transactions_root.exists():
            return diagnostics
        events_by_id: dict[str, list[dict[str, Any]]] = {}
        for event in process_events:
            events_by_id.setdefault(event["event_id"], []).append(event)
        for path in sorted(self.layout.transactions_root.glob("*.json"), key=lambda item: item.name):
            try:
                journal = read_json_document(path, record_kind="transaction-journal")
                journal_diagnostics = validate_record("transaction-journal", journal, actor="stored")
            except ResearchKBError as error:
                diagnostics.append(error.diagnostic)
                continue
            diagnostics.extend(journal_diagnostics)
            if journal_diagnostics:
                continue
            if journal["phase"] != "complete":
                diagnostics.append(
                    Diagnostic(
                        INCOMPLETE_TRANSACTION,
                        "transaction-journal",
                        journal["event_id"],
                        "/phase",
                        f"transaction journal is not complete: {journal['phase']}",
                    )
                )
                continue
            matching_events = events_by_id.get(journal["event_id"], [])
            if len(matching_events) != 1:
                diagnostics.append(
                    Diagnostic(
                        INCOMPLETE_TRANSACTION,
                        "transaction-journal",
                        journal["event_id"],
                        "/event_id",
                        f"completed transaction must have exactly one process event; found {len(matching_events)}",
                    )
                )
                continue
            expected_event = build_journal_event(journal, journal["result"])
            if matching_events[0] != expected_event:
                diagnostics.append(
                    Diagnostic(
                        INCOMPLETE_TRANSACTION,
                        "transaction-journal",
                        journal["event_id"],
                        "/event_id",
                        "completed transaction process event does not match its journal",
                    )
                )
        return diagnostics

    def _write_report(self, report: dict[str, Any]) -> TransactionResult:
        target = self.layout.guardian_reports_path
        target_before = file_sha256(target)
        reports = read_jsonl(target, record_kind="guardian-report", id_field="guardian_report_id")
        proposed = [*reports, report]

        def validate_temp(path: Path) -> None:
            temporary = read_jsonl(
                path,
                record_kind="guardian-report",
                missing_ok=False,
                id_field="guardian_report_id",
            )
            for item in temporary:
                diagnostics = validate_record("guardian-report", item, actor="stored")
                if diagnostics:
                    raise ResearchKBError(diagnostics[0])

        return self.transactions.promote_bytes(
            target=target,
            content=serialize_jsonl(proposed),
            target_store="guardian_reports",
            operation="guardian_check",
            actor="cli",
            input_refs=[self.layout.workspace_id],
            output_refs=[report["guardian_report_id"]],
            validator=validate_temp,
            expected_before_sha256=target_before,
        )


def status_for_findings(findings: list[dict[str, Any]]) -> str:
    severities = {finding["severity"] for finding in findings}
    if "error" in severities:
        return "failure"
    if "warning" in severities:
        return "warning"
    return "success"


def review_memory_freshness_diagnostics(
    memory: dict[str, Any],
    entries: list[BundleEntry],
) -> list[Diagnostic]:
    active, failures = build_active_parse_index(
        record for kind, record in entries if kind == "parsed-page"
    )
    if failures or review_memory_freshness(memory, active) != "stale_parse":
        return []
    return [
        Diagnostic(
            SNAPSHOT_MISMATCH,
            "review-memory",
            memory["review_memory_id"],
            "/parse_snapshot",
            "Review Memory parse snapshot is stale relative to the active parse",
            severity="warning",
        )
    ]


def step7_freshness_diagnostics(
    kind: str,
    candidate: dict[str, Any],
    entries: list[BundleEntry],
) -> list[Diagnostic]:
    freshness = candidate_freshness(candidate, entries)
    if freshness["state"] == "current":
        return []
    return [
        Diagnostic(
            SNAPSHOT_MISMATCH,
            kind,
            candidate["candidate_id"],
            "/input_snapshot",
            "Step 7 candidate is stale relative to upstream records: "
            + ", ".join(freshness["reasons"]),
            severity="warning",
        )
    ]


def _finding_from_diagnostic(diagnostic: Diagnostic, defined_ids: set[str]) -> dict[str, Any]:
    remediation = {
        GROUNDING_MISMATCH: "Restore the registered source or correct parsed-page and Evidence provenance against the current source.",
        INCOMPLETE_TRANSACTION: "Run transaction recover and inspect ambiguous digests before any further mutation.",
        PATH_ESCAPE: "Move the canonical target under knowledge_root and correct the workspace path contract.",
        SNAPSHOT_MISMATCH: "Refresh the Question Mapping from its current Paper Card, evidence, and review queue inputs.",
    }.get(diagnostic.code, "Inspect the referenced structured record and correct the reported contract violation.")
    if diagnostic.code == SNAPSHOT_MISMATCH and diagnostic.record_kind == "review-memory":
        remediation = "Reread the current parse and explicitly refresh the AI-owned Review Memory; do not rebind old source notes."
    elif diagnostic.code == SNAPSHOT_MISMATCH and diagnostic.record_kind in STEP7_RECORD_KINDS:
        remediation = "Refresh the candidate from the current Question Mapping and selected grounded Card Units; do not rewrite it automatically."
    elif diagnostic.code == GROUNDING_MISMATCH and diagnostic.record_kind == "discovery-candidate":
        remediation = "Restore the exact acquired source or inspect its receipt; do not overwrite or silently rebind it."
    elif diagnostic.code == INCOMPLETE_TRANSACTION and diagnostic.record_kind in {
        "discovery-candidate",
        "discovery-acquisition",
    }:
        remediation = "Inspect the acquisition journal, receipt and operation-owned files; do not delete or adopt source files automatically."
    elif diagnostic.code == INCOMPLETE_TRANSACTION and diagnostic.record_kind in {
        "pipeline-job-state",
        "guardian-finding-disposition",
    }:
        remediation = "Inspect the append-only operational chain and transaction journal; recover by digest or append an explicit disposition without rewriting history."
    elif diagnostic.record_kind == "source-adequacy-profile":
        remediation = "Re-run the exact requested-use assessment or resume its owning Pipeline Job; do not edit or delete the historical profile."
    return {
        "code": diagnostic.code,
        "severity": diagnostic.severity,
        "record_ref": diagnostic.record_id if diagnostic.record_id in defined_ids else None,
        "message": diagnostic.message,
        "remediation": remediation,
    }


def _defined_ids(entries: list[BundleEntry]) -> set[str]:
    result: set[str] = set()
    fields = {
        "registry-paper": "paper_id",
        "review-memory": "review_memory_id",
        "evidence": "evidence_id",
        "review-queue": "queue_id",
        "process-event": "event_id",
        "guardian-report": "guardian_report_id",
        "pipeline-job-state": "state_id",
        "guardian-finding-disposition": "disposition_id",
        "source-asset-state": "source_asset_state_id",
        "registry-identity-correction": "correction_id",
        "source-adequacy-profile": "profile_id",
        "question-mapping": "question_id",
        "discovery-candidate": "candidate_id",
        "step7-synthesis": "candidate_id",
        "step7-review-angle": "candidate_id",
        "step7-insight": "candidate_id",
        "step7-cross-view": "candidate_id",
    }
    for kind, record in entries:
        if kind == "workspace":
            result.add(record["workspace"]["id"])
        elif kind == "paper-card":
            for section in record.get("sections", []):
                result.update(unit["unit_id"] for unit in section.get("units", []))
        elif kind == "review-memory":
            result.add(record["review_memory_id"])
            for section in record.get("sections", []):
                result.update(unit["review_unit_id"] for unit in section.get("units", []))
        elif kind == "question-mapping":
            result.add(record["question_id"])
            result.update(link["question_link_id"] for link in record.get("paper_links", []))
        elif kind in fields:
            value = record.get(fields[kind])
            if isinstance(value, str):
                result.add(value)
            if kind == "pipeline-job-state" and isinstance(record.get("job_id"), str):
                result.add(record["job_id"])
            elif kind == "source-asset-state" and isinstance(record.get("source_asset_id"), str):
                result.add(record["source_asset_id"])
    return result


def _deduplicate(diagnostics: list[Diagnostic]) -> list[Diagnostic]:
    seen: set[tuple[str, str, str | None, str, str, str]] = set()
    result: list[Diagnostic] = []
    for diagnostic in diagnostics:
        key = (
            diagnostic.code,
            diagnostic.record_kind,
            diagnostic.record_id,
            diagnostic.json_path,
            diagnostic.message,
            diagnostic.severity,
        )
        if key not in seen:
            seen.add(key)
            result.append(diagnostic)
    return result
