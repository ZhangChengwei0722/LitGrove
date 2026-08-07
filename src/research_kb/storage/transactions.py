from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from research_kb.contracts.validator import RecordValidationSession, validate_record
from research_kb.errors import (
    INCOMPLETE_TRANSACTION,
    PATH_ESCAPE,
    WORKSPACE_LAYOUT_CONFLICT,
    WRITE_CONFLICT,
    Diagnostic,
    ResearchKBError,
)
from research_kb.identifiers import Namespace, allocate_id
from research_kb.process_events import Clock, append_process_event, build_process_event, read_process_events, timestamp, utc_now
from research_kb.storage.json_io import (
    atomic_write_bytes,
    file_sha256,
    read_json_document,
    replace_temp,
    serialize_json,
    sha256_bytes,
    write_fsynced_temp,
)
from research_kb.storage.locking import workspace_lock
from research_kb.workspace import WorkspaceLayout


PhaseHook = Callable[[str], None]
TemporaryValidator = Callable[[Path], None]
PostReplaceValidator = Callable[[], None]
LockedPrecondition = Callable[[], None]
EventIdFactory = Callable[[], str]
_EXPECTED_BEFORE_UNSET = object()

MANUAL_RESOLUTION_ACTIONS = frozenset({
    "completed_event_missing",
    "event_content_mismatch",
    "journal_result_mismatch",
    "manual_resolution_required",
    "target_digest_ambiguous",
})


@dataclass(frozen=True, slots=True)
class TransactionResult:
    event_id: str
    target: Path
    before_sha256: str | None
    after_sha256: str


class TransactionManager:
    def __init__(
        self,
        layout: WorkspaceLayout,
        *,
        lock_timeout: float = 30.0,
        clock: Clock = utc_now,
        event_id_factory: EventIdFactory | None = None,
    ):
        self.layout = layout
        self.lock_timeout = lock_timeout
        self.clock = clock
        self.event_id_factory = event_id_factory or (lambda: allocate_id(Namespace.PROCESS_EVENT))

    def promote_bytes(
        self,
        *,
        target: Path,
        content: bytes,
        target_store: str,
        operation: str,
        actor: str,
        input_refs: list[str],
        output_refs: list[str],
        validator: TemporaryValidator | None = None,
        post_replace_validator: PostReplaceValidator | None = None,
        locked_precondition: LockedPrecondition | None = None,
        expected_before_sha256: str | None | object = _EXPECTED_BEFORE_UNSET,
        phase_hook: PhaseHook | None = None,
        event_id: str | None = None,
        job_id: str | None = None,
    ) -> TransactionResult:
        resolved_target = self.layout.ensure_writable_target(target)
        with workspace_lock(self.layout.lock_path, timeout=self.lock_timeout):
            if locked_precondition is not None:
                locked_precondition()
            before_sha256 = file_sha256(resolved_target)
            if expected_before_sha256 is not _EXPECTED_BEFORE_UNSET and before_sha256 != expected_before_sha256:
                raise ResearchKBError(
                    Diagnostic(WRITE_CONFLICT, "transaction", None, "", "canonical target changed before promotion")
                )
            event_id = event_id or self.event_id_factory()
            self._ensure_event_id_available(event_id)
            created_at = timestamp(self.clock)
            after_sha256 = sha256_bytes(content)
            journal = {
                "schema_version": "1.0",
                "event_id": event_id,
                **({"job_id": job_id} if job_id is not None else {}),
                "operation": operation,
                "actor": actor,
                "target_store": target_store,
                "target_relative_path": self.layout.target_relative_path(resolved_target),
                "before_sha256": before_sha256,
                "after_sha256": after_sha256,
                "input_refs": input_refs,
                "output_refs": output_refs,
                "phase": "prepared",
                "result": None,
                "created_at": created_at,
                "updated_at": created_at,
            }
            self._write_journal(journal)
            temporary: Path | None = None
            try:
                temporary = write_fsynced_temp(resolved_target, content, event_id)
                if validator is not None:
                    validator(temporary)
                if phase_hook is not None:
                    phase_hook("prepared")
                replace_temp(temporary, resolved_target)
                temporary = None
                self._set_phase(journal, "target_replaced")
                if phase_hook is not None:
                    phase_hook("target_replaced")
                if post_replace_validator is not None:
                    try:
                        post_replace_validator()
                    except Exception as validation_error:
                        self._set_state(journal, phase="needs_resolution", result="needs_resolution")
                        raise ResearchKBError(
                            Diagnostic(
                                INCOMPLETE_TRANSACTION,
                                "transaction",
                                event_id,
                                "",
                                "post-replacement validation failed; manual resolution is required",
                            )
                        ) from validation_error
                event = build_journal_event(journal, "success")
                append_process_event(self.layout.process_events_path, event, write_id=event_id)
                self._set_state(journal, phase="event_recorded", result="success")
                self._set_state(journal, phase="complete")
                return TransactionResult(event_id, resolved_target, before_sha256, after_sha256)
            except BaseException as error:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
                if isinstance(error, Exception) and journal["phase"] != "needs_resolution":
                    self._handle_failure(error, journal, resolved_target)
                raise

    def record_failure(
        self,
        *,
        operation: str,
        actor: str,
        input_refs: list[str],
        event_id: str | None = None,
        job_id: str | None = None,
    ) -> str:
        with workspace_lock(self.layout.lock_path, timeout=self.lock_timeout):
            resolved_event_id = event_id or self.event_id_factory()
            self._ensure_event_id_available(resolved_event_id)
            event = build_process_event(
                event_id=resolved_event_id,
                operation=operation,
                actor=actor,
                result="failure",
                input_refs=input_refs,
                output_refs=[],
                created_at=timestamp(self.clock),
                job_id=job_id,
            )
            append_process_event(self.layout.process_events_path, event, write_id=resolved_event_id)
            return resolved_event_id

    def recover(self, *, dry_run: bool = True) -> list[dict[str, str]]:
        actions: list[dict[str, str]] = []
        with workspace_lock(self.layout.lock_path, timeout=self.lock_timeout):
            if not self.layout.transactions_root.exists():
                return actions
            events = {item["event_id"]: item for item in read_process_events(self.layout.process_events_path)}
            journal_validation = RecordValidationSession("transaction-journal", actor="cli")
            for path in sorted(self.layout.transactions_root.glob("*.json"), key=lambda item: item.name):
                journal = read_json_document(path, record_kind="transaction-journal")
                self._validate_journal(journal, session=journal_validation)
                existing_event = events.get(journal["event_id"])
                if journal["phase"] == "complete":
                    expected_event = expected_journal_event(journal, journal["result"])
                    if existing_event is None:
                        actions.append(self._needs_resolution(journal, dry_run, "completed_event_missing"))
                    elif existing_event != expected_event:
                        actions.append(self._needs_resolution(journal, dry_run, "event_content_mismatch"))
                    continue
                if journal["phase"] == "needs_resolution":
                    actions.append({"event_id": journal["event_id"], "action": "manual_resolution_required"})
                    continue
                target = self.layout.ensure_writable_target(
                    self.layout.knowledge_root / Path(*journal["target_relative_path"].split("/"))
                )
                current = file_sha256(target)
                if current not in {journal["before_sha256"], journal["after_sha256"]}:
                    actions.append(self._needs_resolution(journal, dry_run, "target_digest_ambiguous"))
                    continue
                expected_result = "success" if current == journal["after_sha256"] else "failure"
                if journal["result"] not in {None, expected_result}:
                    actions.append(self._needs_resolution(journal, dry_run, "journal_result_mismatch"))
                    continue
                if existing_event is not None:
                    expected_event = expected_journal_event(journal, expected_result)
                    if existing_event != expected_event:
                        actions.append(self._needs_resolution(journal, dry_run, "event_content_mismatch"))
                    else:
                        actions.append(self._complete_recovery(journal, dry_run, expected_result, "event_already_recorded"))
                    continue
                if current == journal["after_sha256"]:
                    actions.append(self._recover_event(journal, dry_run, "success", "append_missing_success_event"))
                elif current == journal["before_sha256"]:
                    actions.append(self._recover_event(journal, dry_run, "failure", "append_missing_failure_event"))
        return actions

    def _handle_failure(self, error: Exception, journal: dict[str, Any], target: Path) -> None:
        current = file_sha256(target)
        if current == journal["before_sha256"]:
            try:
                event = build_journal_event(journal, "failure")
                append_process_event(self.layout.process_events_path, event, write_id=journal["event_id"])
                self._set_state(journal, phase="event_recorded", result="failure")
                self._set_state(journal, phase="complete")
                return
            except Exception as event_error:
                raise ResearchKBError(
                    Diagnostic(INCOMPLETE_TRANSACTION, "transaction", journal["event_id"], "", "failure event could not be recorded")
                ) from event_error
        if current == journal["after_sha256"]:
            raise ResearchKBError(
                Diagnostic(INCOMPLETE_TRANSACTION, "transaction", journal["event_id"], "", "target was replaced but process event is incomplete")
            ) from error
        self._set_state(journal, phase="needs_resolution", result="needs_resolution")
        raise ResearchKBError(
            Diagnostic(INCOMPLETE_TRANSACTION, "transaction", journal["event_id"], "", "transaction target digest is ambiguous")
        ) from error

    def _recover_event(
        self,
        journal: dict[str, Any],
        dry_run: bool,
        result: str,
        action: str,
    ) -> dict[str, str]:
        if not dry_run:
            event = build_journal_event(journal, result)
            append_process_event(self.layout.process_events_path, event, write_id=journal["event_id"])
            self._set_state(journal, phase="event_recorded", result=result)
            self._set_state(journal, phase="complete")
        return {"event_id": journal["event_id"], "action": action}

    def _complete_recovery(
        self,
        journal: dict[str, Any],
        dry_run: bool,
        result: str,
        action: str,
    ) -> dict[str, str]:
        if not dry_run:
            self._set_state(journal, phase="complete", result=result)
        return {"event_id": journal["event_id"], "action": action}

    def _needs_resolution(self, journal: dict[str, Any], dry_run: bool, action: str) -> dict[str, str]:
        if not dry_run:
            self._set_state(journal, phase="needs_resolution", result="needs_resolution")
        return {"event_id": journal["event_id"], "action": action}

    def _set_phase(self, journal: dict[str, Any], phase: str) -> None:
        self._set_state(journal, phase=phase)

    def _set_state(self, journal: dict[str, Any], *, phase: str, result: str | None = None) -> None:
        journal["phase"] = phase
        if result is not None:
            journal["result"] = result
        journal["updated_at"] = timestamp(self.clock)
        self._write_journal(journal)

    def _ensure_event_id_available(self, event_id: str) -> None:
        event_exists = any(item["event_id"] == event_id for item in read_process_events(self.layout.process_events_path))
        if event_exists or self.layout.journal_path(event_id).exists():
            raise ResearchKBError(
                Diagnostic(WRITE_CONFLICT, "transaction", event_id, "/event_id", "event ID is already in use")
            )

    def _write_journal(self, journal: dict[str, Any]) -> None:
        self._validate_journal(journal)
        path = self.layout.ensure_writable_target(self.layout.journal_path(journal["event_id"]))
        atomic_write_bytes(path, serialize_json(journal), f"{journal['event_id']}-journal")

    @staticmethod
    def _validate_journal(
        journal: dict[str, Any],
        *,
        session: RecordValidationSession | None = None,
    ) -> None:
        diagnostics = (
            session.validate(journal)
            if session is not None
            else validate_record("transaction-journal", journal, actor="cli")
        )
        if diagnostics:
            raise ResearchKBError(diagnostics[0])


def build_journal_event(journal: dict[str, Any], result: str) -> dict[str, Any]:
    return build_process_event(
        event_id=journal["event_id"],
        operation=journal["operation"],
        actor=journal["actor"],
        result=result,
        input_refs=journal["input_refs"],
        output_refs=journal["output_refs"] if result == "success" else [],
        created_at=journal["created_at"],
        job_id=journal.get("job_id"),
    )


def expected_journal_event(journal: dict[str, Any], result: str) -> dict[str, Any]:
    event = {
        "schema_version": "1.0",
        "event_id": journal["event_id"],
        "operation": journal["operation"],
        "actor": journal["actor"],
        "result": result,
        "input_refs": journal["input_refs"],
        "output_refs": journal["output_refs"] if result == "success" else [],
        "created_at": journal["created_at"],
    }
    if journal.get("job_id") is not None:
        event["job_id"] = journal["job_id"]
    return event


_TRANSACTION_EXACT_TARGETS = {
    "registry": "registry/papers.jsonl",
    "source_assets": "registry/source_assets.jsonl",
    "identity_corrections": "registry/identity_corrections.jsonl",
    "review_queue": "review_queue/items.jsonl",
    "guardian_reports": "guardian/reports.jsonl",
    "guardian_finding_dispositions": "guardian/finding_dispositions.jsonl",
    "pipeline_jobs": "process/jobs.jsonl",
    "source_adequacy": "process/source_adequacy.jsonl",
    "question_mappings": "questions/mappings.jsonl",
    "discovery_candidates": "discovery/candidates.jsonl",
    "agent_tasks": "process/agent_tasks.jsonl",
    "maintenance_work": "process/maintenance.jsonl",
    "trusted_parse_authorities": "process/trusted_parse_authorities.jsonl",
    "step7_synthesis": "step7/synthesis.jsonl",
    "step7_review_angles": "step7/review_angles.jsonl",
    "step7_insights": "step7/insights.jsonl",
    "step7_cross_views": "step7/cross_views.jsonl",
}

_TRANSACTION_PATTERN_TARGETS = {
    "parsed_pages": ("parse/by_paper/", ".pages.jsonl"),
    "paper_cards": ("paper_cards/by_paper/", ".card.json"),
    "evidence": ("evidence/by_paper/", ".evidence.jsonl"),
    "review_memories": ("review_memories/by_paper/", ".review.json"),
    "primary_bundles": ("primary_bundles/by_paper/", ".primary.json"),
    "review_bundles": ("review_bundles/by_paper/", ".review-bundle.json"),
    "organization_directions": ("organization/directions/by_id/", ".direction-bundle.json"),
    "organization_field_map": ("organization/field_map/by_id/", ".field-map-bundle.json"),
    "organization_questions": ("organization/questions/by_id/", ".question-revision-bundle.json"),
    "organization_tags": ("organization/tags/by_id/", ".tag-bundle.json"),
    "organization_tag_links": ("organization/tag_links/by_id/", ".tag-link-bundle.json"),
    "organization_screening_criteria": (
        "organization/screening_criteria/by_id/",
        ".screening-criteria-bundle.json",
    ),
    "organization_screening_decisions": (
        "organization/screening_decisions/by_id/",
        ".screening-decision-bundle.json",
    ),
}


def transaction_target_matches_store(target_store: str, relative_path: str) -> bool:
    """Return whether a journal target is bound to its declared store."""
    exact = _TRANSACTION_EXACT_TARGETS.get(target_store)
    if exact is not None:
        return relative_path == exact
    pattern = _TRANSACTION_PATTERN_TARGETS.get(target_store)
    if pattern is None:
        return False
    prefix, suffix = pattern
    remainder = relative_path[len(prefix) :] if relative_path.startswith(prefix) else ""
    return bool(remainder) and "/" not in remainder and remainder.endswith(suffix)


def transaction_integrity_diagnostics(
    layout: WorkspaceLayout,
    journal_path: Path | Iterable[Path],
    process_events: list[dict[str, Any]],
    *,
    validation: RecordValidationSession | None = None,
) -> list[Diagnostic]:
    """Validate completed transaction journals without changing workspace state.

    A target may have several completed journals. Older journals describe an
    intermediate target state, so their ``after_sha256`` must chain into the
    next journal rather than equal the current file digest.
    """
    journal_paths = (
        [journal_path]
        if isinstance(journal_path, Path)
        else sorted((Path(path) for path in journal_path), key=lambda path: path.name)
    )
    diagnostics: list[Diagnostic] = []
    completed: list[tuple[Path, dict[str, Any], int | None, Path]] = []
    for path in journal_paths:
        try:
            journal = read_json_document(path, record_kind="transaction-journal")
            journal_diagnostics = (
                validation.validate(journal)
                if validation is not None
                else validate_record("transaction-journal", journal, actor="stored")
            )
        except ResearchKBError as error:
            diagnostics.append(error.diagnostic)
            continue
        if journal_diagnostics:
            diagnostics.extend(journal_diagnostics)
            continue

        event_id = journal["event_id"]
        if path.name != f"{event_id}.json":
            diagnostics.append(
                Diagnostic(
                    WORKSPACE_LAYOUT_CONFLICT,
                    "transaction-journal",
                    event_id,
                    "/event_id",
                    "transaction journal filename does not match event_id",
                )
            )
        if not transaction_target_matches_store(journal["target_store"], journal["target_relative_path"]):
            diagnostics.append(
                Diagnostic(
                    INCOMPLETE_TRANSACTION,
                    "transaction-journal",
                    event_id,
                    "/target_relative_path",
                    "transaction target path does not match target_store",
                )
            )
        if journal["phase"] != "complete" or journal["result"] not in {"success", "failure"}:
            diagnostics.append(
                Diagnostic(
                    INCOMPLETE_TRANSACTION,
                    "transaction-journal",
                    event_id,
                    "/phase",
                    f"transaction journal is not complete: {journal['phase']}",
                )
            )
            continue

        matching_events = [
            (index, item)
            for index, item in enumerate(process_events)
            if item.get("event_id") == event_id
        ]
        event_position = matching_events[0][0] if len(matching_events) == 1 else None
        if len(matching_events) != 1:
            diagnostics.append(
                Diagnostic(
                    INCOMPLETE_TRANSACTION,
                    "transaction-journal",
                    event_id,
                    "/event_id",
                    f"completed transaction must have exactly one process event; found {len(matching_events)}",
                )
            )
        elif matching_events[0][1] != expected_journal_event(journal, journal["result"]):
            diagnostics.append(
                Diagnostic(
                    INCOMPLETE_TRANSACTION,
                    "transaction-journal",
                    event_id,
                    "/event_id",
                    "completed transaction process event does not match its journal",
                )
            )

        try:
            target = layout.ensure_writable_target(
                layout.knowledge_root.joinpath(*journal["target_relative_path"].split("/"))
            )
        except (OSError, ResearchKBError) as error:
            diagnostic = (
                error.diagnostic
                if isinstance(error, ResearchKBError)
                else Diagnostic(
                    PATH_ESCAPE,
                    "transaction-journal",
                    event_id,
                    "/target_relative_path",
                    "transaction target could not be inspected safely",
                )
            )
            diagnostics.append(diagnostic)
            continue
        completed.append((path, journal, event_position, target))

    grouped: dict[tuple[str, str], list[tuple[Path, dict[str, Any], int | None, Path]]] = {}
    for item in completed:
        grouped.setdefault((item[1]["target_store"], item[1]["target_relative_path"]), []).append(item)
    for items in grouped.values():
        items.sort(
            key=lambda item: (
                item[2] is None,
                item[2] if item[2] is not None else 0,
                item[1]["created_at"],
                item[0].name,
            )
        )
        previous_digest: str | None | object = _EXPECTED_BEFORE_UNSET
        for _path, journal, _event_position, _target in items:
            if previous_digest is not _EXPECTED_BEFORE_UNSET and journal["before_sha256"] != previous_digest:
                diagnostics.append(
                    Diagnostic(
                        INCOMPLETE_TRANSACTION,
                        "transaction-journal",
                        journal["event_id"],
                        "/before_sha256",
                        "completed transaction target digest chain does not match its predecessor",
                    )
                )
            previous_digest = (
                journal["after_sha256"]
                if journal["result"] == "success"
                else journal["before_sha256"]
            )
        latest_path, latest, _event_position, latest_target = items[-1]
        try:
            current_digest = file_sha256(latest_target)
        except OSError:
            diagnostics.append(
                Diagnostic(
                    INCOMPLETE_TRANSACTION,
                    "transaction-journal",
                    latest["event_id"],
                    "/target_relative_path",
                    "transaction target could not be inspected safely",
                )
            )
            continue
        if current_digest != previous_digest:
            diagnostics.append(
                Diagnostic(
                    INCOMPLETE_TRANSACTION,
                    "transaction-journal",
                    latest["event_id"],
                    "/target_relative_path",
                    "transaction target does not match the completed journal",
                )
            )
    return diagnostics
