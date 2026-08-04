from __future__ import annotations

import os
import re
import shutil
import stat
import uuid
from collections.abc import Mapping
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable

from research_kb.bundle import load_workspace_entries, validate_workspace_entries
from research_kb.catalog.models import canonical_digest
from research_kb.errors import (
    INVALID_AUTHORITY,
    PATH_ESCAPE,
    SCHEMA_VALIDATION_FAILED,
    SNAPSHOT_MISMATCH,
    Diagnostic,
    ResearchKBError,
)
from research_kb.obsidian_views import (
    MANIFEST_CONTRACT,
    OPTIONAL_TABLES,
    RENDERER_VERSION,
    ViewDraft,
    project_obsidian_views,
)
from research_kb.process_events import timestamp, utc_now
from research_kb.storage.json_io import (
    atomic_write_bytes,
    ensure_private_directory,
    file_sha256,
    read_json_document,
    serialize_json,
    sha256_bytes,
)
from research_kb.storage.locking import workspace_lock
from research_kb.workspace import WorkspaceLayout


OBSIDIAN_VIEW_INVALID = "RKBC-038"
OBSIDIAN_MANAGED_EDIT = "RKBC-039"
MAX_PREVIEW_PATHS = 200
GENERATION_PATTERN = re.compile(r"^gen-[0-9a-f]{64}$")
DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")
STABLE_ID_SUFFIX = r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
LOGICAL_VIEW_PATTERNS = {
    "Papers": re.compile(rf"^paper_{STABLE_ID_SUFFIX}\.md$"),
    "Reviews": re.compile(rf"^paper_{STABLE_ID_SUFFIX}\.md$"),
    "Directions": re.compile(rf"^direction_{STABLE_ID_SUFFIX}\.md$"),
    "Questions": re.compile(rf"^question_{STABLE_ID_SUFFIX}\.md$"),
    "Research Synthesis": re.compile(rf"^question_{STABLE_ID_SUFFIX}\.md$"),
}
MANIFEST_KEYS = {
    "contract_version",
    "workspace_id",
    "renderer_version",
    "generation_id",
    "optional_tables",
    "source_watermark",
    "rendered_at",
    "files",
    "manifest_payload_digest",
}
ENTRY_KEYS = {
    "logical_path",
    "view_kind",
    "view_id",
    "render_version",
    "dependencies",
    "source_watermark",
    "content_digest",
    "byte_count",
    "rendered_at",
}
DEPENDENCY_KEYS = {"record_kind", "record_id", "record_digest", "revision_id"}
Clock = Callable[[], object]


class ObsidianGeneratedViewsService:
    def __init__(self, layout: WorkspaceLayout, *, clock: Clock = utc_now):
        self.layout = layout
        self.clock = clock

    def status(self) -> dict[str, Any]:
        active = self._inspect_active()
        if active is None:
            return {
                "projection_state": "missing",
                "integrity_state": "intact",
                "generation_id": None,
                "manifest_digest": None,
                "source_watermark": None,
                "optional_tables": [],
                "entries": [],
                "file_count": 0,
                "current_count": 0,
                "stale_count": 0,
                "edited_paths": [],
                "edited_paths_truncated": False,
            }
        drafts = self._drafts(active.manifest["optional_tables"])
        current = {item.logical_path: item for item in drafts}
        saved = {item["logical_path"]: item for item in active.manifest["files"]}
        projected: list[dict[str, Any]] = []
        for logical_path in sorted(set(saved) | set(current)):
            old = saved.get(logical_path)
            draft = current.get(logical_path)
            reasons: list[str] = []
            if old is None:
                reasons.append("view_missing")
            elif draft is None:
                reasons.append("source_removed")
            elif old["source_watermark"] != draft.source_watermark:
                reasons.append("source_dependency_changed")
            freshness = "current" if not reasons else "stale_upstream"
            projected.append(
                {
                    "logical_path": logical_path,
                    "view_kind": (old or {"view_kind": draft.view_kind})["view_kind"],
                    "view_id": (old or {"view_id": draft.view_id})["view_id"],
                    "freshness": freshness,
                    "freshness_reasons": reasons,
                    "source_watermark": (
                        old["source_watermark"] if old is not None else draft.source_watermark
                    ),
                    "content_digest": None if old is None else old["content_digest"],
                    "byte_count": None if old is None else old["byte_count"],
                    "rendered_at": None if old is None else old["rendered_at"],
                }
            )
        edited = sorted(active.edited_paths)
        return {
            "projection_state": "ready",
            "integrity_state": "intact" if not edited else "edited_managed_file",
            "generation_id": active.manifest["generation_id"],
            "manifest_digest": active.manifest["manifest_payload_digest"],
            "source_watermark": active.manifest["source_watermark"],
            "optional_tables": list(active.manifest["optional_tables"]),
            "entries": projected,
            "file_count": len(projected),
            "current_count": sum(item["freshness"] == "current" for item in projected),
            "stale_count": sum(item["freshness"] != "current" for item in projected),
            "edited_paths": edited[:MAX_PREVIEW_PATHS],
            "edited_paths_truncated": len(edited) > MAX_PREVIEW_PATHS,
        }

    def preview_render(self, *, optional_tables: Iterable[str] = ()) -> dict[str, Any]:
        return self._preview(_normalize_tables(optional_tables))

    def render(self, request: Mapping[str, Any], *, actor: str) -> dict[str, Any]:
        normalized = _normalize_render_request(request)
        if actor not in {"cli", "user"}:
            raise ResearchKBError(
                Diagnostic(
                    INVALID_AUTHORITY,
                    "obsidian-generated-view-render",
                    None,
                    "/actor",
                    "Obsidian generated-view render requires CLI or user authority",
                )
            )
        if normalized["discard_managed_edits"] and actor != "user":
            raise ResearchKBError(
                Diagnostic(
                    INVALID_AUTHORITY,
                    "obsidian-generated-view-render",
                    None,
                    "/discard_managed_edits",
                    "discarding managed-view edits requires explicit user authority",
                )
            )
        with workspace_lock(self.layout.lock_path):
            preview = self._preview(normalized["optional_tables"])
            if normalized["expected_state"] != preview["expected_state"]:
                raise ResearchKBError(
                    Diagnostic(
                        SNAPSHOT_MISMATCH,
                        "obsidian-generated-view-render",
                        None,
                        "/expected_state",
                        "Obsidian render inputs changed after preview",
                    )
                )
            if preview["integrity_state"] != "intact" and not normalized["discard_managed_edits"]:
                raise ResearchKBError(
                    Diagnostic(
                        OBSIDIAN_MANAGED_EDIT,
                        "obsidian-generated-view",
                        None,
                        "",
                        "edited managed generated files must be discarded explicitly before render",
                    )
                )
            if (
                preview["changed_file_count"] == 0
                and preview["removed_file_count"] == 0
                and preview["integrity_state"] == "intact"
            ):
                return {
                    "result": "no_change",
                    "generation_id": preview["generation_id"],
                    "manifest_digest": preview["current_manifest_digest"],
                    "source_watermark": preview["source_watermark"],
                    "file_count": preview["proposed_file_count"],
                    "changed_file_count": 0,
                    "removed_file_count": 0,
                }
            active = self._inspect_active()
            drafts = self._drafts(normalized["optional_tables"])
            manifest, files = self._materialize_manifest(
                drafts,
                active=active,
                optional_tables=normalized["optional_tables"],
                force_rebuild=bool(
                    normalized["discard_managed_edits"]
                    and active is not None
                    and active.edited_paths
                ),
            )
            self._activate_generation(manifest, files)
            return {
                "result": "committed",
                "generation_id": manifest["generation_id"],
                "manifest_digest": manifest["manifest_payload_digest"],
                "source_watermark": manifest["source_watermark"],
                "file_count": len(manifest["files"]),
                "changed_file_count": preview["changed_file_count"],
                "removed_file_count": preview["removed_file_count"],
            }

    def _preview(self, optional_tables: tuple[str, ...]) -> dict[str, Any]:
        active = self._inspect_active()
        drafts = self._drafts(optional_tables)
        current = {item.logical_path: item for item in drafts}
        source_watermark = _global_source_watermark(drafts, optional_tables)
        if active is None:
            saved: dict[str, dict[str, Any]] = {}
            edited: list[str] = []
        else:
            saved = {item["logical_path"]: item for item in active.manifest["files"]}
            edited = sorted(active.edited_paths)
        changed = sorted(
            path
            for path, draft in current.items()
            if path not in saved
            or saved[path]["source_watermark"] != draft.source_watermark
            or path in edited
        )
        removed = sorted(set(saved) - set(current))
        integrity_digest = canonical_digest(
            {
                "edited_paths": edited,
                "actual_digests": {} if active is None else active.actual_digests,
            }
        )
        expected_state = {
            "source_watermark": source_watermark,
            "manifest_digest": None if active is None else active.manifest["manifest_payload_digest"],
            "integrity_digest": integrity_digest,
        }
        return {
            "projection_state": "missing" if active is None else "ready",
            "integrity_state": "intact" if not edited else "edited_managed_file",
            "generation_id": None if active is None else active.manifest["generation_id"],
            "current_manifest_digest": (
                None if active is None else active.manifest["manifest_payload_digest"]
            ),
            "source_watermark": source_watermark,
            "optional_tables": list(optional_tables),
            "proposed_file_count": len(drafts),
            "changed_file_count": len(changed),
            "removed_file_count": len(removed),
            "changed_paths": changed[:MAX_PREVIEW_PATHS],
            "changed_paths_truncated": len(changed) > MAX_PREVIEW_PATHS,
            "removed_paths": removed[:MAX_PREVIEW_PATHS],
            "removed_paths_truncated": len(removed) > MAX_PREVIEW_PATHS,
            "edited_paths": edited[:MAX_PREVIEW_PATHS],
            "edited_paths_truncated": len(edited) > MAX_PREVIEW_PATHS,
            "expected_state": expected_state,
        }

    def _drafts(self, optional_tables: Iterable[str]) -> tuple[ViewDraft, ...]:
        entries = load_workspace_entries(self.layout)
        validate_workspace_entries(entries)
        try:
            return project_obsidian_views(entries, optional_tables=optional_tables)
        except (KeyError, TypeError, ValueError) as error:
            raise _view_error("structured records cannot be projected into Obsidian views") from error

    def _inspect_active(self) -> "_ActiveProjection | None":
        root = _safe_managed_target(self.layout, self.layout.obsidian_views_root)
        manifest_path = _safe_managed_target(self.layout, self.layout.obsidian_manifest_path)
        if not manifest_path.exists():
            if root.exists():
                unexpected = [
                    item
                    for item in root.iterdir()
                    if item.name != "generations"
                ]
                if unexpected:
                    raise _view_error("managed Obsidian root has content without an active manifest")
            return None
        if not manifest_path.is_file() or _is_unsafe_link(manifest_path):
            raise _view_error("managed Obsidian manifest is not a safe regular file")
        manifest = read_json_document(manifest_path, record_kind="obsidian-generated-view-manifest")
        _validate_manifest(manifest, workspace_id=self.layout.workspace_id)
        generation = _safe_managed_target(
            self.layout,
            self.layout.obsidian_generation_path(manifest["generation_id"]),
        )
        if not generation.is_dir() or _is_unsafe_link(generation):
            raise _view_error("active Obsidian generation is missing or unsafe")
        expected = {item["logical_path"]: item for item in manifest["files"]}
        actual_files: dict[str, Path] = {}
        for path in generation.rglob("*"):
            _require_safe_components(path)
            if path.is_dir():
                continue
            logical = path.relative_to(generation).as_posix()
            actual_files[logical] = path
        actual_digests = {
            logical: file_sha256(path)
            for logical, path in sorted(actual_files.items())
        }
        edited = set(actual_files) - set(expected)
        edited.update(set(expected) - set(actual_files))
        edited.update(
            logical
            for logical in set(actual_files) & set(expected)
            if actual_digests[logical] != expected[logical]["content_digest"]
        )
        return _ActiveProjection(manifest, generation, tuple(sorted(edited)), actual_digests)

    def _materialize_manifest(
        self,
        drafts: tuple[ViewDraft, ...],
        *,
        active: "_ActiveProjection | None",
        optional_tables: tuple[str, ...],
        force_rebuild: bool,
    ) -> tuple[dict[str, Any], dict[str, bytes]]:
        rendered_at = _render_timestamp(self.clock, active if force_rebuild else None)
        old_entries = (
            {} if active is None else {item["logical_path"]: item for item in active.manifest["files"]}
        )
        edited = set() if active is None else set(active.edited_paths)
        files: dict[str, bytes] = {}
        entries: list[dict[str, Any]] = []
        for draft in drafts:
            old = old_entries.get(draft.logical_path)
            if (
                active is not None
                and not force_rebuild
                and old is not None
                and old["source_watermark"] == draft.source_watermark
                and old["render_version"] == RENDERER_VERSION
                and draft.logical_path not in edited
            ):
                content = (active.generation / _logical_path(draft.logical_path)).read_bytes()
                entry = dict(old)
            else:
                content = draft.render(rendered_at)
                entry = {
                    "logical_path": draft.logical_path,
                    "view_kind": draft.view_kind,
                    "view_id": draft.view_id,
                    "render_version": RENDERER_VERSION,
                    "dependencies": [item.to_dict() for item in draft.dependencies],
                    "source_watermark": draft.source_watermark,
                    "content_digest": sha256_bytes(content),
                    "byte_count": len(content),
                    "rendered_at": rendered_at,
                }
            files[draft.logical_path] = content
            entries.append(entry)
        generation_id = "gen-" + canonical_digest(
            {
                "renderer_version": RENDERER_VERSION,
                "optional_tables": list(optional_tables),
                "files": [
                    {
                        "logical_path": item["logical_path"],
                        "content_digest": item["content_digest"],
                    }
                    for item in entries
                ],
            }
        )
        payload = {
            "contract_version": MANIFEST_CONTRACT,
            "workspace_id": self.layout.workspace_id,
            "renderer_version": RENDERER_VERSION,
            "generation_id": generation_id,
            "optional_tables": list(optional_tables),
            "source_watermark": _global_source_watermark(drafts, optional_tables),
            "rendered_at": rendered_at,
            "files": entries,
        }
        manifest = {**payload, "manifest_payload_digest": canonical_digest(payload)}
        _validate_manifest(manifest, workspace_id=self.layout.workspace_id)
        return manifest, files

    def _activate_generation(self, manifest: dict[str, Any], files: dict[str, bytes]) -> None:
        root = _safe_managed_target(self.layout, self.layout.obsidian_views_root)
        generations = _safe_managed_target(self.layout, self.layout.obsidian_generations_root)
        ensure_private_directory(generations)
        staging = generations / f".staging-{uuid.uuid4().hex}"
        final = _safe_managed_target(
            self.layout,
            self.layout.obsidian_generation_path(manifest["generation_id"]),
        )
        ensure_private_directory(staging)
        try:
            for logical_path, content in sorted(files.items()):
                target = staging / _logical_path(logical_path)
                atomic_write_bytes(target, content, uuid.uuid4().hex)
            _require_exact_tree(staging, files)
            if final.exists():
                _require_exact_tree(final, files)
                _remove_owned_tree(staging, generations)
            else:
                os.replace(staging, final)
            _require_exact_tree(final, files)
            atomic_write_bytes(
                self.layout.obsidian_manifest_path,
                serialize_json(manifest),
                uuid.uuid4().hex,
            )
        finally:
            if staging.exists():
                _remove_owned_tree(staging, generations)


class _ActiveProjection:
    def __init__(
        self,
        manifest: dict[str, Any],
        generation: Path,
        edited_paths: tuple[str, ...],
        actual_digests: dict[str, str | None],
    ):
        self.manifest = manifest
        self.generation = generation
        self.edited_paths = edited_paths
        self.actual_digests = actual_digests


def _normalize_tables(values: Iterable[str]) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise _request_error("/optional_tables", "optional_tables must be an array")
    selected = tuple(sorted(set(values)))
    if any(not isinstance(value, str) or value not in OPTIONAL_TABLES for value in selected):
        raise _request_error("/optional_tables", "optional_tables contains an unsupported table")
    return selected


def _normalize_render_request(request: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(request, Mapping) or set(request) != {
        "optional_tables",
        "expected_state",
        "discard_managed_edits",
    }:
        raise _request_error("", "render request has missing or unexpected fields")
    expected = request["expected_state"]
    if not isinstance(expected, Mapping) or set(expected) != {
        "source_watermark",
        "manifest_digest",
        "integrity_digest",
    }:
        raise _request_error("/expected_state", "expected_state is invalid")
    if not all(value is None or isinstance(value, str) for value in expected.values()):
        raise _request_error("/expected_state", "expected_state values are invalid")
    if type(request["discard_managed_edits"]) is not bool:
        raise _request_error("/discard_managed_edits", "discard_managed_edits must be boolean")
    return {
        "optional_tables": _normalize_tables(request["optional_tables"]),
        "expected_state": dict(expected),
        "discard_managed_edits": request["discard_managed_edits"],
    }


def _validate_manifest(manifest: dict[str, Any], *, workspace_id: str) -> None:
    if set(manifest) != MANIFEST_KEYS:
        raise _view_error("managed Obsidian manifest has missing or unexpected fields")
    if manifest["contract_version"] != MANIFEST_CONTRACT:
        raise _view_error("managed Obsidian manifest contract is incompatible")
    if manifest["workspace_id"] != workspace_id:
        raise _view_error("managed Obsidian manifest belongs to another workspace")
    if manifest["renderer_version"] != RENDERER_VERSION:
        raise _view_error("managed Obsidian renderer version is incompatible")
    if not isinstance(manifest["generation_id"], str) or not GENERATION_PATTERN.fullmatch(
        manifest["generation_id"]
    ):
        raise _view_error("managed Obsidian generation ID is invalid")
    tables = _normalize_tables(manifest["optional_tables"])
    if list(tables) != manifest["optional_tables"]:
        raise _view_error("managed Obsidian optional tables are not canonical")
    _parse_utc_timestamp(manifest["rendered_at"], "managed Obsidian render time is invalid")
    if not isinstance(manifest["source_watermark"], str) or not DIGEST_PATTERN.fullmatch(
        manifest["source_watermark"]
    ):
        raise _view_error("managed Obsidian source watermark is invalid")
    if not isinstance(manifest["files"], list) or not manifest["files"]:
        raise _view_error("managed Obsidian manifest has no files")
    paths: list[str] = []
    for entry in manifest["files"]:
        if not isinstance(entry, dict) or set(entry) != ENTRY_KEYS:
            raise _view_error("managed Obsidian file entry is invalid")
        _logical_path(entry["logical_path"])
        paths.append(entry["logical_path"])
        if not all(isinstance(entry[key], str) and entry[key] for key in ("view_kind", "view_id", "source_watermark", "content_digest", "rendered_at")):
            raise _view_error("managed Obsidian file entry text field is invalid")
        if not DIGEST_PATTERN.fullmatch(entry["source_watermark"]) or not DIGEST_PATTERN.fullmatch(
            entry["content_digest"]
        ):
            raise _view_error("managed Obsidian file entry digest is invalid")
        if entry["render_version"] != RENDERER_VERSION:
            raise _view_error("managed Obsidian file render version is incompatible")
        _parse_utc_timestamp(entry["rendered_at"], "managed Obsidian file render time is invalid")
        if type(entry["byte_count"]) is not int or entry["byte_count"] < 0:
            raise _view_error("managed Obsidian file byte count is invalid")
        dependencies = entry["dependencies"]
        if not isinstance(dependencies, list):
            raise _view_error("managed Obsidian dependencies are invalid")
        dependency_keys: list[tuple[str, str]] = []
        for dependency in dependencies:
            if not isinstance(dependency, dict) or set(dependency) != DEPENDENCY_KEYS:
                raise _view_error("managed Obsidian dependency entry is invalid")
            if not all(
                isinstance(dependency[key], str) and dependency[key]
                for key in ("record_kind", "record_id", "record_digest")
            ) or not (dependency["revision_id"] is None or isinstance(dependency["revision_id"], str)):
                raise _view_error("managed Obsidian dependency value is invalid")
            if not DIGEST_PATTERN.fullmatch(dependency["record_digest"]):
                raise _view_error("managed Obsidian dependency digest is invalid")
            dependency_keys.append((dependency["record_kind"], dependency["record_id"]))
        if dependency_keys != sorted(set(dependency_keys)):
            raise _view_error("managed Obsidian dependencies are not canonical")
        expected_watermark = canonical_digest(
            {
                "renderer_version": entry["render_version"],
                "dependencies": dependencies,
                "render_options": list(tables) if entry["view_kind"] == "home" else [],
            }
        )
        if entry["source_watermark"] != expected_watermark:
            raise _view_error("managed Obsidian file source watermark is invalid")
    if paths != sorted(set(paths)):
        raise _view_error("managed Obsidian logical paths are not canonical")
    expected_generation = "gen-" + canonical_digest(
        {
            "renderer_version": RENDERER_VERSION,
            "optional_tables": list(tables),
            "files": [
                {
                    "logical_path": item["logical_path"],
                    "content_digest": item["content_digest"],
                }
                for item in manifest["files"]
            ],
        }
    )
    if manifest["generation_id"] != expected_generation:
        raise _view_error("managed Obsidian generation digest is invalid")
    expected_source = canonical_digest(
        {
            "renderer_version": RENDERER_VERSION,
            "optional_tables": list(tables),
            "views": [
                {
                    "logical_path": item["logical_path"],
                    "source_watermark": item["source_watermark"],
                }
                for item in manifest["files"]
            ],
        }
    )
    if manifest["source_watermark"] != expected_source:
        raise _view_error("managed Obsidian global source watermark is invalid")
    payload = {key: value for key, value in manifest.items() if key != "manifest_payload_digest"}
    if manifest["manifest_payload_digest"] != canonical_digest(payload):
        raise _view_error("managed Obsidian manifest payload digest is invalid")


def _global_source_watermark(
    drafts: Iterable[ViewDraft], optional_tables: Iterable[str]
) -> str:
    return canonical_digest(
        {
            "renderer_version": RENDERER_VERSION,
            "optional_tables": list(optional_tables),
            "views": [
                {
                    "logical_path": item.logical_path,
                    "source_watermark": item.source_watermark,
                }
                for item in drafts
            ],
        }
    )


def _logical_path(value: object) -> Path:
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise _view_error("managed Obsidian logical path is invalid")
    logical = PurePosixPath(value)
    if logical.is_absolute() or any(part in {"", ".", ".."} for part in logical.parts):
        raise _view_error("managed Obsidian logical path escapes its generation")
    if logical.suffix != ".md":
        raise _view_error("managed Obsidian logical path is not Markdown")
    if logical.parts == ("Home.md",):
        return Path("Home.md")
    if len(logical.parts) != 2:
        raise _view_error("managed Obsidian logical path is outside the allowlist")
    folder, filename = logical.parts
    if folder == "Tables":
        if filename not in {f"{table}.md" for table in OPTIONAL_TABLES}:
            raise _view_error("managed Obsidian table path is outside the allowlist")
        return Path(*logical.parts)
    pattern = LOGICAL_VIEW_PATTERNS.get(folder)
    if pattern is None or (filename != "_index.md" and not pattern.fullmatch(filename)):
        raise _view_error("managed Obsidian logical path is outside the allowlist")
    return Path(*logical.parts)


def _require_exact_tree(root: Path, files: Mapping[str, bytes]) -> None:
    actual: dict[str, Path] = {}
    for path in root.rglob("*"):
        _require_safe_components(path)
        if path.is_dir():
            continue
        actual[path.relative_to(root).as_posix()] = path
    if set(actual) != set(files):
        raise _view_error("generated Obsidian generation file set is incomplete")
    if any(file_sha256(actual[key]) != sha256_bytes(content) for key, content in files.items()):
        raise _view_error("generated Obsidian generation content digest is invalid")


def _remove_owned_tree(path: Path, parent: Path) -> None:
    resolved = path.resolve()
    parent_resolved = parent.resolve()
    if resolved.parent != parent_resolved or not path.name.startswith(".staging-"):
        raise _view_error("refusing to remove a non-owned generated staging directory")
    _require_safe_components(path)
    shutil.rmtree(path)


def _require_safe_components(path: Path) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        if os.path.lexists(current) and _is_unsafe_link(current):
            raise ResearchKBError(
                Diagnostic(
                    PATH_ESCAPE,
                    "obsidian-generated-view",
                    None,
                    "",
                    "managed Obsidian path traverses an unsafe filesystem link",
                )
            )


def _safe_managed_target(layout: WorkspaceLayout, path: Path) -> Path:
    _require_safe_components(path)
    resolved = layout.ensure_writable_target(path)
    _require_safe_components(resolved)
    return resolved


def _render_timestamp(clock: Clock, active: _ActiveProjection | None) -> str:
    candidate = timestamp(clock)
    candidate_time = _parse_utc_timestamp(candidate, "managed Obsidian render clock is invalid")
    if active is not None:
        previous = _parse_utc_timestamp(
            active.manifest["rendered_at"],
            "managed Obsidian render time is invalid",
        )
        if candidate_time <= previous:
            candidate_time = previous + timedelta(microseconds=1)
            candidate = candidate_time.isoformat().replace("+00:00", "Z")
    return candidate


def _parse_utc_timestamp(value: object, message: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise _view_error(message)
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise _view_error(message) from error
    if parsed.utcoffset() != timedelta(0):
        raise _view_error(message)
    return parsed


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


def _view_error(message: str) -> ResearchKBError:
    return ResearchKBError(
        Diagnostic(OBSIDIAN_VIEW_INVALID, "obsidian-generated-view", None, "", message)
    )


def _request_error(path: str, message: str) -> ResearchKBError:
    return ResearchKBError(
        Diagnostic(
            SCHEMA_VALIDATION_FAILED,
            "obsidian-generated-view-request",
            None,
            path,
            message,
        )
    )


__all__ = [
    "MAX_PREVIEW_PATHS",
    "OBSIDIAN_MANAGED_EDIT",
    "OBSIDIAN_VIEW_INVALID",
    "ObsidianGeneratedViewsService",
]
