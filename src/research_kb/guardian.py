from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from research_kb.bundle import BundleEntry, load_workspace_entries
from research_kb.contracts.validator import validate_bundle, validate_record
from research_kb.errors import (
    GROUNDING_MISMATCH,
    INCOMPLETE_TRANSACTION,
    PATH_ESCAPE,
    SNAPSHOT_MISMATCH,
    Diagnostic,
    ResearchKBError,
)
from research_kb.identifiers import Namespace, allocate_id
from research_kb.process_events import timestamp
from research_kb.services.question_mapping import mapping_freshness_diagnostics
from research_kb.storage.json_io import file_sha256, read_json_document, read_jsonl, serialize_jsonl
from research_kb.storage.transactions import TransactionManager, TransactionResult, build_journal_event
from research_kb.workspace import WorkspaceLayout


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
            for kind, mapping in entries:
                if kind == "question-mapping" and not validate_record(
                    "question-mapping", mapping, actor="stored"
                ):
                    diagnostics.extend(mapping_freshness_diagnostics(mapping, entries))
        diagnostics.extend(self._canonical_path_diagnostics())
        process_events = [record for kind, record in entries if kind == "process-event"]
        diagnostics.extend(self._transaction_diagnostics(process_events))
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
        for kind, paper in entries:
            if kind != "registry-paper":
                continue
            paper_id = paper["paper_id"]
            try:
                _, source = self.layout.resolve_source(
                    paper["source_ref"]["root_id"],
                    paper["source_ref"]["relative_path"],
                )
            except ResearchKBError as error:
                diagnostics.append(error.diagnostic)
                continue
            expected = paper["source_fingerprint"]["value"]
            if file_sha256(source) != expected:
                diagnostics.append(
                    Diagnostic(
                        GROUNDING_MISMATCH,
                        "registry-paper",
                        paper_id,
                        "/source_fingerprint",
                        "registered source is missing or its SHA-256 fingerprint changed",
                    )
                )
        return diagnostics

    def _canonical_path_diagnostics(self) -> list[Diagnostic]:
        diagnostics: list[Diagnostic] = []
        paths = [
            self.layout.registry_path,
            self.layout.review_queue_path,
            self.layout.process_events_path,
            self.layout.guardian_reports_path,
            self.layout.question_mappings_path,
        ]
        for directory, pattern in (
            (self.layout.knowledge_root / "parse" / "by_paper", "*.pages.jsonl"),
            (self.layout.knowledge_root / "paper_cards" / "by_paper", "*.card.json"),
            (self.layout.knowledge_root / "evidence" / "by_paper", "*.evidence.jsonl"),
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


def _finding_from_diagnostic(diagnostic: Diagnostic, defined_ids: set[str]) -> dict[str, Any]:
    remediation = {
        GROUNDING_MISMATCH: "Restore the registered source or register the changed asset as a new controlled input.",
        INCOMPLETE_TRANSACTION: "Run transaction recover and inspect ambiguous digests before any further mutation.",
        PATH_ESCAPE: "Move the canonical target under knowledge_root and correct the workspace path contract.",
        SNAPSHOT_MISMATCH: "Refresh the Question Mapping from its current Paper Card, evidence, and review queue inputs.",
    }.get(diagnostic.code, "Inspect the referenced structured record and correct the reported contract violation.")
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
        "evidence": "evidence_id",
        "review-queue": "queue_id",
        "process-event": "event_id",
        "guardian-report": "guardian_report_id",
        "question-mapping": "question_id",
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
        elif kind == "question-mapping":
            result.add(record["question_id"])
            result.update(link["question_link_id"] for link in record.get("paper_links", []))
        elif kind in fields:
            value = record.get(fields[kind])
            if isinstance(value, str):
                result.add(value)
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
