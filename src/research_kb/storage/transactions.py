from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from research_kb.contracts.validator import validate_record
from research_kb.errors import INCOMPLETE_TRANSACTION, WRITE_CONFLICT, Diagnostic, ResearchKBError
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
EventIdFactory = Callable[[], str]

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
        expected_before_sha256: str | None = None,
        phase_hook: PhaseHook | None = None,
        event_id: str | None = None,
    ) -> TransactionResult:
        resolved_target = self.layout.ensure_writable_target(target)
        with workspace_lock(self.layout.lock_path, timeout=self.lock_timeout):
            before_sha256 = file_sha256(resolved_target)
            if expected_before_sha256 is not None and before_sha256 != expected_before_sha256:
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
            )
            append_process_event(self.layout.process_events_path, event, write_id=resolved_event_id)
            return resolved_event_id

    def recover(self, *, dry_run: bool = True) -> list[dict[str, str]]:
        actions: list[dict[str, str]] = []
        with workspace_lock(self.layout.lock_path, timeout=self.lock_timeout):
            if not self.layout.transactions_root.exists():
                return actions
            events = {item["event_id"]: item for item in read_process_events(self.layout.process_events_path)}
            for path in sorted(self.layout.transactions_root.glob("*.json"), key=lambda item: item.name):
                journal = read_json_document(path, record_kind="transaction-journal")
                self._validate_journal(journal)
                existing_event = events.get(journal["event_id"])
                if journal["phase"] == "complete":
                    expected_event = build_journal_event(journal, journal["result"])
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
                    expected_event = build_journal_event(journal, expected_result)
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
    def _validate_journal(journal: dict[str, Any]) -> None:
        diagnostics = validate_record("transaction-journal", journal, actor="cli")
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
    )
