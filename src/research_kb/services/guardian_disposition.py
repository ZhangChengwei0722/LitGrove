from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from research_kb.bundle import load_workspace_entries, records_of_kind, validate_workspace_entries
from research_kb.contracts.validator import validate_record
from research_kb.errors import (
    DUPLICATE_ID,
    INVALID_AUTHORITY,
    UNRESOLVED_REFERENCE,
    WRITE_CONFLICT,
    Diagnostic,
    ResearchKBError,
)
from research_kb.guardian_dispositions import (
    DISPOSITION_TRANSITIONS,
    INITIAL_DISPOSITION_STATUSES,
    TERMINAL_DISPOSITION_STATUSES,
    current_guardian_dispositions,
    finding_digest,
    guardian_disposition_diagnostics,
)
from research_kb.identifiers import Namespace, allocate_id, validate_id
from research_kb.process_events import timestamp
from research_kb.storage.json_io import file_sha256, read_jsonl, serialize_jsonl
from research_kb.storage.transactions import TransactionManager, TransactionResult
from research_kb.workspace import WorkspaceLayout


IdAllocator = Callable[[Namespace], str]


@dataclass(frozen=True, slots=True)
class GuardianDispositionResult:
    disposition: dict[str, Any]
    transaction: TransactionResult | None


class GuardianFindingDispositionService:
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

    def record(
        self,
        *,
        guardian_report_id: str,
        finding_index: int,
        status: str,
        rationale: str,
        expected_previous_disposition_id: str | None,
        actor: str,
        fixture_origin: str | None = None,
    ) -> GuardianDispositionResult:
        guardian_report_id = validate_id(guardian_report_id, Namespace.GUARDIAN_REPORT)
        if expected_previous_disposition_id is not None:
            expected_previous_disposition_id = validate_id(
                expected_previous_disposition_id,
                Namespace.GUARDIAN_DISPOSITION,
            )
        if actor not in {"user", "cli"}:
            raise ResearchKBError(
                Diagnostic(INVALID_AUTHORITY, "guardian-finding-disposition", None, "/actor", "Guardian finding disposition requires user or Core authority")
            )
        if actor == "cli" and status != "superseded":
            raise ResearchKBError(
                Diagnostic(INVALID_AUTHORITY, "guardian-finding-disposition", None, "/status", "Core authority may only supersede a Guardian disposition")
            )
        if not isinstance(finding_index, int) or isinstance(finding_index, bool) or finding_index < 0:
            raise ResearchKBError(
                Diagnostic(UNRESOLVED_REFERENCE, "guardian-finding-disposition", None, "/finding_index", "Guardian finding index is invalid")
            )

        entries = load_workspace_entries(self.layout)
        validate_workspace_entries(entries)
        reports = records_of_kind(entries, "guardian-report")
        report = next((item for item in reports if item["guardian_report_id"] == guardian_report_id), None)
        if report is None:
            raise ResearchKBError(
                Diagnostic(UNRESOLVED_REFERENCE, "guardian-finding-disposition", None, "/guardian_report_id", "Guardian report does not exist")
            )
        if finding_index >= len(report["findings"]):
            raise ResearchKBError(
                Diagnostic(UNRESOLVED_REFERENCE, "guardian-finding-disposition", None, "/finding_index", "Guardian finding index is out of range")
            )

        existing = records_of_kind(entries, "guardian-finding-disposition")
        heads = current_guardian_dispositions(existing, reports)
        current = next(
            (
                item
                for item in heads
                if item["guardian_report_id"] == guardian_report_id
                and item["finding_index"] == finding_index
            ),
            None,
        )
        actual_previous = current["disposition_id"] if current is not None else None
        if actual_previous != expected_previous_disposition_id:
            if (
                current is not None
                and current["previous_disposition_id"] == expected_previous_disposition_id
                and current["status"] == status
                and current["rationale"] == rationale
                and current["actor"] == actor
            ):
                return GuardianDispositionResult(current, None)
            raise ResearchKBError(
                Diagnostic(WRITE_CONFLICT, "guardian-finding-disposition", actual_previous, "/previous_disposition_id", "Guardian finding disposition changed before update")
            )

        if current is None:
            if status not in INITIAL_DISPOSITION_STATUSES:
                raise ResearchKBError(
                    Diagnostic(WRITE_CONFLICT, "guardian-finding-disposition", None, "/status", "Guardian disposition root status is invalid")
                )
        elif current["status"] in TERMINAL_DISPOSITION_STATUSES:
            raise ResearchKBError(
                Diagnostic(WRITE_CONFLICT, "guardian-finding-disposition", current["disposition_id"], "/status", "terminal Guardian disposition cannot have a successor")
            )
        elif status not in DISPOSITION_TRANSITIONS.get(current["status"], frozenset()):
            raise ResearchKBError(
                Diagnostic(WRITE_CONFLICT, "guardian-finding-disposition", current["disposition_id"], "/status", "Guardian disposition transition is invalid")
            )

        disposition_id = self.id_allocator(Namespace.GUARDIAN_DISPOSITION)
        validate_id(disposition_id, Namespace.GUARDIAN_DISPOSITION)
        if disposition_id in {item["disposition_id"] for item in existing}:
            raise ResearchKBError(
                Diagnostic(DUPLICATE_ID, "guardian-finding-disposition", disposition_id, "/disposition_id", "allocated Guardian disposition ID is already in use")
            )
        disposition = {
            "schema_version": "1.0",
            "disposition_id": disposition_id,
            "workspace_id": self.layout.workspace_id,
            "guardian_report_id": guardian_report_id,
            "finding_index": finding_index,
            "finding_digest": finding_digest(report["findings"][finding_index]),
            "status": status,
            "rationale": rationale,
            "previous_disposition_id": actual_previous,
            "actor": actor,
            "created_at": timestamp(self.transactions.clock),
        }
        if fixture_origin is not None:
            disposition["fixture_origin"] = fixture_origin
        diagnostics = validate_record("guardian-finding-disposition", disposition, actor="stored")
        if diagnostics:
            raise ResearchKBError(diagnostics[0])

        proposed = [*existing, disposition]
        chain_diagnostics = guardian_disposition_diagnostics(proposed, reports)
        if chain_diagnostics:
            raise ResearchKBError(chain_diagnostics[0])
        target = self.layout.guardian_finding_dispositions_path
        before_sha256 = file_sha256(target)

        def validate_temp(path: Path) -> None:
            temporary = read_jsonl(
                path,
                record_kind="guardian-finding-disposition",
                missing_ok=False,
                id_field="disposition_id",
            )
            temporary_diagnostics = guardian_disposition_diagnostics(temporary, reports)
            if temporary_diagnostics:
                raise ResearchKBError(temporary_diagnostics[0])
            current_entries = load_workspace_entries(
                self.layout,
                overrides={target: [("guardian-finding-disposition", item) for item in temporary]},
            )
            validate_workspace_entries(current_entries)

        transaction = self.transactions.promote_bytes(
            target=target,
            content=serialize_jsonl(proposed),
            target_store="guardian_finding_dispositions",
            operation="guardian_finding_disposition_record",
            actor=actor,
            input_refs=[
                guardian_report_id,
                *([actual_previous] if actual_previous is not None else []),
            ],
            output_refs=[disposition_id],
            validator=validate_temp,
            expected_before_sha256=before_sha256,
        )
        return GuardianDispositionResult(disposition, transaction)


__all__ = ["GuardianDispositionResult", "GuardianFindingDispositionService"]
