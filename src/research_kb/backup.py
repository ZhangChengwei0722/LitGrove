from __future__ import annotations

import json
import os
import shutil
import stat
import zipfile
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from research_kb.bundle import load_workspace_entries, validate_workspace_entries
from research_kb.config.loader import load_config
from research_kb.contracts.validator import validate_record
from research_kb.errors import (
    INCOMPLETE_TRANSACTION,
    INPUT_TOO_LARGE,
    INVALID_AUTHORITY,
    PATH_ESCAPE,
    PROTECTED_INPUT_CHANGED,
    SCHEMA_VALIDATION_FAILED,
    WRITE_CONFLICT,
    Diagnostic,
    ResearchKBError,
)
from research_kb.guardian import GuardianService
from research_kb.identifiers import Namespace, allocate_id, validate_id
from research_kb.process_events import Clock, read_process_events, timestamp, utc_now
from research_kb.source_assets import current_source_asset_heads
from research_kb.storage.json_io import (
    atomic_write_bytes,
    file_sha256,
    read_json_document,
    serialize_json,
    sha256_bytes,
)
from research_kb.storage.locking import workspace_lock
from research_kb.storage.transactions import TransactionManager
from research_kb.workspace import WorkspaceLayout
from research_kb.workspace_validation import MANAGED_DIRECTORIES, build_workspace_marker


BACKUP_FORMAT = "research-kb-backup@1.0"
BACKUP_INTERFACE_VERSION = "1.0"
BACKUP_PROFILE_ID = "p11-backup-restore-windows-v1"
BACKUP_RIGHTS_ASSERTION = "user_asserts_backup_authorized"


@dataclass(frozen=True, slots=True)
class BackupReaderProfile:
    profile_id: str = BACKUP_PROFILE_ID
    max_archive_bytes: int = 16 * 1024**3
    max_entries: int = 300_000
    max_entry_uncompressed_bytes: int = 1024**3
    max_total_uncompressed_bytes: int = 16 * 1024**3
    max_path_bytes: int = 512
    max_manifest_bytes: int = 64 * 1024**2


class BackupArchiveReader:
    def __init__(self, profile: BackupReaderProfile | None = None):
        self.profile = profile or BackupReaderProfile()

    def inspect(self, archive_path: Path) -> dict[str, Any]:
        path = _validate_archive_path(Path(archive_path), self.profile)
        archive_sha256 = _hash_file(path)
        entries: list[dict[str, Any]] = []
        structured: dict[str, bytes] = {}
        try:
            with zipfile.ZipFile(path) as archive:
                infos = archive.infolist()
                if not infos or len(infos) > self.profile.max_entries:
                    raise _error(INPUT_TOO_LARGE, "/archive", "backup entry count exceeds the reader profile")
                names = [_validate_archive_name(info.filename, self.profile) for info in infos]
                if names != sorted(names) or len(names) != len(set(names)) or len({name.casefold() for name in names}) != len(names):
                    raise _error(PATH_ESCAPE, "/archive", "backup paths are not canonical and collision-free")
                total = 0
                for info, name in zip(infos, names, strict=True):
                    _validate_zip_info(info, self.profile)
                    total += info.file_size
                    if total > self.profile.max_total_uncompressed_bytes:
                        raise _error(INPUT_TOO_LARGE, f"/{name}", "backup expanded size exceeds the reader profile")
                    digest, content = _stream_zip_entry(archive, info, self.profile)
                    if name == "manifest.json" or name.startswith("config/"):
                        if info.file_size > self.profile.max_manifest_bytes:
                            raise _error(INPUT_TOO_LARGE, f"/{name}", "backup structured control file is too large")
                        structured[name] = content
                    entries.append({"path": name, "sha256": digest, "bytes": info.file_size})
        except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as error:
            raise _error(SCHEMA_VALIDATION_FAILED, "/archive", "backup archive is malformed or unsupported") from error
        manifest_bytes = structured.get("manifest.json")
        manifest = _load_canonical_json(manifest_bytes, "backup-manifest")
        _validate("backup-manifest", manifest)
        if manifest["backup_format"] != BACKUP_FORMAT or manifest["profile_id"] != self.profile.profile_id:
            raise _error(SCHEMA_VALIDATION_FAILED, "/manifest", "backup format or reader profile is unsupported")
        actual_payload = entries[1:] if entries and entries[0]["path"] == "manifest.json" else [
            item for item in entries if item["path"] != "manifest.json"
        ]
        if manifest["entries"] != actual_payload:
            raise _error(PROTECTED_INPUT_CHANGED, "/manifest/entries", "backup payload inventory does not match archive bytes")
        expected_names = {"manifest.json", *(item["path"] for item in manifest["entries"])}
        if expected_names != {item["path"] for item in entries}:
            raise _error(PROTECTED_INPUT_CHANGED, "/manifest/entries", "backup contains unlisted or missing payload")
        controls: dict[str, dict[str, Any]] = {}
        for name in ("config/workspace.json", "config/domain-profile.json"):
            control = _load_canonical_json(structured.get(name), name)
            kind = "workspace" if name.endswith("workspace.json") else "domain-profile"
            diagnostics = validate_record(kind, control, actor="stored")
            if diagnostics:
                raise ResearchKBError(diagnostics[0])
            controls[name] = control
        _validate_backup_control_closure(
            manifest,
            controls["config/workspace.json"],
            actual_payload,
        )
        return {
            "status": "success",
            "interface_version": BACKUP_INTERFACE_VERSION,
            "safe_reader_profile": asdict(self.profile),
            "archive_sha256": archive_sha256,
            "archive_bytes": path.stat().st_size,
            "manifest_sha256": sha256_bytes(manifest_bytes or b""),
            "manifest": manifest,
            "persistent_writes": 0,
            "canonical_scientific_write": False,
        }

    def extract_to(self, archive_path: Path, target: Path) -> dict[str, Any]:
        inspection = self.inspect(archive_path)
        if os.path.lexists(target):
            raise _error(WRITE_CONFLICT, "/stage", "backup extraction stage already exists")
        target.mkdir(mode=0o700, parents=True)
        try:
            expected = {item["path"]: item for item in inspection["manifest"]["entries"]}
            expected["manifest.json"] = {
                "path": "manifest.json",
                "sha256": inspection["manifest_sha256"],
            }
            with zipfile.ZipFile(archive_path) as archive:
                for name in sorted(expected):
                    destination = target.joinpath(*PurePosixPath(name).parts)
                    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                    if not destination.resolve(strict=False).is_relative_to(target.resolve()):
                        raise _error(PATH_ESCAPE, f"/{name}", "backup extraction escaped staging")
                    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    digest = __import__("hashlib").sha256()
                    try:
                        with os.fdopen(descriptor, "wb") as output, archive.open(name) as source:
                            while chunk := source.read(1024 * 1024):
                                digest.update(chunk)
                                output.write(chunk)
                            output.flush()
                            os.fsync(output.fileno())
                    except BaseException:
                        destination.unlink(missing_ok=True)
                        raise
                    if digest.hexdigest() != expected[name]["sha256"]:
                        raise _error(PROTECTED_INPUT_CHANGED, f"/{name}", "backup changed during extraction")
        except BaseException:
            shutil.rmtree(target, ignore_errors=True)
            raise
        return inspection


class BackupService:
    def __init__(
        self,
        layout: WorkspaceLayout,
        *,
        reader: BackupArchiveReader | None = None,
        clock: Clock = utc_now,
        backup_id_factory: Callable[[], str] | None = None,
        lock_timeout: float = 30.0,
        phase_hook: Callable[[str], None] | None = None,
    ):
        self.layout = layout
        self.reader = reader or BackupArchiveReader()
        self.clock = clock
        self.backup_id_factory = backup_id_factory or (lambda: allocate_id(Namespace.BACKUP))
        self.lock_timeout = lock_timeout
        self.phase_hook = phase_hook

    def preview(
        self,
        *,
        include_sources: bool,
        rights_assertion: str | None = None,
    ) -> dict[str, Any]:
        _validate_source_authority(include_sources, rights_assertion)
        with workspace_lock(self.layout.lock_path, timeout=self.lock_timeout):
            plan = self._plan(include_sources=include_sources, rights_assertion=rights_assertion)
        return {
            "status": "success",
            "interface_version": BACKUP_INTERFACE_VERSION,
            "profile_id": BACKUP_PROFILE_ID,
            "backup_id": self.backup_id_factory(),
            **{key: value for key, value in plan.items() if not key.startswith("_")},
            "persistent_writes": 0,
            "canonical_scientific_write": False,
        }

    def create(self, request: Mapping[str, Any], *, target: Path, actor: str) -> dict[str, Any]:
        if actor not in {"cli", "user"}:
            raise _error(INVALID_AUTHORITY, "/actor", "backup creation requires cli or user authority")
        normalized = _validate_create_request(request)
        resolved_target = _validate_create_target(Path(target))
        with workspace_lock(self.layout.lock_path, timeout=self.lock_timeout):
            plan = self._plan(
                include_sources=normalized["include_sources"],
                rights_assertion=normalized.get("rights_assertion"),
            )
            if plan["basis_digest"] != normalized["expected_basis_digest"]:
                raise _error(PROTECTED_INPUT_CHANGED, "/expected_basis_digest", "backup basis changed after preview")
            return self._create_locked(normalized, plan, resolved_target, actor)

    def _create_locked(
        self,
        request: dict[str, Any],
        plan: dict[str, Any],
        target: Path,
        actor: str,
    ) -> dict[str, Any]:
        backup_id = request["backup_id"]
        manifest = {
            "schema_version": "1.0",
            "backup_format": BACKUP_FORMAT,
            "profile_id": BACKUP_PROFILE_ID,
            "backup_id": backup_id,
            "workspace_id": self.layout.workspace_id,
            "source_mode": plan["source_mode"],
            "rights_assertion": plan["rights_assertion"],
            "basis_digest": plan["basis_digest"],
            "entries": plan["entries"],
            "source_inventory": plan["source_inventory"],
            "process_watermark": plan["process_watermark"],
            "created_at": request["created_at"],
        }
        _validate("backup-manifest", manifest)
        manifest_bytes = serialize_json(manifest)
        archive_entries = {"manifest.json": manifest_bytes, **plan["_payload"]}
        receipt_path = self.layout.ensure_writable_target(
            self.layout.backup_receipt_path(backup_id)
        )
        if receipt_path.exists():
            raise _error(WRITE_CONFLICT, "/backup_id", "backup ID is already in use")
        stage = target.parent / f".{target.name}.{backup_id}.tmp"
        if os.path.lexists(stage):
            raise _error(WRITE_CONFLICT, "/target", "operation-owned backup stage already exists")
        published = False
        receipt_created = False
        archive_sha256: str | None = None
        try:
            _write_archive(stage, archive_entries)
            if self.phase_hook is not None:
                self.phase_hook("archive_written")
            inspection = self.reader.inspect(stage)
            if inspection["manifest"] != manifest:
                raise _error(SCHEMA_VALIDATION_FAILED, "/manifest", "written backup failed read-back validation")
            archive_sha256 = inspection["archive_sha256"]
            if os.path.lexists(target):
                raise _error(WRITE_CONFLICT, "/target", "backup target appeared before publication")
            os.replace(stage, target)
            published = True
            if self.phase_hook is not None:
                self.phase_hook("published")
            receipt = {
                "schema_version": "1.0",
                "backup_id": backup_id,
                "workspace_id": self.layout.workspace_id,
                "archive_sha256": archive_sha256,
                "manifest_sha256": sha256_bytes(manifest_bytes),
                "basis_digest": plan["basis_digest"],
                "archive_bytes": target.stat().st_size,
                "source_mode": plan["source_mode"],
                "actor": actor,
                "created_at": request["created_at"],
            }
            _validate("backup-local-receipt", receipt)
            atomic_write_bytes(receipt_path, serialize_json(receipt), backup_id)
            receipt_created = True
        except BaseException:
            if receipt_created and receipt_path.is_file():
                receipt_path.unlink(missing_ok=True)
            if published and archive_sha256 and target.is_file() and _hash_file(target) == archive_sha256:
                target.unlink(missing_ok=True)
            raise
        finally:
            stage.unlink(missing_ok=True)
        return {
            "status": "success",
            "interface_version": BACKUP_INTERFACE_VERSION,
            "result": "created",
            "backup_id": backup_id,
            "source_mode": plan["source_mode"],
            "entry_count": len(plan["entries"]),
            "source_count": len(plan["source_inventory"]),
            "basis_digest": plan["basis_digest"],
            "manifest_sha256": sha256_bytes(manifest_bytes),
            "archive_sha256": archive_sha256,
            "archive_bytes": target.stat().st_size,
            "persistent_writes": 1,
            "canonical_scientific_write": False,
        }

    def _plan(self, *, include_sources: bool, rights_assertion: str | None) -> dict[str, Any]:
        entries = load_workspace_entries(self.layout)
        validate_workspace_entries(entries)
        payload = {
            "config/workspace.json": serialize_json(_portable_workspace_config(self.layout)),
            "config/domain-profile.json": serialize_json(
                load_config(self.layout.domain_profile_path, "domain-profile").data
            ),
        }
        payload.update(_workspace_payload(self.layout))
        source_inventory, source_payload = _source_payload(
            self.layout,
            entries,
            include_sources=include_sources,
        )
        payload.update(source_payload)
        descriptors = [
            {
                "path": path,
                "sha256": sha256_bytes(content),
                "bytes": len(content),
            }
            for path, content in sorted(payload.items())
        ]
        _require_collision_free_paths([item["path"] for item in descriptors])
        process_events = read_process_events(self.layout.process_events_path)
        active_journals = sorted(self.layout.transactions_root.glob("*.json")) if self.layout.transactions_root.exists() else []
        watermark = {
            "event_count": len(process_events),
            "events_sha256": file_sha256(self.layout.process_events_path),
            "last_event_id": process_events[-1]["event_id"] if process_events else None,
            "active_journal_count": len(active_journals),
            "first_journal_event_id": active_journals[0].stem if active_journals else None,
            "last_journal_event_id": active_journals[-1].stem if active_journals else None,
        }
        source_mode = "included" if include_sources else "inventory_only"
        basis = {
            "backup_format": BACKUP_FORMAT,
            "workspace_id": self.layout.workspace_id,
            "source_mode": source_mode,
            "rights_assertion": rights_assertion,
            "entries": descriptors,
            "source_inventory": source_inventory,
            "process_watermark": watermark,
        }
        return {
            "source_mode": source_mode,
            "rights_assertion": rights_assertion,
            "entry_count": len(descriptors),
            "source_count": len(source_inventory),
            "estimated_archive_bytes": sum(item["bytes"] for item in descriptors),
            "entries": descriptors,
            "source_inventory": source_inventory,
            "process_watermark": watermark,
            "basis_digest": sha256_bytes(serialize_json(basis)),
            "_payload": payload,
        }

    @staticmethod
    def restore(
        archive_path: Path,
        request: Mapping[str, Any],
        *,
        target_root: Path,
        actor: str,
        reader: BackupArchiveReader | None = None,
    ) -> dict[str, Any]:
        if actor not in {"cli", "user"}:
            raise _error(INVALID_AUTHORITY, "/actor", "backup restore requires cli or user authority")
        normalized = _validate_restore_request(request)
        target = _validate_restore_target(Path(target_root))
        active_reader = reader or BackupArchiveReader()
        inspection = active_reader.inspect(Path(archive_path))
        if inspection["archive_sha256"] != normalized["expected_archive_sha256"]:
            raise _error(PROTECTED_INPUT_CHANGED, "/expected_archive_sha256", "backup archive changed after inspection")
        restore_id = normalized["restore_id"]
        stage = target.parent / f".{target.name}.{restore_id}.stage"
        if os.path.lexists(stage):
            raise _error(WRITE_CONFLICT, "/target_root", "operation-owned restore stage already exists")
        published = False
        try:
            stage.mkdir(mode=0o700)
            extracted = stage / ".payload"
            extracted_inspection = active_reader.extract_to(Path(archive_path), extracted)
            if (
                extracted_inspection["archive_sha256"] != normalized["expected_archive_sha256"]
                or extracted_inspection["manifest"] != inspection["manifest"]
            ):
                raise _error(PROTECTED_INPUT_CHANGED, "/archive", "backup changed during restore staging")
            inspection = extracted_inspection
            manifest = inspection["manifest"]
            portable_workspace = _load_canonical_json(
                (extracted / "config" / "workspace.json").read_bytes(),
                "workspace",
            )
            domain_profile = _load_canonical_json(
                (extracted / "config" / "domain-profile.json").read_bytes(),
                "domain-profile",
            )
            root_ids = [item["root_id"] for item in portable_workspace["workspace"]["source_roots"]]
            mappings = normalized["source_root_mappings"]
            if manifest["source_mode"] == "included":
                if mappings:
                    raise _error(SCHEMA_VALIDATION_FAILED, "/source_root_mappings", "source-inclusive restore does not accept external mappings")
                source_roots = [
                    {"root_id": root_id, "path": f"./sources/{root_id}", "read_only_assets": True}
                    for root_id in root_ids
                ]
            else:
                if set(mappings) != set(root_ids):
                    raise _error(SCHEMA_VALIDATION_FAILED, "/source_root_mappings", "source-free restore requires one mapping for every source root")
                source_roots = [
                    {"root_id": root_id, "path": mappings[root_id], "read_only_assets": True}
                    for root_id in root_ids
                ]
            restored_config = deepcopy(portable_workspace)
            restored_config["workspace"]["knowledge_root"] = "./knowledge"
            restored_config["workspace"]["domain_profile"] = "./domain-profile.json"
            restored_config["workspace"]["local_inbox"] = "./inbox"
            restored_config["workspace"]["source_roots"] = source_roots
            payload_knowledge = extracted / "knowledge"
            if payload_knowledge.exists():
                os.replace(payload_knowledge, stage / "knowledge")
            else:
                (stage / "knowledge").mkdir(mode=0o700)
            if manifest["source_mode"] == "included":
                payload_sources = extracted / "sources"
                if not payload_sources.is_dir():
                    raise _error(SCHEMA_VALIDATION_FAILED, "/sources", "source-inclusive backup lacks source bytes")
                os.replace(payload_sources, stage / "sources")
            shutil.rmtree(extracted)
            atomic_write_bytes(stage / "workspace.json", serialize_json(restored_config), restore_id)
            atomic_write_bytes(stage / "domain-profile.json", serialize_json(domain_profile), restore_id)
            (stage / "inbox").mkdir(mode=0o700, exist_ok=True)
            config_path = stage / "workspace.json"
            for relative in MANAGED_DIRECTORIES:
                (stage / "knowledge" / Path(*relative.split("/"))).mkdir(
                    mode=0o700,
                    parents=True,
                    exist_ok=True,
                )
            stage_marker_path = stage / "knowledge" / ".research-kb" / "workspace.json"
            stage_marker_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            atomic_write_bytes(
                stage_marker_path,
                serialize_json(build_workspace_marker(config_path)),
                restore_id,
            )
            stage_layout = WorkspaceLayout.load(config_path)
            stage_entries = load_workspace_entries(stage_layout)
            validate_workspace_entries(stage_entries)
            if TransactionManager(stage_layout).recover(dry_run=True):
                raise _error(INCOMPLETE_TRANSACTION, "/restore", "restored workspace has unsettled transactions")
            if GuardianService(stage_layout).check(
                entries=stage_entries,
                entries_validated=True,
            ).report["status"] == "failure":
                raise _error(SCHEMA_VALIDATION_FAILED, "/restore", "restored workspace failed Guardian")
            stage_layout.marker_path.unlink(missing_ok=True)
            if os.path.lexists(target):
                raise _error(WRITE_CONFLICT, "/target_root", "restore target appeared before publication")
            os.replace(stage, target)
            published = True
            final_config = target / "workspace.json"
            final_marker_path = target / "knowledge" / ".research-kb" / "workspace.json"
            atomic_write_bytes(
                final_marker_path,
                serialize_json(build_workspace_marker(final_config)),
                restore_id,
            )
            final_layout = WorkspaceLayout.load(final_config)
            receipt = {
                "schema_version": "1.0",
                "restore_id": restore_id,
                "backup_id": manifest["backup_id"],
                "workspace_id": final_layout.workspace_id,
                "archive_sha256": inspection["archive_sha256"],
                "manifest_sha256": inspection["manifest_sha256"],
                "source_mode": manifest["source_mode"],
                "actor": actor,
                "created_at": normalized["created_at"],
            }
            _validate("restore-receipt", receipt)
            atomic_write_bytes(final_layout.restore_receipt_path(restore_id), serialize_json(receipt), restore_id)
            return {
                "status": "success",
                "interface_version": BACKUP_INTERFACE_VERSION,
                "result": "restored",
                "restore_id": restore_id,
                "backup_id": manifest["backup_id"],
                "workspace_id": final_layout.workspace_id,
                "workspace_config_path": str(final_config),
                "archive_sha256": inspection["archive_sha256"],
                "source_mode": manifest["source_mode"],
                "persistent_writes": 1,
                "canonical_scientific_write": False,
            }
        except BaseException:
            if stage.exists():
                shutil.rmtree(stage, ignore_errors=True)
            if published and target.exists():
                shutil.rmtree(target, ignore_errors=True)
            raise


def _portable_workspace_config(layout: WorkspaceLayout) -> dict[str, Any]:
    config = deepcopy(layout.config.data)
    config["workspace"]["knowledge_root"] = "./knowledge"
    config["workspace"]["domain_profile"] = "./domain-profile.json"
    config["workspace"]["local_inbox"] = "./inbox"
    config["workspace"]["source_roots"] = [
        {"root_id": root_id, "path": f"./sources/{root_id}", "read_only_assets": True}
        for root_id in sorted(layout.source_roots)
    ]
    return config


def _workspace_payload(layout: WorkspaceLayout) -> dict[str, bytes]:
    payload: dict[str, bytes] = {}
    excluded = {
        ".research-kb/workspace.json",
        ".research-kb/locks/workspace.lock",
    }
    for path in sorted(layout.knowledge_root.rglob("*"), key=lambda item: item.as_posix()):
        relative = path.relative_to(layout.knowledge_root).as_posix()
        if relative in excluded or relative.startswith("views/obsidian/"):
            continue
        if _is_unsafe_link(path):
            raise _error(PATH_ESCAPE, f"/knowledge/{relative}", "workspace payload contains an unsafe link")
        if path.is_dir():
            continue
        if not path.is_file():
            raise _error(PATH_ESCAPE, f"/knowledge/{relative}", "workspace payload is not a regular file")
        payload[f"knowledge/{relative}"] = path.read_bytes()
    return payload


def _source_payload(
    layout: WorkspaceLayout,
    entries: list[tuple[str, dict[str, Any]]],
    *,
    include_sources: bool,
) -> tuple[list[dict[str, Any]], dict[str, bytes]]:
    papers = [record for kind, record in entries if kind == "registry-paper"]
    source_states = [record for kind, record in entries if kind == "source-asset-state"]
    heads = current_source_asset_heads(source_states) if source_states else ()
    by_paper: dict[str, list[dict[str, Any]]] = {}
    for head in heads:
        if head.get("paper_id") is not None:
            by_paper.setdefault(head["paper_id"], []).append(head)
    inventory: list[dict[str, Any]] = []
    payload: dict[str, bytes] = {}
    for paper in sorted(papers, key=lambda item: item["paper_id"]):
        assets = sorted(
            by_paper.get(paper["paper_id"], []),
            key=lambda item: (item["asset_role"], item["source_asset_id"]),
        )
        if not any(item["asset_role"] == "main_pdf" for item in assets):
            assets.insert(
                0,
                {
                    "source_asset_id": None,
                    "asset_role": "main_pdf",
                    "source_ref": paper["source_ref"],
                    "source_fingerprint": paper["source_fingerprint"],
                    "manifestation_status": "active",
                    "availability": "available",
                },
            )
        for asset in assets:
            source_ref = asset["source_ref"]
            root_id = source_ref["root_id"]
            relative_path = source_ref["relative_path"]
            archive_path = f"sources/{root_id}/{relative_path}"
            item = {
                "paper_id": paper["paper_id"],
                "source_asset_id": asset.get("source_asset_id"),
                "asset_role": asset["asset_role"],
                "root_id": root_id,
                "relative_path": relative_path,
                "sha256": asset["source_fingerprint"]["value"],
                "availability": asset.get("availability", "unknown"),
                "archive_path": archive_path if include_sources else None,
            }
            inventory.append(item)
            if not include_sources:
                continue
            if asset.get("manifestation_status") != "active" or asset.get("availability") != "available":
                raise _error(PROTECTED_INPUT_CHANGED, "/sources", "source-inclusive backup requires current available sources")
            _, source_path = layout.resolve_source(root_id, relative_path)
            if not source_path.is_file() or _is_unsafe_link(source_path):
                raise _error(PROTECTED_INPUT_CHANGED, "/sources", "source-inclusive backup source is unavailable or unsafe")
            content = source_path.read_bytes()
            if sha256_bytes(content) != item["sha256"]:
                raise _error(PROTECTED_INPUT_CHANGED, "/sources", "source fingerprint changed before backup")
            existing = payload.get(archive_path)
            if existing is not None and existing != content:
                raise _error(WRITE_CONFLICT, "/sources", "source archive path collision has different bytes")
            payload[archive_path] = content
    inventory.sort(key=lambda item: (item["paper_id"], item["asset_role"], item["source_asset_id"] or ""))
    return inventory, payload


def _validate_source_authority(include_sources: bool, rights_assertion: str | None) -> None:
    if not isinstance(include_sources, bool):
        raise _error(SCHEMA_VALIDATION_FAILED, "/include_sources", "include_sources must be boolean")
    if include_sources and rights_assertion != BACKUP_RIGHTS_ASSERTION:
        raise _error(INVALID_AUTHORITY, "/rights_assertion", "source-inclusive backup requires explicit authority")
    if not include_sources and rights_assertion is not None:
        raise _error(SCHEMA_VALIDATION_FAILED, "/rights_assertion", "source-free backup cannot carry a rights assertion")


def _validate_backup_control_closure(
    manifest: dict[str, Any],
    workspace: dict[str, Any],
    payload_entries: list[dict[str, Any]],
) -> None:
    workspace_data = workspace["workspace"]
    if manifest["workspace_id"] != workspace_data["id"]:
        raise _error(PROTECTED_INPUT_CHANGED, "/workspace_id", "backup manifest and workspace identity differ")
    root_ids = {item["root_id"] for item in workspace_data["source_roots"]}
    inventory = manifest["source_inventory"]
    expected_order = sorted(
        inventory,
        key=lambda item: (item["paper_id"], item["asset_role"], item["source_asset_id"] or ""),
    )
    if inventory != expected_order:
        raise _error(SCHEMA_VALIDATION_FAILED, "/source_inventory", "source inventory order is not canonical")
    descriptors = {item["path"]: item for item in payload_entries}
    source_paths = {path for path in descriptors if path.startswith("sources/")}
    expected_source_paths: set[str] = set()
    for index, item in enumerate(inventory):
        if item["root_id"] not in root_ids:
            raise _error(
                PROTECTED_INPUT_CHANGED,
                f"/source_inventory/{index}/root_id",
                "source inventory references an undeclared source root",
            )
        expected_path = f"sources/{item['root_id']}/{item['relative_path']}"
        if manifest["source_mode"] == "inventory_only":
            if item["archive_path"] is not None:
                raise _error(
                    SCHEMA_VALIDATION_FAILED,
                    f"/source_inventory/{index}/archive_path",
                    "inventory-only backup cannot bind source bytes",
                )
            continue
        if item["availability"] != "available" or item["archive_path"] != expected_path:
            raise _error(
                PROTECTED_INPUT_CHANGED,
                f"/source_inventory/{index}/archive_path",
                "source-inclusive inventory is not bound to one current archive path",
            )
        descriptor = descriptors.get(expected_path)
        if descriptor is None or descriptor["sha256"] != item["sha256"]:
            raise _error(
                PROTECTED_INPUT_CHANGED,
                f"/source_inventory/{index}/sha256",
                "source-inclusive inventory does not match archived source bytes",
            )
        expected_source_paths.add(expected_path)
    if source_paths != expected_source_paths:
        raise _error(
            PROTECTED_INPUT_CHANGED,
            "/source_inventory",
            "backup source payload is missing, extra or unbound",
        )


def _validate_create_request(request: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(request, Mapping):
        raise _error(SCHEMA_VALIDATION_FAILED, "", "backup request must be an object")
    include_sources = request.get("include_sources")
    allowed = {"backup_id", "include_sources", "expected_basis_digest", "created_at"}
    if include_sources:
        allowed.add("rights_assertion")
    if set(request) != allowed:
        raise _error(SCHEMA_VALIDATION_FAILED, "", "backup request fields do not match the contract")
    normalized = dict(request)
    normalized["backup_id"] = validate_id(request.get("backup_id"), Namespace.BACKUP)
    _validate_source_authority(include_sources, request.get("rights_assertion"))
    _require_digest(request.get("expected_basis_digest"), "/expected_basis_digest")
    _require_timestamp(request.get("created_at"), "/created_at")
    return normalized


def _validate_restore_request(request: Mapping[str, Any]) -> dict[str, Any]:
    required = {"restore_id", "expected_archive_sha256", "source_root_mappings", "created_at"}
    if not isinstance(request, Mapping) or set(request) != required:
        raise _error(SCHEMA_VALIDATION_FAILED, "", "restore request fields do not match the contract")
    mappings = request.get("source_root_mappings")
    if not isinstance(mappings, Mapping) or not all(
        isinstance(key, str) and key and isinstance(value, str) and value for key, value in mappings.items()
    ):
        raise _error(SCHEMA_VALIDATION_FAILED, "/source_root_mappings", "source root mappings must be string pairs")
    normalized = dict(request)
    normalized["restore_id"] = validate_id(request.get("restore_id"), Namespace.RESTORE)
    normalized["source_root_mappings"] = dict(mappings)
    _require_digest(request.get("expected_archive_sha256"), "/expected_archive_sha256")
    _require_timestamp(request.get("created_at"), "/created_at")
    return normalized


def _validate_create_target(path: Path) -> Path:
    if not path.is_absolute() or not path.name.endswith(".rkb-backup.zip"):
        raise _error(PATH_ESCAPE, "/target", "backup target must be an absolute .rkb-backup.zip path")
    if not path.parent.is_dir() or _has_unsafe_component(path.parent) or os.path.lexists(path):
        raise _error(WRITE_CONFLICT, "/target", "backup target is unavailable, unsafe or already exists")
    return path.resolve(strict=False)


def _validate_restore_target(path: Path) -> Path:
    if not path.is_absolute() or not path.parent.is_dir() or _has_unsafe_component(path.parent):
        raise _error(PATH_ESCAPE, "/target_root", "restore target must have a safe absolute parent")
    if os.path.lexists(path):
        raise _error(WRITE_CONFLICT, "/target_root", "restore target already exists")
    return path.resolve(strict=False)


def _write_archive(path: Path, entries: Mapping[str, bytes]) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(descriptor)
    try:
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
            for name, content in sorted(entries.items()):
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_STORED
                info.create_system = 3
                info.external_attr = (stat.S_IFREG | 0o600) << 16
                info.flag_bits = 0x800
                archive.writestr(info, content)
        with path.open("rb+") as handle:
            os.fsync(handle.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def _validate_archive_path(path: Path, profile: BackupReaderProfile) -> Path:
    if not path.is_absolute() or not path.is_file() or _has_unsafe_component(path):
        raise _error(PATH_ESCAPE, "/archive", "backup archive must be a safe absolute regular file")
    size = path.stat().st_size
    if size <= 0 or size > profile.max_archive_bytes:
        raise _error(INPUT_TOO_LARGE, "/archive", "backup archive size exceeds the reader profile")
    _require_no_zip_trailing_bytes(path)
    return path.resolve()


def _require_no_zip_trailing_bytes(path: Path) -> None:
    size = path.stat().st_size
    tail_size = min(size, 65_557)
    with path.open("rb") as handle:
        handle.seek(size - tail_size)
        tail = handle.read()
    offset = tail.rfind(b"PK\x05\x06")
    if offset < 0 or offset + 22 > len(tail):
        raise _error(SCHEMA_VALIDATION_FAILED, "/archive", "backup lacks a terminal ZIP directory record")
    comment_length = int.from_bytes(tail[offset + 20 : offset + 22], "little")
    if offset + 22 + comment_length != len(tail):
        raise _error(SCHEMA_VALIDATION_FAILED, "/archive", "backup contains trailing or malformed bytes")


def _validate_archive_name(name: str, profile: BackupReaderProfile) -> str:
    if not isinstance(name, str) or not name or len(name.encode("utf-8")) > profile.max_path_bytes:
        raise _error(PATH_ESCAPE, "/archive", "backup path is empty or too long")
    pure = PurePosixPath(name)
    if name != pure.as_posix() or pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise _error(PATH_ESCAPE, f"/{name}", "backup path is not confined POSIX form")
    if any(":" in part or part.endswith((" ", ".")) for part in pure.parts):
        raise _error(PATH_ESCAPE, f"/{name}", "backup path is unsafe on Windows")
    return name


def _validate_zip_info(info: zipfile.ZipInfo, profile: BackupReaderProfile) -> None:
    mode = (info.external_attr >> 16) & 0xFFFF
    if info.is_dir() or info.compress_type != zipfile.ZIP_STORED or (mode and not stat.S_ISREG(mode)):
        raise _error(SCHEMA_VALIDATION_FAILED, f"/{info.filename}", "backup entry type or compression is unsupported")
    if info.file_size > profile.max_entry_uncompressed_bytes:
        raise _error(INPUT_TOO_LARGE, f"/{info.filename}", "backup entry exceeds the reader profile")
    if info.date_time != (1980, 1, 1, 0, 0, 0):
        raise _error(SCHEMA_VALIDATION_FAILED, f"/{info.filename}", "backup entry timestamp is not canonical")


def _stream_zip_entry(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    profile: BackupReaderProfile,
) -> tuple[str, bytes]:
    digest = __import__("hashlib").sha256()
    content = bytearray()
    with archive.open(info) as source:
        while chunk := source.read(1024 * 1024):
            if len(content) + len(chunk) > profile.max_entry_uncompressed_bytes:
                raise _error(INPUT_TOO_LARGE, f"/{info.filename}", "backup entry expanded beyond its declared budget")
            digest.update(chunk)
            if info.filename == "manifest.json" or info.filename.startswith("config/"):
                content.extend(chunk)
    return digest.hexdigest(), bytes(content)


def _load_canonical_json(content: bytes | None, kind: str) -> dict[str, Any]:
    if content is None:
        raise _error(SCHEMA_VALIDATION_FAILED, f"/{kind}", "required backup control record is missing")
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise _error(SCHEMA_VALIDATION_FAILED, f"/{kind}", "backup control record is invalid JSON") from error
    if not isinstance(value, dict) or serialize_json(value) != content:
        raise _error(SCHEMA_VALIDATION_FAILED, f"/{kind}", "backup control record is not canonical JSON")
    return value


def _require_collision_free_paths(paths: list[str]) -> None:
    normalized = [_validate_archive_name(path, BackupReaderProfile()) for path in paths]
    if len(normalized) != len(set(normalized)) or len(normalized) != len({item.casefold() for item in normalized}):
        raise _error(PATH_ESCAPE, "/entries", "backup payload paths collide")


def _require_digest(value: Any, path: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise _error(SCHEMA_VALIDATION_FAILED, path, "digest must be lowercase SHA-256")
    return value


def _require_timestamp(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.endswith("Z") or "T" not in value:
        raise _error(SCHEMA_VALIDATION_FAILED, path, "timestamp must be UTC RFC3339")
    return value


def _hash_file(path: Path) -> str:
    digest = __import__("hashlib").sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _has_unsafe_component(path: Path) -> bool:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        if os.path.lexists(current) and _is_unsafe_link(current):
            return True
    return False


def _is_unsafe_link(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    if callable(is_junction) and is_junction():
        return True
    try:
        attributes = getattr(os.lstat(path), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _validate(kind: str, record: dict[str, Any]) -> None:
    diagnostics = validate_record(kind, record, actor="stored")
    if diagnostics:
        raise ResearchKBError(diagnostics[0])


def _error(code: str, path: str, message: str) -> ResearchKBError:
    return ResearchKBError(Diagnostic(code, "backup", None, path, message))


__all__ = [
    "BACKUP_FORMAT",
    "BACKUP_INTERFACE_VERSION",
    "BACKUP_PROFILE_ID",
    "BACKUP_RIGHTS_ASSERTION",
    "BackupArchiveReader",
    "BackupReaderProfile",
    "BackupService",
]
