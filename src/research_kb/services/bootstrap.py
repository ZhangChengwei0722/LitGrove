from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from research_kb.bundle import load_workspace_entries, validate_workspace_entries
from research_kb.contracts.validator import validate_record
from research_kb.errors import (
    INCOMPLETE_TRANSACTION,
    LOCK_TIMEOUT,
    PATH_ESCAPE,
    UNSUPPORTED_VERSION,
    UNKNOWN_SCHEMA_KIND,
    WORKSPACE_IDENTITY_CONFLICT,
    WORKSPACE_LAYOUT_CONFLICT,
    Diagnostic,
    ResearchKBError,
)
from research_kb.storage.json_io import (
    atomic_write_bytes,
    ensure_private_directory,
    file_sha256,
    read_json_document,
    read_jsonl,
    serialize_json,
)
from research_kb.storage.locking import workspace_lock
from research_kb.storage.transactions import build_journal_event
from research_kb.workspace import WorkspaceLayout
from research_kb.workspace_validation import (
    MANAGED_DIRECTORIES,
    PREVIOUS_LAYOUT_CONTRACT_VERSION,
    WorkspaceContext,
    _is_unsafe_link,
    _lexists,
    _validate_workspace_for_bootstrap,
)


DirectoryCreator = Callable[[Path], None]
MarkerWriter = Callable[[Path, bytes, str], None]


@dataclass(frozen=True, slots=True)
class BootstrapResult:
    status: str
    result: str
    dry_run: bool
    workspace_id: str | None
    domain_profile_id: str | None
    managed_actions: tuple[dict[str, str], ...]
    diagnostics: tuple[Diagnostic, ...]
    exit_code: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_version": "1.0",
            "command": "workspace_init",
            "status": self.status,
            "result": self.result,
            "dry_run": self.dry_run,
            "workspace_id": self.workspace_id,
            "domain_profile_id": self.domain_profile_id,
            "managed_actions": list(self.managed_actions),
            "diagnostics": [item.to_dict() for item in self.diagnostics],
            "source_scan_performed": False,
            "process_event_emitted": False,
        }


class WorkspaceBootstrapService:
    def __init__(
        self,
        config_path: Path,
        *,
        lock_timeout: float = 30.0,
        directory_creator: DirectoryCreator = ensure_private_directory,
        marker_writer: MarkerWriter = atomic_write_bytes,
    ):
        self.config_path = config_path
        self.lock_timeout = lock_timeout
        self.directory_creator = directory_creator
        self.marker_writer = marker_writer

    def run(self, *, dry_run: bool = False) -> BootstrapResult:
        try:
            initial = _validate_workspace_for_bootstrap(self.config_path)
        except ResearchKBError as error:
            return self._input_failure(error.diagnostic, dry_run)
        if initial.errors:
            return self._blocked(initial.context, initial.diagnostics, dry_run=dry_run, exit_code=4)
        if dry_run:
            if self._requires_layout_upgrade(initial.context):
                try:
                    self._validate_adoption(initial.context)
                except ResearchKBError as error:
                    return self._blocked(
                        initial.context,
                        (error.diagnostic,),
                        dry_run=True,
                        exit_code=4,
                    )
            return BootstrapResult(
                "success",
                "planned",
                True,
                initial.context.workspace_id,
                initial.context.domain_profile_id,
                tuple(self._plan_actions(initial.context)),
                initial.warnings,
                0,
            )
        return self._apply(initial.context, initial.warnings)

    def _apply(self, initial: WorkspaceContext, warnings: tuple[Diagnostic, ...]) -> BootstrapResult:
        actions: list[dict[str, str]] = []
        adoption_validation = False
        upgrade_validation = False
        try:
            self._ensure_lock_scaffold(initial, actions)
            lock_path = initial.knowledge_root / ".research-kb" / "locks" / "workspace.lock"
            with workspace_lock(lock_path, timeout=self.lock_timeout):
                actions.append({"relative_path": ".research-kb/locks/workspace.lock", "action": "acquire_workspace_lock"})
                under_lock = _validate_workspace_for_bootstrap(self.config_path)
                if under_lock.errors:
                    return self._blocked(
                        under_lock.context,
                        under_lock.diagnostics,
                        dry_run=False,
                        exit_code=4,
                        actions=actions,
                    )
                context = under_lock.context
                warnings = under_lock.warnings
                if (
                    context.knowledge_root != initial.knowledge_root
                    or context.expected_marker != initial.expected_marker
                ):
                    diagnostic = Diagnostic(
                        WORKSPACE_IDENTITY_CONFLICT,
                        "workspace-marker",
                        initial.workspace_id,
                        "/config_fingerprint",
                        "workspace configuration changed during initialization",
                    )
                    return self._blocked(context, (diagnostic,), dry_run=False, exit_code=4, actions=actions)

                if not context.marker_path.exists() and self._has_existing_structured_state(context):
                    adoption_validation = True
                    self._validate_adoption(context)
                    adoption_validation = False
                upgrade = self._requires_layout_upgrade(context)
                if upgrade:
                    upgrade_validation = True
                    self._validate_adoption(context)
                    upgrade_validation = False
                self._ensure_managed_directories(context, actions)
                if upgrade:
                    self.marker_writer(
                        context.marker_path,
                        serialize_json(context.expected_marker),
                        f"workspace-marker-{uuid.uuid4().hex}",
                    )
                    actions.append({"relative_path": ".research-kb/workspace.json", "action": "upgrade_identity_marker"})
                    self._verify_marker(context)
                elif context.marker_path.exists():
                    actions.append({"relative_path": ".research-kb/workspace.json", "action": "already_present"})
                else:
                    self.marker_writer(
                        context.marker_path,
                        serialize_json(context.expected_marker),
                        f"workspace-marker-{uuid.uuid4().hex}",
                    )
                    actions.append({"relative_path": ".research-kb/workspace.json", "action": "write_identity_marker"})
                    self._verify_marker(context)
            self._preserve_lock_file(context, lock_path)
        except ResearchKBError as error:
            exit_code = 4 if adoption_validation or upgrade_validation or error.diagnostic.code in {
                LOCK_TIMEOUT,
                INCOMPLETE_TRANSACTION,
                WORKSPACE_IDENTITY_CONFLICT,
                WORKSPACE_LAYOUT_CONFLICT,
                PATH_ESCAPE,
            } else 2
            return self._blocked(initial, (error.diagnostic,), dry_run=False, exit_code=exit_code, actions=actions)
        except OSError:
            diagnostic = Diagnostic(
                WORKSPACE_LAYOUT_CONFLICT,
                "workspace",
                initial.workspace_id,
                "/workspace/knowledge_root",
                "workspace initialization encountered a filesystem error",
            )
            return self._blocked(initial, (diagnostic,), dry_run=False, exit_code=2, actions=actions)

        lock_action_index = next(
            index
            for index, item in enumerate(actions)
            if item["action"] == "acquire_workspace_lock"
        )
        created = any(
            item["action"] in {"create_directory", "write_identity_marker", "upgrade_identity_marker"}
            for item in actions[lock_action_index + 1 :]
        )
        return BootstrapResult(
            "success",
            "initialized" if created else "no_change",
            False,
            initial.workspace_id,
            initial.domain_profile_id,
            tuple(actions),
            warnings,
            0,
        )

    def _ensure_lock_scaffold(self, context: WorkspaceContext, actions: list[dict[str, str]]) -> None:
        self._ensure_directory(context, context.knowledge_root, ".", actions)
        self._ensure_directory(context, context.knowledge_root / ".research-kb", ".research-kb", actions)
        self._ensure_directory(
            context,
            context.knowledge_root / ".research-kb" / "locks",
            ".research-kb/locks",
            actions,
        )

    def _ensure_managed_directories(self, context: WorkspaceContext, actions: list[dict[str, str]]) -> None:
        existing_actions = {item["relative_path"] for item in actions if item["action"] in {"create_directory", "already_present"}}
        for relative in MANAGED_DIRECTORIES:
            if relative in existing_actions:
                continue
            path = context.knowledge_root / Path(*relative.split("/"))
            self._ensure_directory(context, path, relative, actions)

    def _ensure_directory(
        self,
        context: WorkspaceContext,
        path: Path,
        relative: str,
        actions: list[dict[str, str]],
    ) -> None:
        self._require_safe_managed_chain(context, path, allow_missing_target=True)
        if _lexists(path):
            self._require_safe_managed_chain(context, path, allow_missing_target=False)
            actions.append({"relative_path": relative, "action": "already_present"})
            return
        self.directory_creator(path)
        self._require_safe_managed_chain(context, path, allow_missing_target=False)
        actions.append({"relative_path": relative, "action": "create_directory"})

    def _preserve_lock_file(self, context: WorkspaceContext, path: Path) -> None:
        deadline = time.monotonic() + min(self.lock_timeout, 1.0)
        while True:
            self._require_safe_managed_chain(context, path.parent, allow_missing_target=False)
            if _lexists(path):
                try:
                    self._require_safe_operational_file(context, path)
                except ResearchKBError:
                    if _lexists(path) or time.monotonic() >= deadline:
                        raise
                    time.sleep(0.01)
                    continue
                return
            try:
                atomic_write_bytes(path, b"", f"workspace-lock-{uuid.uuid4().hex}")
            except OSError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.01)
                continue
            self._require_safe_operational_file(context, path)
            self._require_safe_managed_chain(context, path.parent, allow_missing_target=False)
            return

    @staticmethod
    def _require_safe_operational_file(context: WorkspaceContext, path: Path) -> None:
        if _is_unsafe_link(path) or not path.is_file():
            raise ResearchKBError(
                Diagnostic(
                    WORKSPACE_LAYOUT_CONFLICT,
                    "workspace",
                    context.workspace_id,
                    "/workspace/knowledge_root",
                    "workspace lock is not a regular file",
                )
            )

    @staticmethod
    def _require_safe_managed_chain(
        context: WorkspaceContext,
        target: Path,
        *,
        allow_missing_target: bool,
    ) -> None:
        try:
            relative_parts = target.relative_to(context.knowledge_root).parts
        except ValueError:
            if target != context.knowledge_root:
                raise ResearchKBError(
                    Diagnostic(
                        PATH_ESCAPE,
                        "workspace",
                        context.workspace_id,
                        "/workspace/knowledge_root",
                        "managed directory escapes knowledge_root",
                    )
                )
            relative_parts = ()
        current = context.knowledge_root
        candidates = [current]
        for part in relative_parts:
            current = current / part
            candidates.append(current)
        for candidate in candidates:
            if not _lexists(candidate):
                if candidate == target and allow_missing_target:
                    continue
                raise ResearchKBError(
                    Diagnostic(
                        WORKSPACE_LAYOUT_CONFLICT,
                        "workspace",
                        context.workspace_id,
                        "/workspace/knowledge_root",
                        "managed directory chain is incomplete",
                    )
                )
            if _is_unsafe_link(candidate) or not candidate.is_dir():
                raise ResearchKBError(
                    Diagnostic(
                        WORKSPACE_LAYOUT_CONFLICT,
                        "workspace",
                        context.workspace_id,
                        "/workspace/knowledge_root",
                        "managed directory chain contains an unsafe path type",
                    )
                )

    @staticmethod
    def _has_existing_structured_state(context: WorkspaceContext) -> bool:
        for relative in (
            "registry/papers.jsonl",
            "review_queue/items.jsonl",
            "process/events.jsonl",
            "guardian/reports.jsonl",
            "questions/mappings.jsonl",
        ):
            if (context.knowledge_root / Path(*relative.split("/"))).is_file():
                return True
        for relative, pattern in (
            ("parse/by_paper", "*.pages.jsonl"),
            ("paper_cards/by_paper", "*.card.json"),
            ("evidence/by_paper", "*.evidence.jsonl"),
            ("review_memories/by_paper", "*.review.json"),
            (".research-kb/transactions", "*.json"),
        ):
            directory = context.knowledge_root / Path(*relative.split("/"))
            if directory.is_dir() and next(directory.glob(pattern), None) is not None:
                return True
        return False

    @staticmethod
    def _validate_adoption(context: WorkspaceContext) -> None:
        layout = WorkspaceLayout._from_context(context)
        WorkspaceBootstrapService._validate_store_bindings(layout)
        validate_workspace_entries(load_workspace_entries(layout), actor="stored")
        events = {
            item["event_id"]: item
            for item in read_jsonl(layout.process_events_path, record_kind="process-event", id_field="event_id")
        }
        if not layout.transactions_root.is_dir():
            return
        for path in sorted(layout.transactions_root.glob("*.json")):
            journal = read_json_document(path, record_kind="transaction-journal")
            diagnostics = validate_record("transaction-journal", journal, actor="stored")
            if diagnostics:
                raise ResearchKBError(diagnostics[0])
            if path.name != f"{journal['event_id']}.json":
                raise ResearchKBError(
                    Diagnostic(
                        WORKSPACE_LAYOUT_CONFLICT,
                        "transaction-journal",
                        journal["event_id"],
                        "/event_id",
                        "transaction journal filename does not match event_id",
                    )
                )
            if not _journal_target_matches_store(journal["target_store"], journal["target_relative_path"]):
                raise ResearchKBError(
                    Diagnostic(
                        INCOMPLETE_TRANSACTION,
                        "transaction-journal",
                        journal["event_id"],
                        "/target_relative_path",
                        "transaction target path does not match target_store",
                    )
                )
            if journal["phase"] != "complete" or journal["result"] not in {"success", "failure"}:
                raise ResearchKBError(
                    Diagnostic(
                        INCOMPLETE_TRANSACTION,
                        "transaction-journal",
                        journal.get("event_id"),
                        "/phase",
                        "markerless workspace contains an incomplete transaction",
                    )
                )
            expected_event = build_journal_event(journal, journal["result"])
            if events.get(journal["event_id"]) != expected_event:
                raise ResearchKBError(
                    Diagnostic(
                        INCOMPLETE_TRANSACTION,
                        "transaction-journal",
                        journal["event_id"],
                        "/event_id",
                        "transaction journal and process event do not match",
                    )
                )
            target = layout.ensure_writable_target(
                layout.knowledge_root / Path(*journal["target_relative_path"].split("/"))
            )
            expected_digest = journal["after_sha256"] if journal["result"] == "success" else journal["before_sha256"]
            if file_sha256(target) != expected_digest:
                raise ResearchKBError(
                    Diagnostic(
                        INCOMPLETE_TRANSACTION,
                        "transaction-journal",
                        journal["event_id"],
                        "/target_relative_path",
                        "transaction target does not match the completed journal",
                    )
                )

    @staticmethod
    def _validate_store_bindings(layout: WorkspaceLayout) -> None:
        stores = (
            (layout.knowledge_root / "parse" / "by_paper", "*.pages.jsonl", ".pages.jsonl", "parsed-page"),
            (layout.knowledge_root / "evidence" / "by_paper", "*.evidence.jsonl", ".evidence.jsonl", "evidence"),
        )
        for directory, pattern, suffix, kind in stores:
            if not directory.is_dir():
                continue
            for path in sorted(directory.glob(pattern)):
                expected_paper_id = path.name[: -len(suffix)]
                records = read_jsonl(path, record_kind=kind, missing_ok=False)
                if any(record.get("paper_id") != expected_paper_id for record in records):
                    raise ResearchKBError(
                        Diagnostic(
                            WORKSPACE_LAYOUT_CONFLICT,
                            kind,
                            expected_paper_id,
                            "/paper_id",
                            "store filename does not match contained paper_id",
                        )
                    )
        card_directory = layout.knowledge_root / "paper_cards" / "by_paper"
        if card_directory.is_dir():
            for path in sorted(card_directory.glob("*.card.json")):
                expected_paper_id = path.name[: -len(".card.json")]
                card = read_json_document(path, record_kind="paper-card")
                if card.get("paper_id") != expected_paper_id:
                    raise ResearchKBError(
                        Diagnostic(
                            WORKSPACE_LAYOUT_CONFLICT,
                            "paper-card",
                            expected_paper_id,
                            "/paper_id",
                            "store filename does not match contained paper_id",
                        )
                    )
        review_directory = layout.knowledge_root / "review_memories" / "by_paper"
        if review_directory.is_dir():
            for path in sorted(review_directory.glob("*.review.json")):
                expected_paper_id = path.name[: -len(".review.json")]
                memory = read_json_document(path, record_kind="review-memory")
                if memory.get("paper_id") != expected_paper_id:
                    raise ResearchKBError(
                        Diagnostic(
                            WORKSPACE_LAYOUT_CONFLICT,
                            "review-memory",
                            expected_paper_id,
                            "/paper_id",
                            "store filename does not match contained paper_id",
                        )
                    )

    @staticmethod
    def _verify_marker(context: WorkspaceContext) -> None:
        marker = read_json_document(context.marker_path, record_kind="workspace-marker")
        diagnostics = validate_record("workspace-marker", marker, actor="stored")
        if diagnostics or marker != context.expected_marker:
            raise ResearchKBError(
                Diagnostic(
                    WORKSPACE_LAYOUT_CONFLICT,
                    "workspace-marker",
                    context.workspace_id,
                    "/config_fingerprint",
                    "workspace marker read-back verification failed",
                )
            )

    @staticmethod
    def _plan_actions(context: WorkspaceContext) -> list[dict[str, str]]:
        actions: list[dict[str, str]] = []
        paths = ((".", context.knowledge_root),) + tuple(
            (relative, context.knowledge_root / Path(*relative.split("/")))
            for relative in MANAGED_DIRECTORIES
        )
        for relative, path in paths:
            actions.append(
                {"relative_path": relative, "action": "already_present" if path.is_dir() else "create_directory"}
            )
        actions.append({"relative_path": ".research-kb/locks/workspace.lock", "action": "acquire_workspace_lock"})
        actions.append(
            {
                "relative_path": ".research-kb/workspace.json",
                "action": (
                    "upgrade_identity_marker"
                    if WorkspaceBootstrapService._requires_layout_upgrade(context)
                    else "already_present" if context.marker_path.is_file() else "write_identity_marker"
                ),
            }
        )
        return actions

    @staticmethod
    def _requires_layout_upgrade(context: WorkspaceContext) -> bool:
        if not context.marker_path.is_file():
            return False
        marker = read_json_document(context.marker_path, record_kind="workspace-marker")
        expected = dict(context.expected_marker)
        expected["layout_contract_version"] = PREVIOUS_LAYOUT_CONTRACT_VERSION
        return marker == expected

    @staticmethod
    def _blocked(
        context: WorkspaceContext,
        diagnostics: tuple[Diagnostic, ...] | list[Diagnostic],
        *,
        dry_run: bool,
        exit_code: int,
        actions: list[dict[str, str]] | None = None,
    ) -> BootstrapResult:
        redacted = tuple(_redact_diagnostic(context, item) for item in diagnostics)
        return BootstrapResult(
            "failure",
            "blocked",
            dry_run,
            context.workspace_id,
            context.domain_profile_id,
            tuple(actions or ()),
            redacted,
            exit_code,
        )

    @staticmethod
    def _input_failure(diagnostic: Diagnostic, dry_run: bool) -> BootstrapResult:
        if diagnostic.code in {UNSUPPORTED_VERSION, UNKNOWN_SCHEMA_KIND}:
            exit_code = 3
        else:
            exit_code = 2
        return BootstrapResult("failure", "blocked", dry_run, None, None, (), (diagnostic,), exit_code)


__all__ = ["MANAGED_DIRECTORIES", "BootstrapResult", "WorkspaceBootstrapService"]


def _redact_diagnostic(context: WorkspaceContext, diagnostic: Diagnostic) -> Diagnostic:
    replacements = [
        (context.config.path, "<workspace-config>"),
        (context.domain_profile.path, "<domain-profile>"),
        (context.knowledge_root, "<knowledge-root>"),
        (context.local_inbox, "<local-inbox>"),
        *((item.path, f"<source-root:{item.root_id}>") for item in context.source_root_items),
        (context.config.base_dir, "<workspace-root>"),
    ]
    replacements.sort(key=lambda item: len(str(item[0])), reverse=True)
    json_path = diagnostic.json_path
    message = diagnostic.message
    for path, label in replacements:
        for value in {str(path), path.as_posix()}:
            json_path = json_path.replace(value, label)
            message = message.replace(value, label)
    return Diagnostic(
        diagnostic.code,
        diagnostic.record_kind,
        diagnostic.record_id,
        json_path,
        message,
        diagnostic.severity,
    )


def _journal_target_matches_store(target_store: str, relative_path: str) -> bool:
    exact = {
        "registry": "registry/papers.jsonl",
        "review_queue": "review_queue/items.jsonl",
        "guardian_reports": "guardian/reports.jsonl",
        "question_mappings": "questions/mappings.jsonl",
    }
    if target_store in exact:
        return relative_path == exact[target_store]
    patterns = {
        "parsed_pages": ("parse/by_paper/", ".pages.jsonl"),
        "paper_cards": ("paper_cards/by_paper/", ".card.json"),
        "evidence": ("evidence/by_paper/", ".evidence.jsonl"),
        "review_memories": ("review_memories/by_paper/", ".review.json"),
    }
    match = patterns.get(target_store)
    if match is None:
        return False
    prefix, suffix = match
    remainder = relative_path[len(prefix) :] if relative_path.startswith(prefix) else ""
    return bool(remainder) and "/" not in remainder and remainder.endswith(suffix)
