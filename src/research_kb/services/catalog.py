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

from filelock import FileLock, Timeout

from research_kb.application import APPLICATION_SERVICE_INTERFACE_VERSION
from research_kb.bundle import BundleEntry, load_workspace_entries, validate_workspace_entries
from research_kb.catalog import CATALOG_CONTRACT_VERSION, CatalogAdapterRegistry, CatalogDatabase, CatalogSnapshot
from research_kb.catalog.models import (
    CatalogSourceLocator,
    CatalogSourceRecord,
    canonical_digest,
)
from research_kb.catalog.storage import CATALOG_PROJECTION_ERROR, CATALOG_SCHEMA_VERSION
from research_kb.contracts.validator import RecordValidationSession, validate_record
from research_kb.contracts.registry import SchemaRegistry
from research_kb.errors import (
    PATH_ESCAPE,
    JSONL_FORMAT_ERROR,
    LOCK_TIMEOUT,
    SCHEMA_VALIDATION_FAILED,
    UNRESOLVED_REFERENCE,
    Diagnostic,
    ResearchKBError,
)
from research_kb.identity_corrections import project_registry_identity
from research_kb.organization_bundles import (
    ORGANIZATION_BUNDLE_SPECS,
    active_organization_record,
    organization_bundle_diagnostics,
)
from research_kb.source_assets import current_source_asset_heads
from research_kb.storage.json_io import (
    atomic_write_bytes,
    ensure_private_directory,
    file_sha256,
    read_json_document,
    read_jsonl,
    serialize_json,
    sha256_bytes,
)
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


@dataclass(frozen=True, slots=True)
class RegistryProjectionInput:
    snapshot: CatalogSnapshot
    locators: tuple[CatalogSourceLocator, ...]
    store_digest: str


@dataclass(frozen=True, slots=True)
class ProjectionSourceBindings:
    locators: tuple[CatalogSourceLocator, ...]
    store_digests: dict[str, str]


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
        self._uses_workspace_loader = entry_loader is load_workspace_entries
        self.paths = _catalog_paths(session, Path(state_root))

    def rebuild(self) -> dict[str, Any]:
        _validate_existing_catalog_paths(self.paths)
        snapshot = self._snapshot()
        registry_input = self._registry_input() if self._uses_workspace_loader else None
        bindings = self._source_bindings(snapshot, registry_input)
        _prepare_managed_root(self.paths)
        temporary = self.paths.workspace_root / f".{DATABASE_FILENAME}.{uuid.uuid4().hex}.tmp"
        try:
            CatalogDatabase.build(
                temporary,
                snapshot,
                build_mode="full",
                source_locators=bindings.locators,
                source_store_digests=bindings.store_digests,
            )
            os.replace(temporary, self.paths.database_path)
        finally:
            temporary.unlink(missing_ok=True)
            temporary.with_name(temporary.name + "-journal").unlink(missing_ok=True)
        return _build_result(snapshot, mode="full")

    def update(self) -> dict[str, Any]:
        _validate_existing_catalog_paths(self.paths)
        inspection = CatalogDatabase.inspect(self.paths.database_path)
        if inspection.state == "missing":
            return self.rebuild()
        if inspection.state == "corrupt":
            raise _projection_error("catalog projection is corrupt and cannot be updated")
        if inspection.metadata.get("workspace_id") != self.session.workspace_id:
            raise _projection_error("catalog projection workspace does not match the active session")
        if inspection.state == "incompatible":
            try:
                stored_schema_version = int(inspection.metadata.get("catalog_schema_version", "-1"))
            except (TypeError, ValueError):
                stored_schema_version = -1
            if (
                inspection.metadata.get("catalog_contract_version") == CATALOG_CONTRACT_VERSION
                and 0 <= stored_schema_version < CATALOG_SCHEMA_VERSION
            ):
                return {**self.rebuild(), "reason": "schema_upgrade"}
            raise _projection_error("catalog projection is not compatible with update")
        if inspection.state != "ready":
            raise _projection_error("catalog projection is not compatible with update")
        snapshot = self._snapshot()
        registry_input = self._registry_input() if self._uses_workspace_loader else None
        bindings = self._source_bindings(snapshot, registry_input)
        _prepare_managed_root(self.paths)
        changes = CatalogDatabase.update(
            self.paths.database_path,
            snapshot,
            source_locators=bindings.locators,
            source_store_digests=bindings.store_digests,
        )
        return {**_build_result(snapshot, mode="incremental"), **changes}

    def inspect_status(self) -> dict[str, Any]:
        _validate_existing_catalog_paths(self.paths)
        inspection = CatalogDatabase.inspect(self.paths.database_path)
        if inspection.state != "ready":
            return {
                "status": "success",
                "projection_state": inspection.state,
                "freshness_verification": "not_applicable",
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
                "freshness_verification": "not_applicable",
                "workspace_id": self.session.workspace_id,
                "item_count": 0,
                "source_watermark": inspection.metadata.get("source_watermark"),
                "current_source_watermark": None,
                "unknown_record_kinds": inspection.metadata.get("unknown_record_kinds", []),
            }
        return {
            "status": "success",
            "projection_state": "stale",
            "freshness_verification": "unverified_after_restart",
            "workspace_id": self.session.workspace_id,
            "item_count": inspection.item_count,
            "source_watermark": inspection.metadata["source_watermark"],
            "current_source_watermark": None,
            "unknown_record_kinds": inspection.metadata.get("unknown_record_kinds", []),
        }

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

    def _registry_input(self) -> RegistryProjectionInput:
        return _load_registry_projection_input(
            self.session._layout.registry_path,
            self.registry,
            workspace_id=self.session.workspace_id,
        )

    def _source_bindings(
        self,
        snapshot: CatalogSnapshot,
        registry_input: RegistryProjectionInput | None,
    ) -> ProjectionSourceBindings:
        if registry_input is None:
            return ProjectionSourceBindings((), {})
        locators = list(registry_input.locators)
        store_digests = {"registry": registry_input.store_digest}
        for path, record_kind, store_key in (
            (self.session._layout.pipeline_jobs_path, "pipeline-job-state", "pipeline_jobs"),
            (self.session._layout.agent_tasks_path, "agent-task-state", "agent_tasks"),
        ):
            selected = {
                item.source_key
                for item in snapshot.source_records
                if item.record_kind == record_kind
            }
            store_locators, store_digest = _load_jsonl_projection_locators(
                path,
                record_kind=record_kind,
                id_field="state_id",
                store_key=store_key,
                selected_source_keys=selected,
            )
            locators.extend(store_locators)
            store_digests[store_key] = store_digest
        return ProjectionSourceBindings(tuple(locators), store_digests)

    def _benchmark_registry_delta(
        self,
        *,
        base_source_watermark: str,
        before_registry_store_digest: str,
        after_registry_store_digest: str,
    ) -> dict[str, Any]:
        if not self._uses_workspace_loader:
            raise _projection_error("Registry benchmark delta requires the workspace loader")
        _validate_existing_catalog_paths(self.paths)
        lock = FileLock(
            self.paths.workspace_root / ".benchmark-registry-delta.lock",
            timeout=30.0,
        )
        try:
            with lock:
                existing_sources = CatalogDatabase.source_index(
                    self.paths.database_path,
                    record_kind="registry-paper",
                )
                registry_input = _load_registry_projection_input(
                    self.session._layout.registry_path,
                    self.registry,
                    workspace_id=self.session.workspace_id,
                    existing_sources=existing_sources,
                )
                if registry_input.store_digest != after_registry_store_digest:
                    raise _projection_error("Registry benchmark digest changed before projection")
                return CatalogDatabase.update_registry_sources(
                    self.paths.database_path,
                    registry_input.snapshot,
                    source_locators=registry_input.locators,
                    registry_store_digest=registry_input.store_digest,
                    base_source_watermark=base_source_watermark,
                    before_registry_store_digest=before_registry_store_digest,
                )
        except Timeout as error:
            raise ResearchKBError(
                Diagnostic(
                    LOCK_TIMEOUT,
                    "catalog-projection",
                    None,
                    "",
                    "Registry benchmark delta lock acquisition timed out",
                )
            ) from error


class CatalogQueryService:
    def __init__(self, projection: CatalogProjectionService):
        self.projection = projection
        self._status: dict[str, Any] | None = None
        self._operational_status: dict[str, Any] | None = None

    def refresh_status(self) -> dict[str, Any]:
        self._status = self.projection.status()
        return dict(self._status)

    def bind_existing_projection(self) -> dict[str, Any]:
        self._status = self.projection.inspect_status()
        self._operational_status = None
        return dict(self._status)

    def bind_projection_result(self, result: dict[str, Any]) -> dict[str, Any]:
        inspection = CatalogDatabase.inspect(self.projection.paths.database_path)
        if (
            result.get("status") != "success"
            or result.get("workspace_id") != self.projection.session.workspace_id
            or inspection.state != "ready"
            or inspection.metadata.get("workspace_id") != result.get("workspace_id")
            or inspection.metadata.get("source_watermark") != result.get("source_watermark")
            or inspection.item_count != result.get("item_count")
            or inspection.metadata.get("unknown_record_kinds", [])
            != result.get("unknown_record_kinds", [])
        ):
            raise _projection_error("catalog projection result does not match stored state")
        self._status = {
            "status": "success",
            "projection_state": "current",
            "workspace_id": self.projection.session.workspace_id,
            "item_count": inspection.item_count,
            "source_watermark": result["source_watermark"],
            "current_source_watermark": result["source_watermark"],
            "unknown_record_kinds": result.get("unknown_record_kinds", []),
        }
        self._operational_status = None
        self.bind_operational_projection()
        return dict(self._status)

    def bind_operational_projection(self) -> dict[str, Any]:
        inspection = CatalogDatabase.inspect(self.projection.paths.database_path)
        expected = {
            "pipeline_jobs": _optional_jsonl_store_digest(
                self.projection.session._layout.pipeline_jobs_path
            ),
            "agent_tasks": _optional_jsonl_store_digest(
                self.projection.session._layout.agent_tasks_path
            ),
        }
        stored = inspection.metadata.get("source_store_digests", {})
        current = (
            inspection.state == "ready"
            and inspection.metadata.get("workspace_id") == self.projection.session.workspace_id
            and inspection.metadata.get("adapter_registry_version")
            == self.projection.registry.registry_version
            and all(stored.get(key) == expected[key] for key in expected)
        )
        self._operational_status = {
            "status": "success",
            "projection_state": "current" if current else inspection.state if inspection.state != "ready" else "stale",
            "projection_scope": "operational_heads",
            "workspace_id": self.projection.session.workspace_id,
            "source_watermark": inspection.metadata.get("source_watermark"),
            "store_digests_verified": current,
        }
        return dict(self._operational_status)

    def mark_stale(self) -> None:
        if self._status is not None:
            self._status = {
                **self._status,
                "projection_state": "stale",
                "freshness_verification": "upstream_write",
                "current_source_watermark": None,
            }
        self._operational_status = None

    def search(
        self,
        *,
        query: str = "",
        item_kinds: Iterable[str] = (),
        paper_id: str | None = None,
        question_id: str | None = None,
        tag_id: str | None = None,
        status_labels: Iterable[str] = (),
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
            paper_id=paper_id,
            question_id=question_id,
            tag_id=tag_id,
            status_labels=tuple(status_labels),
            page_size=page_size,
            cursor=cursor,
        )
        result["projection_state"] = status["projection_state"]
        result["source_watermark"] = status["source_watermark"]
        return result

    def operational_page(
        self,
        *,
        item_kind: str,
        page_size: int = 20,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        if item_kind not in {"pipeline_job", "agent_task"}:
            raise _projection_error("operational page kind is not supported")
        operational_status = self._operational_status or self.bind_operational_projection()
        if operational_status["projection_state"] != "current":
            raise _projection_error("operational projection is not current")
        page = self.search(
            item_kinds=(item_kind,),
            page_size=page_size,
            cursor=cursor,
        )
        item_ids = tuple(item["item_id"] for item in page["items"])
        bindings = CatalogDatabase.detail_bindings(self.projection.paths.database_path, item_ids)
        raw_records = _read_operational_page_records(self.projection.session._layout, bindings)
        records = []
        schema_registry = SchemaRegistry()
        validation_sessions: dict[str, RecordValidationSession] = {}
        for item_id in item_ids:
            binding = bindings.get(item_id)
            record = raw_records.get(item_id)
            if binding is None or record is None:
                raise _projection_error("operational projection record changed before page materialization")
            row = binding["item"]
            if canonical_digest(record) != row["source_record_digest"]:
                raise _projection_error("operational projection record digest changed before page materialization")
            record_kind = row["record_kind"]
            session = validation_sessions.get(record_kind)
            if session is None:
                session = RecordValidationSession(record_kind, registry=schema_registry, actor="stored")
                validation_sessions[record_kind] = session
            diagnostics = session.validate(record)
            if diagnostics:
                raise ResearchKBError(diagnostics[0])
            adapter = self.projection.registry.find_adapter(record_kind)
            if adapter.record_id(record) != row["record_id"]:
                raise _projection_error("operational projection record identity changed before page materialization")
            records.append(adapter.detail(record, row["child_id"]))
        return {
            "status": "success",
            "projection_state": operational_status["projection_state"],
            "source_watermark": page["source_watermark"],
            "item_kind": item_kind,
            "records": records,
            "next_cursor": page["next_cursor"],
            "has_more": page["has_more"],
        }

    def operational_late_cursor(self, *, item_kind: str, page_size: int = 100) -> str | None:
        if item_kind not in {"pipeline_job", "agent_task"}:
            raise _projection_error("operational late cursor kind is not supported")
        operational_status = self._operational_status or self.bind_operational_projection()
        if operational_status["projection_state"] != "current":
            raise _projection_error("operational projection is not current")
        return CatalogDatabase.late_cursor(
            self.projection.paths.database_path,
            item_kind=item_kind,
            page_size=page_size,
        )

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
        binding = CatalogDatabase.detail_binding(self.projection.paths.database_path, item_id)
        if binding is None:
            raise ResearchKBError(
                Diagnostic(
                    UNRESOLVED_REFERENCE,
                    "catalog-item",
                    item_id,
                    "/item_id",
                    "catalog item does not exist",
                )
            )
        row = binding["item"]
        adapter = self.projection.registry.find_adapter(row["record_kind"])
        if self.projection._uses_workspace_loader:
            locator = binding["locator"]
            if locator is not None:
                record = _read_jsonl_record_at_locator(
                    self.projection.session._layout,
                    row,
                    locator,
                )
            else:
                record = _load_exact_workspace_record(self.projection.session._layout, row)
            if record is not None and row["record_kind"] == "screening-decision-bundle":
                entries = self.projection.entry_loader(self.projection.session._layout)
                self.projection.entry_validator(entries)
                record = next(
                    (
                        selected
                        for kind, selected in self.projection.registry.select_entries(entries)
                        if kind == row["record_kind"]
                        and adapter.record_id(selected) == row["record_id"]
                    ),
                    None,
                )
            if record is not None:
                if row["record_kind"] not in {"registry-identity-projection", "screening-decision-bundle"}:
                    diagnostics = validate_record(row["record_kind"], record, actor="stored")
                    if diagnostics:
                        raise ResearchKBError(diagnostics[0])
                if adapter.record_id(record) != row["record_id"]:
                    record = None
        else:
            entries = self.projection.entry_loader(self.projection.session._layout)
            self.projection.entry_validator(entries)
            record = next(
                (
                    record
                    for kind, record in self.projection.registry.select_entries(entries)
                    if kind == row["record_kind"]
                    and adapter.record_id(record) == row["record_id"]
                ),
                None,
            )
        current_digest = None if record is None else canonical_digest(record)
        detail = None
        current_record_status = (
            "changed"
            if (
                record is None
                and row["record_kind"] == "registry-paper"
                and binding["locator"] is not None
                and self.projection.session._layout.registry_path.is_file()
            )
            else "missing"
        )
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
            "query_filters": ["item_kinds", "paper_id", "question_id", "tag_id", "status_labels"],
            **self.registry.capability(record_kinds),
        }


def _load_exact_workspace_record(layout, row: dict[str, Any]) -> dict[str, Any] | None:
    kind = row["record_kind"]
    paper_id = row["paper_id"]
    if kind == "registry-paper":
        return _find_jsonl_record(layout.registry_path, "paper_id", row["record_id"])
    if kind == "paper-card" and paper_id is not None:
        return _read_bound_json(layout.paper_card_path(paper_id), "paper_id", paper_id)
    if kind == "evidence" and paper_id is not None:
        return _find_jsonl_record(layout.evidence_path(paper_id), "evidence_id", row["record_id"])
    if kind == "review-memory" and paper_id is not None:
        return _read_bound_json(
            layout.review_memory_path(paper_id),
            "review_memory_id",
            row["record_id"],
        )
    if kind == "question-mapping":
        successor = layout.question_revision_bundle_path(row["record_id"])
        if successor.is_file():
            return _read_organization_child(successor, "question-revision-bundle")
        return _find_jsonl_record(layout.question_mappings_path, "question_id", row["record_id"])
    if kind == "direction":
        bundle = _read_bound_json(
            layout.direction_bundle_path(row["record_id"]),
            "direction_id",
            row["record_id"],
        )
        return None if bundle is None else _validated_organization_child(bundle, "direction-bundle")
    if kind == "field-map-entry":
        bundle = _read_bound_json(
            layout.field_map_bundle_path(row["record_id"]),
            "field_map_entry_id",
            row["record_id"],
        )
        return None if bundle is None else _validated_organization_child(bundle, "field-map-bundle")
    if kind == "tag-bundle":
        return _read_bound_json(layout.tag_bundle_path(row["record_id"]), "tag_id", row["record_id"])
    if kind == "tag-link-bundle":
        return _read_bound_json(layout.tag_link_bundle_path(row["record_id"]), "tag_link_id", row["record_id"])
    if kind == "screening-criteria-bundle":
        return _read_bound_json(layout.screening_criteria_bundle_path(row["record_id"]), "criteria_id", row["record_id"])
    if kind == "screening-decision-bundle":
        return _read_bound_json(layout.screening_decision_bundle_path(row["record_id"]), "decision_id", row["record_id"])
    if kind in {
        "step7-synthesis",
        "step7-review-angle",
        "step7-insight",
        "step7-cross-view",
    }:
        return _find_jsonl_record(layout.step7_store_path(kind), "candidate_id", row["record_id"])
    if kind == "process-event":
        return _find_jsonl_record(layout.process_events_path, "event_id", row["record_id"])
    if kind == "pipeline-job-state":
        return _find_jsonl_record(layout.pipeline_jobs_path, "state_id", row["record_id"])
    if kind == "agent-task-state":
        return _find_jsonl_record(layout.agent_tasks_path, "state_id", row["record_id"])
    if kind == "source-adequacy-profile":
        return _find_jsonl_record(
            layout.source_adequacy_path,
            "profile_id",
            row["record_id"],
        )
    if kind == "source-asset-state":
        states = read_jsonl(
            layout.source_assets_path,
            record_kind="source-asset-state",
            id_field="source_asset_state_id",
        )
        return next(
            (
                state
                for state in current_source_asset_heads(states)
                if state["source_asset_id"] == row["record_id"]
            ),
            None,
        )
    if kind == "registry-identity-projection":
        papers = read_jsonl(
            layout.registry_path,
            record_kind="registry-paper",
            id_field="paper_id",
        )
        corrections = read_jsonl(
            layout.identity_corrections_path,
            record_kind="registry-identity-correction",
            id_field="correction_id",
        )
        projected = project_registry_identity(papers, corrections).get(row["record_id"])
        return None if projected is None else {"schema_version": "1.0", **projected}
    if kind == "guardian-report":
        return _find_jsonl_record(
            layout.guardian_reports_path,
            "guardian_report_id",
            row["record_id"],
        )
    return None


def _load_registry_projection_input(
    path: Path,
    registry: CatalogAdapterRegistry,
    *,
    workspace_id: str,
    existing_sources: dict[str, tuple[str, str]] | None = None,
) -> RegistryProjectionInput:
    try:
        content = path.read_bytes() if path.exists() else b""
    except OSError as error:
        raise _jsonl_error(path, f"cannot read Registry store: {error}") from error
    if content.startswith(b"\xef\xbb\xbf"):
        raise _jsonl_error(path, "UTF-8 BOM is not permitted")
    if b"\r" in content:
        raise _jsonl_error(path, "canonical structured files must use LF line endings")
    if content and not content.endswith(b"\n"):
        raise _jsonl_error(path, "JSONL must end with LF")

    entries: list[tuple[str, dict[str, Any]]] = []
    source_records: list[CatalogSourceRecord] = []
    documents = []
    offsets: dict[str, tuple[int, int]] = {}
    seen: set[str] = set()
    validation = RecordValidationSession(
        "registry-paper",
        registry=SchemaRegistry(),
        actor="stored",
    )
    adapter = registry.find_adapter("registry-paper")
    offset = 0
    for line_number, raw_line in enumerate(content.splitlines(keepends=True), start=1):
        if raw_line == b"\n":
            raise _jsonl_error(path, f"blank JSONL line at {line_number}")
        try:
            value = json.loads(raw_line[:-1].decode("utf-8", errors="strict"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise _jsonl_error(path, f"invalid JSONL at line {line_number}") from error
        if not isinstance(value, dict):
            raise _jsonl_error(path, f"JSONL line {line_number} must be an object")
        paper_id = value.get("paper_id")
        if not isinstance(paper_id, str) or not paper_id:
            raise _jsonl_error(path, f"JSONL line {line_number} lacks paper_id")
        if paper_id in seen:
            raise _jsonl_error(path, f"duplicate paper_id at line {line_number}")
        seen.add(paper_id)
        source_key = f"registry-paper:{paper_id}"
        digest = canonical_digest(value)
        current_identity = (digest, adapter.adapter_version)
        changed = existing_sources is None or existing_sources.get(source_key) != current_identity
        if changed:
            diagnostics = validation.validate(value)
            if diagnostics:
                raise ResearchKBError(diagnostics[0])
        if existing_sources is None:
            entries.append(("registry-paper", value))
        else:
            source_records.append(
                CatalogSourceRecord(
                    source_key,
                    "registry-paper",
                    paper_id,
                    digest,
                    adapter.adapter_version,
                )
            )
            if changed:
                documents.extend(adapter.project(value, workspace_id, digest))
        offsets[source_key] = (offset, len(raw_line))
        offset += len(raw_line)

    if existing_sources is None:
        snapshot = registry.project_entries(entries, workspace_id=workspace_id)
    else:
        source_records.sort(key=lambda item: item.source_key)
        documents.sort(key=lambda item: (item.sort_key, item.item_kind, item.item_id))
        snapshot = CatalogSnapshot(
            workspace_id,
            registry.registry_version,
            "",
            tuple(source_records),
            tuple(documents),
            (),
        )
    locators = tuple(
        CatalogSourceLocator(source.source_key, "registry", *offsets[source.source_key])
        for source in snapshot.source_records
    )
    return RegistryProjectionInput(snapshot, locators, sha256_bytes(content))


def _load_jsonl_projection_locators(
    path: Path,
    *,
    record_kind: str,
    id_field: str,
    store_key: str,
    selected_source_keys: set[str],
) -> tuple[tuple[CatalogSourceLocator, ...], str]:
    try:
        content = path.read_bytes() if path.exists() else b""
    except OSError as error:
        raise _jsonl_error(path, f"cannot read {store_key} store: {error}", record_kind=record_kind) from error
    if content.startswith(b"\xef\xbb\xbf"):
        raise _jsonl_error(path, "UTF-8 BOM is not permitted", record_kind=record_kind)
    if b"\r" in content:
        raise _jsonl_error(path, "canonical structured files must use LF line endings", record_kind=record_kind)
    if content and not content.endswith(b"\n"):
        raise _jsonl_error(path, "JSONL must end with LF", record_kind=record_kind)

    locators: list[CatalogSourceLocator] = []
    seen: set[str] = set()
    offset = 0
    for line_number, raw_line in enumerate(content.splitlines(keepends=True), start=1):
        if raw_line == b"\n":
            raise _jsonl_error(path, f"blank JSONL line at {line_number}", record_kind=record_kind)
        try:
            value = json.loads(raw_line[:-1].decode("utf-8", errors="strict"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise _jsonl_error(path, f"invalid JSONL at line {line_number}", record_kind=record_kind) from error
        record_id = value.get(id_field) if isinstance(value, dict) else None
        if not isinstance(record_id, str) or not record_id:
            raise _jsonl_error(path, f"JSONL line {line_number} lacks {id_field}", record_kind=record_kind)
        source_key = f"{record_kind}:{record_id}"
        if source_key in seen:
            raise _jsonl_error(path, f"duplicate {id_field} at line {line_number}", record_kind=record_kind)
        seen.add(source_key)
        if source_key in selected_source_keys:
            locators.append(CatalogSourceLocator(source_key, store_key, offset, len(raw_line)))
        offset += len(raw_line)
    if {item.source_key for item in locators} != selected_source_keys:
        raise _jsonl_error(path, "projected source locator set is incomplete", record_kind=record_kind)
    return tuple(locators), sha256_bytes(content)


def _optional_jsonl_store_digest(path: Path) -> str:
    if not path.exists():
        return sha256_bytes(b"")
    digest = file_sha256(path)
    if digest is None:
        raise _projection_error("operational projection store is not a regular file")
    return digest


def _read_jsonl_record_at_locator(
    layout,
    row: dict[str, Any],
    locator: dict[str, Any],
) -> dict[str, Any] | None:
    stores = {
        "registry": (layout.registry_path, "registry-paper", "paper_id"),
        "pipeline_jobs": (layout.pipeline_jobs_path, "pipeline-job-state", "state_id"),
        "agent_tasks": (layout.agent_tasks_path, "agent-task-state", "state_id"),
    }
    binding = stores.get(locator.get("store_key"))
    if binding is None:
        return None
    path, record_kind, id_field = binding
    if row.get("record_kind") != record_kind:
        return None
    offset = locator.get("byte_offset")
    length = locator.get("byte_length")
    if not isinstance(offset, int) or offset < 0 or not isinstance(length, int) or length < 2:
        return None
    try:
        with path.open("rb") as handle:
            handle.seek(offset)
            raw_line = handle.read(length)
    except OSError:
        return None
    if len(raw_line) != length or not raw_line.endswith(b"\n") or b"\r" in raw_line:
        return None
    try:
        record = json.loads(raw_line[:-1].decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(record, dict) or record.get(id_field) != row["record_id"]:
        return None
    return record


def _read_operational_page_records(
    layout,
    bindings: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    stores = {
        "pipeline_jobs": (layout.pipeline_jobs_path, "pipeline-job-state", "state_id"),
        "agent_tasks": (layout.agent_tasks_path, "agent-task-state", "state_id"),
    }
    grouped: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for item_id, binding in bindings.items():
        locator = binding.get("locator")
        store_key = None if locator is None else locator.get("store_key")
        if store_key not in stores:
            raise _projection_error("operational projection locator is unavailable")
        grouped.setdefault(store_key, []).append((item_id, binding))

    records: dict[str, dict[str, Any]] = {}
    for store_key, selected in grouped.items():
        path, record_kind, id_field = stores[store_key]
        try:
            with path.open("rb") as handle:
                for item_id, binding in selected:
                    row = binding["item"]
                    locator = binding["locator"]
                    offset = locator.get("byte_offset")
                    length = locator.get("byte_length")
                    if (
                        row.get("record_kind") != record_kind
                        or not isinstance(offset, int)
                        or offset < 0
                        or not isinstance(length, int)
                        or length < 2
                    ):
                        raise _projection_error("operational projection locator is invalid")
                    handle.seek(offset)
                    raw_line = handle.read(length)
                    if len(raw_line) != length or not raw_line.endswith(b"\n") or b"\r" in raw_line:
                        raise _projection_error("operational projection locator no longer resolves")
                    try:
                        record = json.loads(raw_line[:-1].decode("utf-8", errors="strict"))
                    except (UnicodeDecodeError, json.JSONDecodeError) as error:
                        raise _projection_error("operational projection locator no longer contains JSON") from error
                    if not isinstance(record, dict) or record.get(id_field) != row["record_id"]:
                        raise _projection_error("operational projection locator identity changed")
                    records[item_id] = record
        except OSError as error:
            raise _projection_error("operational projection store cannot be read") from error
    return records


def _jsonl_error(path: Path, message: str, *, record_kind: str = "registry-paper") -> ResearchKBError:
    return ResearchKBError(
        Diagnostic(JSONL_FORMAT_ERROR, record_kind, None, "", f"{message}: {path.name}")
    )


def _read_bound_json(path: Path, id_field: str, expected_id: str) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    record = read_json_document(path, record_kind="catalog-detail")
    return record if record.get(id_field) == expected_id else None


def _read_organization_child(path: Path, kind: str) -> dict[str, Any] | None:
    bundle = read_json_document(path, record_kind=kind)
    return _validated_organization_child(bundle, kind)


def _validated_organization_child(bundle: dict[str, Any], kind: str) -> dict[str, Any] | None:
    diagnostics = validate_record(kind, bundle, actor="stored")
    target_field, child_field, _ = ORGANIZATION_BUNDLE_SPECS[kind]
    diagnostics.extend(
        organization_bundle_diagnostics(
            bundle,
            bundle_kind=kind,
            target_id_field=target_field,
            child_field=child_field,
        )
    )
    if diagnostics:
        raise ResearchKBError(diagnostics[0])
    return active_organization_record(bundle, child_field=child_field)


def _find_jsonl_record(path: Path, id_field: str, expected_id: str) -> dict[str, Any] | None:
    return next(
        (
            record
            for record in read_jsonl(path, record_kind="catalog-detail", id_field=id_field)
            if record.get(id_field) == expected_id
        ),
        None,
    )


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
