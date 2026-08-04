from __future__ import annotations

import os
import shutil
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

from research_kb.catalog.models import canonical_digest
from research_kb.contracts.validator import RecordValidationSession, validate_record
from research_kb.errors import (
    INVALID_AUTHORITY,
    SCHEMA_VALIDATION_FAILED,
    WRITE_CONFLICT,
    Diagnostic,
    ResearchKBError,
)
from research_kb.identifiers import Namespace, allocate_id, validate_id
from research_kb.process_events import (
    Clock,
    append_process_event,
    build_process_event,
    read_process_event_subset,
    timestamp,
    utc_now,
)
from research_kb.storage.json_io import (
    atomic_write_bytes,
    ensure_private_directory,
    file_sha256,
    read_json_document,
    read_jsonl,
    serialize_json,
    serialize_jsonl,
    sha256_bytes,
)
from research_kb.storage.locking import workspace_lock
from research_kb.storage.transactions import (
    TransactionManager,
    expected_journal_event,
)
from research_kb.workspace import WorkspaceLayout


ARCHIVE_PROFILE_ID = "p11-transaction-journal-archive-v1"
ArchiveIdFactory = Callable[[], str]
EventIdFactory = Callable[[], str]
MaintenanceIdFactory = Callable[[], str]
PhaseHook = Callable[[str], None]


class OperationalMaintenanceService:
    def __init__(
        self,
        layout: WorkspaceLayout,
        *,
        lock_timeout: float = 30.0,
        archive_id_factory: ArchiveIdFactory | None = None,
        event_id_factory: EventIdFactory | None = None,
        clock: Clock = utc_now,
        phase_hook: PhaseHook | None = None,
    ):
        self.layout = layout
        self.lock_timeout = lock_timeout
        self.archive_id_factory = archive_id_factory or (
            lambda: allocate_id(Namespace.OPERATIONAL_ARCHIVE)
        )
        self.event_id_factory = event_id_factory or (
            lambda: allocate_id(Namespace.PROCESS_EVENT)
        )
        self.clock = clock
        self.phase_hook = phase_hook

    def preview_journal_archive(self) -> dict[str, Any]:
        with workspace_lock(self.layout.lock_path, timeout=self.lock_timeout):
            eligible = self._eligible_journals()
            return _archive_preview(eligible)

    def archive_journals(
        self,
        *,
        expected_basis_digest: str,
        actor: str,
    ) -> dict[str, Any]:
        if actor != "user":
            raise _error(INVALID_AUTHORITY, "journal archival requires explicit user authority")
        with workspace_lock(self.layout.lock_path, timeout=self.lock_timeout):
            recovered = self._recover_matching_archive(expected_basis_digest)
            if recovered is not None:
                return recovered
            eligible = self._eligible_journals()
            preview = _archive_preview(eligible)
            if preview["basis_digest"] != expected_basis_digest:
                raise _error(WRITE_CONFLICT, "eligible journal set changed before archival")
            if not eligible:
                return {
                    "status": "success",
                    "interface_version": "1.0",
                    "result": "no_change",
                    "archive_id": None,
                    "archived_journal_count": 0,
                    "persistent_writes": 0,
                    "canonical_scientific_write": False,
                }

            archive_id = validate_id(
                self.archive_id_factory(), Namespace.OPERATIONAL_ARCHIVE
            )
            event_id = validate_id(self.event_id_factory(), Namespace.PROCESS_EVENT)
            target = self.layout.operational_archive_path(archive_id)
            if target.exists():
                raise _error(WRITE_CONFLICT, "operational archive ID already exists")
            ensure_private_directory(self.layout.operational_archives_root)
            stage = self.layout.operational_archives_root / f".{archive_id}.stage"
            if stage.exists():
                raise _error(WRITE_CONFLICT, "operational archive stage already exists")
            ensure_private_directory(stage)
            try:
                journals = [item["journal"] for item in eligible]
                segment = serialize_jsonl(journals)
                segment_sha256 = sha256_bytes(segment)
                created_at = timestamp(self.clock)
                manifest = {
                    "schema_version": "1.0",
                    "archive_id": archive_id,
                    "workspace_id": self.layout.workspace_id,
                    "profile_id": ARCHIVE_PROFILE_ID,
                    "event_id": event_id,
                    "event_ids": [item["journal"]["event_id"] for item in eligible],
                    "journal_count": len(eligible),
                    "segment_path": "journals.jsonl",
                    "segment_sha256": segment_sha256,
                    "basis_digest": preview["basis_digest"],
                    "created_at": created_at,
                }
                _validate("operational-archive-manifest", manifest)
                atomic_write_bytes(stage / "journals.jsonl", segment, archive_id)
                atomic_write_bytes(stage / "manifest.json", serialize_json(manifest), archive_id)
                if self.phase_hook is not None:
                    self.phase_hook("staged")
                os.replace(stage, target)
                if self.phase_hook is not None:
                    self.phase_hook("published")
                self._settle_published_archive(target, manifest, journals)
            finally:
                if stage.exists():
                    shutil.rmtree(stage)
            return {
                "status": "success",
                "interface_version": "1.0",
                "result": "archived",
                "archive_id": archive_id,
                "archived_journal_count": len(eligible),
                "persistent_writes": 1,
                "canonical_scientific_write": False,
            }

    def _recover_matching_archive(self, expected_basis_digest: str) -> dict[str, Any] | None:
        root = self.layout.operational_archives_root
        if not root.exists():
            return None
        matching: list[tuple[Path, dict[str, Any], list[dict[str, Any]], bool]] = []
        unrelated_unsettled = False
        for path in sorted(root.iterdir(), key=lambda item: item.name):
            if not path.is_dir() or path.name.startswith("."):
                continue
            manifest, journals = _read_operational_archive_payload(path)
            settled = (path / "receipt.json").is_file()
            if manifest["basis_digest"] == expected_basis_digest:
                matching.append((path, manifest, journals, settled))
            elif not settled:
                unrelated_unsettled = True
        if not matching:
            if unrelated_unsettled:
                raise _error(WRITE_CONFLICT, "unsettled operational archive does not match the requested basis")
            return None
        if len(matching) != 1:
            raise _error(WRITE_CONFLICT, "multiple operational archives match the requested basis")
        target, manifest, journals, settled = matching[0]
        if settled:
            self._require_settled_archive(target, manifest, journals)
            return {
                "status": "success",
                "interface_version": "1.0",
                "result": "already_settled",
                "archive_id": manifest["archive_id"],
                "archived_journal_count": len(journals),
                "persistent_writes": 0,
                "canonical_scientific_write": False,
            }
        self._settle_published_archive(target, manifest, journals)
        return {
            "status": "success",
            "interface_version": "1.0",
            "result": "recovered",
            "archive_id": manifest["archive_id"],
            "archived_journal_count": len(journals),
            "persistent_writes": 1,
            "canonical_scientific_write": False,
        }

    def _require_settled_archive(
        self,
        target: Path,
        manifest: dict[str, Any],
        journals: list[dict[str, Any]],
    ) -> None:
        read_operational_archive(target)
        if target.name != manifest["archive_id"] or manifest["workspace_id"] != self.layout.workspace_id:
            raise _error(WRITE_CONFLICT, "settled operational archive identity changed")
        event_ids = {journal["event_id"] for journal in journals}
        event_ids.add(manifest["event_id"])
        events = read_process_event_subset(
            self.layout.process_events_path,
            event_ids,
        )
        for journal in journals:
            if self.layout.journal_path(journal["event_id"]).exists():
                raise _error(WRITE_CONFLICT, "settled operational archive still has an active journal")
            if events.get(journal["event_id"]) != expected_journal_event(
                journal,
                journal["result"],
            ):
                raise _error(WRITE_CONFLICT, "settled operational archive lost journal event closure")
        expected_event = build_process_event(
            event_id=manifest["event_id"],
            operation="operational_journal_archive",
            actor="user",
            result="success",
            input_refs=manifest["event_ids"],
            output_refs=[manifest["archive_id"]],
            created_at=manifest["created_at"],
        )
        if events.get(manifest["event_id"]) != expected_event:
            raise _error(WRITE_CONFLICT, "settled operational archive lost settlement event closure")

    def _settle_published_archive(
        self,
        target: Path,
        manifest: dict[str, Any],
        journals: list[dict[str, Any]],
    ) -> None:
        event_ids = {journal["event_id"] for journal in journals}
        event_ids.add(manifest["event_id"])
        events = read_process_event_subset(
            self.layout.process_events_path,
            event_ids,
        )
        for journal in journals:
            if events.get(journal["event_id"]) != expected_journal_event(
                journal,
                journal["result"],
            ):
                raise _error(WRITE_CONFLICT, "archived journal no longer matches its process event")
            active = self.layout.journal_path(journal["event_id"])
            if active.exists():
                if file_sha256(active) != sha256_bytes(serialize_json(journal)):
                    raise _error(WRITE_CONFLICT, "active journal changed after archive publication")
                active.unlink()
        archive_event = build_process_event(
            event_id=manifest["event_id"],
            operation="operational_journal_archive",
            actor="user",
            result="success",
            input_refs=manifest["event_ids"],
            output_refs=[manifest["archive_id"]],
            created_at=manifest["created_at"],
        )
        existing_event = events.get(manifest["event_id"])
        if existing_event is None:
            append_process_event(
                self.layout.process_events_path,
                archive_event,
                write_id=manifest["event_id"],
            )
        elif existing_event != archive_event:
            raise _error(WRITE_CONFLICT, "archive settlement event content changed")
        receipt = {
            "schema_version": "1.0",
            "archive_id": manifest["archive_id"],
            "workspace_id": self.layout.workspace_id,
            "manifest_sha256": file_sha256(target / "manifest.json"),
            "segment_sha256": manifest["segment_sha256"],
            "journal_count": len(journals),
            "event_id": manifest["event_id"],
            "settled_at": timestamp(self.clock),
        }
        _validate("operational-archive-receipt", receipt)
        receipt_path = target / "receipt.json"
        if receipt_path.exists():
            if read_json_document(receipt_path, record_kind="operational-archive-receipt") != receipt:
                raise _error(WRITE_CONFLICT, "archive settlement receipt content changed")
        else:
            atomic_write_bytes(
                receipt_path,
                serialize_json(receipt),
                manifest["archive_id"],
            )
        if self.phase_hook is not None:
            self.phase_hook("settled")

    def _eligible_journals(self) -> list[dict[str, Any]]:
        if not self.layout.transactions_root.exists():
            return []
        candidates: list[dict[str, Any]] = []
        validation = RecordValidationSession("transaction-journal", actor="stored")
        for path in sorted(self.layout.transactions_root.glob("*.json"), key=lambda item: item.name):
            journal = read_json_document(path, record_kind="transaction-journal")
            if validation.validate(journal):
                continue
            if journal["phase"] != "complete" or journal["result"] not in {"success", "failure"}:
                continue
            candidates.append(
                {
                    "path": path,
                    "sha256": file_sha256(path),
                    "journal": journal,
                }
            )
        events = read_process_event_subset(
            self.layout.process_events_path,
            {item["journal"]["event_id"] for item in candidates},
        )
        eligible: list[dict[str, Any]] = []
        for item in candidates:
            journal = item["journal"]
            if events.get(journal["event_id"]) != expected_journal_event(
                journal,
                journal["result"],
            ):
                continue
            eligible.append(item)
        return eligible


class MaintenanceWorkService:
    def __init__(
        self,
        layout: WorkspaceLayout,
        *,
        transaction_manager: TransactionManager | None = None,
        id_factory: MaintenanceIdFactory | None = None,
        clock: Clock = utc_now,
    ):
        self.layout = layout
        self.transactions = transaction_manager or TransactionManager(layout, clock=clock)
        self.id_factory = id_factory or (lambda: allocate_id(Namespace.MAINTENANCE))
        self.clock = clock

    def enqueue(self, triggers: Iterable[Mapping[str, Any]], *, actor: str) -> dict[str, Any]:
        if actor not in {"cli", "user"}:
            raise _error(INVALID_AUTHORITY, "maintenance work actor is invalid")
        normalized = [_normalize_trigger(item) for item in triggers]
        if not normalized:
            raise _error(SCHEMA_VALIDATION_FAILED, "maintenance trigger batch is empty")
        existing = self._read()
        by_key = {
            (item["dependent_id"], item["upstream_revision"], item["reason"]): item
            for item in existing
        }
        created_count = 0
        coalesced_count = 0
        now = timestamp(self.clock)
        for trigger in normalized:
            key = (
                trigger["dependent_id"],
                trigger["upstream_revision"],
                trigger["reason"],
            )
            current = by_key.get(key)
            if current is None:
                maintenance_id = validate_id(self.id_factory(), Namespace.MAINTENANCE)
                current = {
                    "schema_version": "1.0",
                    "maintenance_id": maintenance_id,
                    "workspace_id": self.layout.workspace_id,
                    "dependent_id": trigger["dependent_id"],
                    "upstream_revision": trigger["upstream_revision"],
                    "reason": trigger["reason"],
                    "trigger_refs": [trigger["trigger_ref"]],
                    "status": "open",
                    "created_at": now,
                    "updated_at": now,
                }
                by_key[key] = current
                created_count += 1
            else:
                coalesced_count += 1
                if trigger["trigger_ref"] not in current["trigger_refs"]:
                    current["trigger_refs"] = sorted(
                        [*current["trigger_refs"], trigger["trigger_ref"]]
                    )
                    current["updated_at"] = now
        proposed = sorted(by_key.values(), key=lambda item: item["maintenance_id"])
        validation = RecordValidationSession("maintenance-work", actor="stored")
        for item in proposed:
            diagnostics = validation.validate(item)
            if diagnostics:
                raise ResearchKBError(diagnostics[0])
        before = file_sha256(self.layout.maintenance_work_path)
        content = serialize_jsonl(proposed)
        if before == sha256_bytes(content):
            transaction = None
        else:
            transaction = self.transactions.promote_bytes(
                target=self.layout.maintenance_work_path,
                content=content,
                target_store="maintenance_work",
                operation="maintenance_work_coalesce",
                actor=actor,
                input_refs=sorted({item["trigger_ref"] for item in normalized}),
                output_refs=[item["maintenance_id"] for item in proposed],
                validator=lambda path: _validate_maintenance_file(path),
                expected_before_sha256=before,
            )
        return {
            "status": "success",
            "interface_version": "1.0",
            "result": "committed" if transaction is not None else "no_change",
            "created_count": created_count,
            "coalesced_count": coalesced_count,
            "open_count": len(proposed),
            "persistent_writes": 1 if transaction is not None else 0,
            "canonical_scientific_write": False,
        }

    def list(self, *, page_size: int = 20, cursor: str | None = None) -> dict[str, Any]:
        if not isinstance(page_size, int) or isinstance(page_size, bool) or not 1 <= page_size <= 100:
            raise _error(SCHEMA_VALIDATION_FAILED, "page size must be between 1 and 100")
        if cursor is not None:
            validate_id(cursor, Namespace.MAINTENANCE)
        items = self._read()
        if cursor is not None:
            items = [item for item in items if item["maintenance_id"] > cursor]
        page = items[:page_size]
        return {
            "status": "success",
            "interface_version": "1.0",
            "items": page,
            "next_cursor": page[-1]["maintenance_id"] if len(items) > page_size else None,
            "persistent_writes": 0,
            "canonical_scientific_write": False,
        }

    def _read(self) -> list[dict[str, Any]]:
        items = read_jsonl(
            self.layout.maintenance_work_path,
            record_kind="maintenance-work",
            id_field="maintenance_id",
        )
        validation = RecordValidationSession("maintenance-work", actor="stored")
        for item in items:
            diagnostics = validation.validate(item)
            if diagnostics:
                raise ResearchKBError(diagnostics[0])
        return sorted(items, key=lambda item: item["maintenance_id"])


def read_operational_archive(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    manifest, journals = _read_operational_archive_payload(path)
    receipt = read_json_document(path / "receipt.json", record_kind="operational-archive-receipt")
    _validate("operational-archive-receipt", receipt)
    if file_sha256(path / "manifest.json") != receipt["manifest_sha256"]:
        raise _error(SCHEMA_VALIDATION_FAILED, "operational archive manifest digest changed")
    if receipt["segment_sha256"] != manifest["segment_sha256"]:
        raise _error(SCHEMA_VALIDATION_FAILED, "operational archive receipt does not match segment")
    return manifest, journals, receipt


def _read_operational_archive_payload(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    manifest = read_json_document(path / "manifest.json", record_kind="operational-archive-manifest")
    _validate("operational-archive-manifest", manifest)
    journals = read_jsonl(
        path / manifest["segment_path"],
        record_kind="transaction-journal",
        missing_ok=False,
        id_field="event_id",
    )
    if len(journals) != manifest["journal_count"]:
        raise _error(SCHEMA_VALIDATION_FAILED, "operational archive journal count changed")
    if file_sha256(path / manifest["segment_path"]) != manifest["segment_sha256"]:
        raise _error(SCHEMA_VALIDATION_FAILED, "operational archive segment digest changed")
    if [item["event_id"] for item in journals] != manifest["event_ids"]:
        raise _error(SCHEMA_VALIDATION_FAILED, "operational archive event inventory changed")
    validation = RecordValidationSession("transaction-journal", actor="stored")
    for journal in journals:
        diagnostics = validation.validate(journal)
        if diagnostics:
            raise ResearchKBError(diagnostics[0])
    return manifest, journals


def _archive_preview(eligible: list[dict[str, Any]]) -> dict[str, Any]:
    inventory = [
        {"event_id": item["journal"]["event_id"], "journal_sha256": item["sha256"]}
        for item in eligible
    ]
    return {
        "status": "success",
        "interface_version": "1.0",
        "eligible_journal_count": len(eligible),
        "basis_digest": canonical_digest(inventory),
        "persistent_writes": 0,
        "canonical_scientific_write": False,
    }


def _normalize_trigger(item: Mapping[str, Any]) -> dict[str, str]:
    required = {"dependent_id", "upstream_revision", "reason", "trigger_ref"}
    if not isinstance(item, Mapping) or set(item) != required:
        raise _error(SCHEMA_VALIDATION_FAILED, "maintenance trigger fields do not match the contract")
    values = {key: item[key] for key in required}
    if not all(isinstance(value, str) and value for value in values.values()):
        raise _error(SCHEMA_VALIDATION_FAILED, "maintenance trigger values must be non-empty strings")
    validate_id(values["dependent_id"])
    return values


def _validate_maintenance_file(path: Path) -> None:
    items = read_jsonl(path, record_kind="maintenance-work", missing_ok=False, id_field="maintenance_id")
    validation = RecordValidationSession("maintenance-work", actor="stored")
    for item in items:
        diagnostics = validation.validate(item)
        if diagnostics:
            raise ResearchKBError(diagnostics[0])


def _validate(kind: str, record: dict[str, Any]) -> None:
    diagnostics = validate_record(kind, record, actor="stored")
    if diagnostics:
        raise ResearchKBError(diagnostics[0])


def _error(code: str, message: str) -> ResearchKBError:
    return ResearchKBError(Diagnostic(code, "operational-maintenance", None, "", message))


__all__ = [
    "ARCHIVE_PROFILE_ID",
    "MaintenanceWorkService",
    "OperationalMaintenanceService",
    "read_operational_archive",
]
