from __future__ import annotations

import json
import os
import re
import sqlite3
import stat
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from research_kb.application import APPLICATION_SERVICE_INTERFACE_VERSION
from research_kb.bundle import BundleEntry, load_workspace_entries, validate_workspace_entries
from research_kb.catalog import CATALOG_CONTRACT_VERSION, CatalogAdapterRegistry, CatalogDatabase, CatalogSnapshot
from research_kb.catalog.models import canonical_digest
from research_kb.catalog.storage import CATALOG_PROJECTION_ERROR
from research_kb.errors import (
    PATH_ESCAPE,
    SCHEMA_VALIDATION_FAILED,
    UNRESOLVED_REFERENCE,
    Diagnostic,
    ResearchKBError,
)
from research_kb.storage.json_io import atomic_write_bytes, ensure_private_directory, read_json_document, serialize_json
from research_kb.services.workspace_session import WorkspaceSession


CATALOG_ITEM_PATTERN = re.compile(r"^catalog_[0-9a-f]{32}$")
MANAGED_DIRECTORY = "research-kb-catalog"
MANAGED_MARKER = ".research-kb-catalog.json"
DATABASE_FILENAME = "catalog-v1.sqlite3"
EntryLoader = Callable[[Any], list[BundleEntry]]
EntryValidator = Callable[[list[BundleEntry]], None]


@dataclass(frozen=True, slots=True)
class CatalogPaths:
    state_root: Path
    managed_root: Path
    workspace_root: Path
    marker_path: Path
    database_path: Path


class CatalogProjectionService:
    def __init__(
        self,
        session: WorkspaceSession,
        state_root: Path,
        *,
        registry: CatalogAdapterRegistry | None = None,
        entry_loader: EntryLoader = load_workspace_entries,
        entry_validator: EntryValidator = validate_workspace_entries,
    ):
        self.session = session
        self.registry = registry or CatalogAdapterRegistry()
        self.entry_loader = entry_loader
        self.entry_validator = entry_validator
        self.paths = _catalog_paths(session, Path(state_root))

    def rebuild(self) -> dict[str, Any]:
        _validate_existing_catalog_paths(self.paths)
        snapshot = self._snapshot()
        _prepare_managed_root(self.paths)
        temporary = self.paths.workspace_root / f".{DATABASE_FILENAME}.{uuid.uuid4().hex}.tmp"
        try:
            CatalogDatabase.build(temporary, snapshot, build_mode="full")
            os.replace(temporary, self.paths.database_path)
        finally:
            temporary.unlink(missing_ok=True)
            temporary.with_name(temporary.name + "-journal").unlink(missing_ok=True)
        return _build_result(snapshot, mode="full")

    def update(self) -> dict[str, Any]:
        _validate_existing_catalog_paths(self.paths)
        if not self.paths.database_path.is_file():
            return self.rebuild()
        snapshot = self._snapshot()
        _prepare_managed_root(self.paths)
        changes = CatalogDatabase.update(self.paths.database_path, snapshot)
        return {**_build_result(snapshot, mode="incremental"), **changes}

    def status(self) -> dict[str, Any]:
        _validate_existing_catalog_paths(self.paths)
        inspection = CatalogDatabase.inspect(self.paths.database_path)
        if inspection.state != "ready":
            return {
                "status": "success",
                "projection_state": inspection.state,
                "workspace_id": self.session.workspace_id,
                "item_count": inspection.item_count,
                "source_watermark": inspection.metadata.get("source_watermark"),
                "current_source_watermark": None,
                "unknown_record_kinds": inspection.metadata.get("unknown_record_kinds", []),
            }
        if inspection.metadata.get("workspace_id") != self.session.workspace_id:
            return {
                "status": "success",
                "projection_state": "incompatible",
                "workspace_id": self.session.workspace_id,
                "item_count": 0,
                "source_watermark": inspection.metadata.get("source_watermark"),
                "current_source_watermark": None,
                "unknown_record_kinds": inspection.metadata.get("unknown_record_kinds", []),
            }
        snapshot = self._snapshot()
        stored = inspection.metadata["source_watermark"]
        return {
            "status": "success",
            "projection_state": "current" if stored == snapshot.source_watermark else "stale",
            "workspace_id": self.session.workspace_id,
            "item_count": inspection.item_count,
            "source_watermark": stored,
            "current_source_watermark": snapshot.source_watermark,
            "unknown_record_kinds": inspection.metadata.get("unknown_record_kinds", []),
        }

    def _snapshot(self) -> CatalogSnapshot:
        entries = self.entry_loader(self.session._layout)
        self.entry_validator(entries)
        workspace_records = [record for kind, record in entries if kind == "workspace"]
        if len(workspace_records) != 1 or workspace_records[0]["workspace"]["id"] != self.session.workspace_id:
            raise _projection_error("catalog input workspace identity does not match the active session")
        return self.registry.project_entries(entries, workspace_id=self.session.workspace_id)


class CatalogQueryService:
    def __init__(self, projection: CatalogProjectionService):
        self.projection = projection
        self._status: dict[str, Any] | None = None

    def refresh_status(self) -> dict[str, Any]:
        self._status = self.projection.status()
        return dict(self._status)

    def search(
        self,
        *,
        query: str = "",
        item_kinds: Iterable[str] = (),
        page_size: int = 20,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        status = self._status or self.refresh_status()
        _require_queryable(status)
        _validate_existing_catalog_paths(self.projection.paths)
        result = CatalogDatabase.query(
            self.projection.paths.database_path,
            query=query,
            item_kinds=tuple(item_kinds),
            page_size=page_size,
            cursor=cursor,
        )
        result["projection_state"] = status["projection_state"]
        result["source_watermark"] = status["source_watermark"]
        return result

    def detail(self, item_id: str) -> dict[str, Any]:
        if not CATALOG_ITEM_PATTERN.fullmatch(item_id):
            raise ResearchKBError(
                Diagnostic(
                    SCHEMA_VALIDATION_FAILED,
                    "catalog-item",
                    item_id,
                    "/item_id",
                    "catalog item ID is invalid",
                )
            )
        status = self._status or self.refresh_status()
        _require_queryable(status)
        _validate_existing_catalog_paths(self.projection.paths)
        row = CatalogDatabase.detail_row(self.projection.paths.database_path, item_id)
        if row is None:
            raise ResearchKBError(
                Diagnostic(
                    UNRESOLVED_REFERENCE,
                    "catalog-item",
                    item_id,
                    "/item_id",
                    "catalog item does not exist",
                )
            )
        entries = self.projection.entry_loader(self.projection.session._layout)
        self.projection.entry_validator(entries)
        adapter = self.projection.registry.find_adapter(row["record_kind"])
        record = next(
            (
                record
                for kind, record in entries
                if kind == row["record_kind"] and adapter.record_id(record) == row["record_id"]
            ),
            None,
        )
        current_digest = None if record is None else canonical_digest(record)
        detail = None
        current_record_status = "missing"
        if record is not None and current_digest == row["source_record_digest"]:
            try:
                detail = adapter.detail(record, row["child_id"])
                current_record_status = "current"
            except KeyError:
                current_record_status = "changed"
        elif record is not None:
            current_record_status = "changed"
        return {
            "status": "success",
            "projection_state": status["projection_state"],
            "current_record_status": current_record_status,
            "item": row,
            "detail": detail,
        }


class CatalogCapabilityService:
    def __init__(self, registry: CatalogAdapterRegistry | None = None):
        self.registry = registry or CatalogAdapterRegistry()

    def show(self, record_kinds: Iterable[str] = ()) -> dict[str, Any]:
        return {
            "status": "success",
            "application_service_interface_version": APPLICATION_SERVICE_INTERFACE_VERSION,
            "catalog_contract_version": CATALOG_CONTRACT_VERSION,
            "projection_storage": "disposable_sqlite_fts",
            "raw_parsed_text_indexed": False,
            "max_page_size": 100,
            **self.registry.capability(record_kinds),
        }


def _catalog_paths(session: WorkspaceSession, state_root: Path) -> CatalogPaths:
    if not state_root.is_absolute():
        raise _path_error("catalog state root must be absolute")
    if _has_unsafe_component(state_root):
        raise _path_error("catalog state root traverses a symlink, junction or reparse point")
    resolved = state_root.resolve()
    protected = [
        session._layout.config.path.parent,
        session._layout.knowledge_root,
        session._layout.local_inbox,
        *session._layout.source_roots.values(),
    ]
    if any(_paths_overlap(resolved, path) for path in protected):
        raise _path_error("catalog state root overlaps a workspace or source root")
    managed_root = resolved / MANAGED_DIRECTORY
    workspace_root = managed_root / session.workspace_id
    return CatalogPaths(
        resolved,
        managed_root,
        workspace_root,
        managed_root / MANAGED_MARKER,
        workspace_root / DATABASE_FILENAME,
    )


def _prepare_managed_root(paths: CatalogPaths) -> None:
    _validate_existing_catalog_paths(paths)
    if paths.state_root.exists() and not paths.state_root.is_dir():
        raise _path_error("catalog state root is not a directory")
    ensure_private_directory(paths.state_root)
    if not paths.managed_root.exists():
        ensure_private_directory(paths.managed_root)
        atomic_write_bytes(
            paths.marker_path,
            serialize_json(
                {
                    "owner": "research-kb-core",
                    "catalog_contract_version": CATALOG_CONTRACT_VERSION,
                }
            ),
            uuid.uuid4().hex,
        )
    ensure_private_directory(paths.workspace_root)
    _validate_existing_catalog_paths(paths)


def _validate_existing_catalog_paths(paths: CatalogPaths) -> None:
    checks = (
        (paths.state_root, "catalog state root", "directory"),
        (paths.managed_root, "managed catalog root", "directory"),
        (paths.workspace_root, "workspace catalog root", "directory"),
        (paths.marker_path, "catalog marker", "file"),
        (paths.database_path, "catalog database", "file"),
    )
    for path, label, expected in checks:
        if not os.path.lexists(path):
            continue
        if _is_unsafe_link(path):
            raise _path_error(f"{label} is a symlink, junction or reparse point")
        if expected == "directory" and not path.is_dir():
            raise _path_error(f"{label} is not a directory")
        if expected == "file" and not path.is_file():
            raise _path_error(f"{label} is not a regular file")
    if paths.managed_root.exists():
        if not paths.marker_path.is_file():
            raise _projection_error("existing catalog directory is not marker-owned")
        marker = read_json_document(paths.marker_path, record_kind="catalog-marker")
        if marker != {
            "owner": "research-kb-core",
            "catalog_contract_version": CATALOG_CONTRACT_VERSION,
        }:
            raise _projection_error("catalog marker is incompatible")


def _build_result(snapshot: CatalogSnapshot, *, mode: str) -> dict[str, Any]:
    return {
        "status": "success",
        "build_mode": mode,
        "workspace_id": snapshot.workspace_id,
        "source_watermark": snapshot.source_watermark,
        "source_record_count": len(snapshot.source_records),
        "item_count": len(snapshot.documents),
        "unknown_record_kinds": list(snapshot.unknown_record_kinds),
    }


def _require_queryable(status: dict[str, Any]) -> None:
    if status["projection_state"] not in {"current", "stale"}:
        raise _projection_error("catalog projection is not queryable")


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


def _paths_overlap(left: Path, right: Path) -> bool:
    left_resolved = left.resolve()
    right_resolved = right.resolve()
    return (
        left_resolved == right_resolved
        or left_resolved.is_relative_to(right_resolved)
        or right_resolved.is_relative_to(left_resolved)
    )


def _projection_error(message: str) -> ResearchKBError:
    return ResearchKBError(
        Diagnostic(CATALOG_PROJECTION_ERROR, "catalog-projection", None, "", message)
    )


def _path_error(message: str) -> ResearchKBError:
    return ResearchKBError(
        Diagnostic(PATH_ESCAPE, "catalog-projection", None, "/state_root", message)
    )


__all__ = [
    "CatalogCapabilityService",
    "CatalogProjectionService",
    "CatalogQueryService",
]
