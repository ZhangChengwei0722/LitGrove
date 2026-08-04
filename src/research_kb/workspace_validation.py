from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import unicodedata
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from research_kb.config.loader import ConfigDocument, load_config, resolve_config_path
from research_kb.contracts.validator import validate_record
from research_kb.errors import (
    DUPLICATE_ID,
    PATH_ESCAPE,
    SCHEMA_VALIDATION_FAILED,
    UNSAFE_DIRECTORY_MODE,
    WORKSPACE_IDENTITY_CONFLICT,
    WORKSPACE_LAYOUT_CONFLICT,
    WORKSPACE_LAYOUT_UPGRADE_REQUIRED,
    WORKSPACE_NOT_INITIALIZED,
    WORKSPACE_PATH_WARNING,
    Diagnostic,
    ResearchKBError,
)
from research_kb.storage.json_io import read_json_document


PREVIOUS_LAYOUT_CONTRACT_VERSION = "p7c-1"
CURRENT_LAYOUT_CONTRACT_VERSION = "p7d-1"
LAYOUT_CONTRACT_VERSION = CURRENT_LAYOUT_CONTRACT_VERSION
MARKER_RELATIVE_PATH = ".research-kb/workspace.json"
M2A_1_MANAGED_DIRECTORIES = (
    ".research-kb",
    ".research-kb/locks",
    ".research-kb/transactions",
    "registry",
    "parse",
    "parse/by_paper",
    "paper_cards",
    "paper_cards/by_paper",
    "evidence",
    "evidence/by_paper",
    "review_queue",
    "process",
    "guardian",
)
M2B_1_MANAGED_DIRECTORIES = M2A_1_MANAGED_DIRECTORIES + ("questions",)
M3A_2A_MANAGED_DIRECTORIES = M2B_1_MANAGED_DIRECTORIES + (
    "review_memories",
    "review_memories/by_paper",
)
M3B_1_MANAGED_DIRECTORIES = M3A_2A_MANAGED_DIRECTORIES + ("step7",)
M3C_2A_MANAGED_DIRECTORIES = M3B_1_MANAGED_DIRECTORIES + ("discovery",)
P4B_1_MANAGED_DIRECTORIES = M3C_2A_MANAGED_DIRECTORIES + (
    "primary_bundles",
    "primary_bundles/by_paper",
)
P4C_1_MANAGED_DIRECTORIES = P4B_1_MANAGED_DIRECTORIES + (
    "review_bundles",
    "review_bundles/by_paper",
)
P7A_1_MANAGED_DIRECTORIES = P4C_1_MANAGED_DIRECTORIES + (
    "organization",
    "organization/directions",
    "organization/directions/by_id",
    "organization/field_map",
    "organization/field_map/by_id",
    "organization/questions",
    "organization/questions/by_id",
)
P7C_1_MANAGED_DIRECTORIES = P7A_1_MANAGED_DIRECTORIES + (
    "organization/tags",
    "organization/tags/by_id",
    "organization/tag_links",
    "organization/tag_links/by_id",
)
P7D_1_MANAGED_DIRECTORIES = P7C_1_MANAGED_DIRECTORIES + (
    "organization/screening_criteria",
    "organization/screening_criteria/by_id",
    "organization/screening_decisions",
    "organization/screening_decisions/by_id",
)
MANAGED_DIRECTORIES = P7D_1_MANAGED_DIRECTORIES
P9_OPTIONAL_MANAGED_DIRECTORIES = (
    "views",
    "views/obsidian",
    "views/obsidian/generations",
)
P10_OPTIONAL_MANAGED_DIRECTORIES = (
    ".research-kb/exchange-import-transactions",
    ".research-kb/exchange-export-receipts",
    "exchange",
    "exchange/imports",
)
P11_OPTIONAL_MANAGED_DIRECTORIES = (
    ".research-kb/backup-receipts",
    ".research-kb/restore-receipts",
    "process/archives",
    "process/archives/transaction_journals",
)
_OBSIDIAN_GENERATION_NAME = r"(?:gen-[0-9a-f]{64}|\.staging-[0-9a-f]{32})"
_OBSIDIAN_GENERATION_DIRECTORY = re.compile(
    rf"^views/obsidian/generations/{_OBSIDIAN_GENERATION_NAME}$"
)
_OBSIDIAN_GENERATION_CHILD_DIRECTORY = re.compile(
    rf"^views/obsidian/generations/{_OBSIDIAN_GENERATION_NAME}/"
    r"(?:Papers|Reviews|Directions|Questions|Research Synthesis|Tables)$"
)
_OBSIDIAN_GENERATED_FILE = re.compile(
    rf"^views/obsidian/generations/{_OBSIDIAN_GENERATION_NAME}/"
    r"(?:Home\.md|(?:Papers|Reviews|Directions|Questions|Research Synthesis|Tables)/[^/]+\.md)$"
)
_UUID4 = r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
_EXCHANGE_IMPORT_NAME = rf"(?:import_{_UUID4}|\.import_{_UUID4}\.stage)"
_EXCHANGE_IMPORT_DIRECTORY = re.compile(rf"^exchange/imports/{_EXCHANGE_IMPORT_NAME}$")
_EXCHANGE_IMPORT_CHILD_DIRECTORY = re.compile(
    rf"^exchange/imports/{_EXCHANGE_IMPORT_NAME}/(?:records|sources|sources/sha256)$"
)
_EXCHANGE_IMPORT_FILE = re.compile(
    rf"^exchange/imports/{_EXCHANGE_IMPORT_NAME}/(?:"
    r"manifest\.json|receipt\.json|import_receipt\.json|"
    r"records/[a-z][a-z0-9-]{0,63}\.jsonl|sources/index\.jsonl|"
    r"sources/sha256/[0-9a-f]{64}\.pdf)$"
)
_EXCHANGE_IMPORT_JOURNAL = re.compile(
    rf"^\.research-kb/exchange-import-transactions/import_{_UUID4}\.json$"
)
_EXCHANGE_EXPORT_RECEIPT = re.compile(
    rf"^\.research-kb/exchange-export-receipts/export_{_UUID4}\.json$"
)
_BACKUP_RECEIPT = re.compile(rf"^\.research-kb/backup-receipts/backup_{_UUID4}\.json$")
_RESTORE_RECEIPT = re.compile(rf"^\.research-kb/restore-receipts/restore_{_UUID4}\.json$")
_OPERATIONAL_ARCHIVE_DIRECTORY = re.compile(
    rf"^process/archives/transaction_journals/(?:oparchive_{_UUID4}|\.oparchive_{_UUID4}\.stage)$"
)
_OPERATIONAL_ARCHIVE_FILE = re.compile(
    rf"^process/archives/transaction_journals/oparchive_{_UUID4}/(?:manifest\.json|receipt\.json|journals\.jsonl)$"
)


@dataclass(frozen=True, slots=True)
class SourceRootBinding:
    root_id: str
    path: Path
    read_only_assets: bool


@dataclass(frozen=True, slots=True)
class WorkspaceContext:
    config: ConfigDocument
    domain_profile: ConfigDocument
    knowledge_root: Path
    local_inbox: Path
    source_root_items: tuple[SourceRootBinding, ...]
    expected_marker: dict[str, Any]

    @property
    def workspace_id(self) -> str:
        return self.config.data["workspace"]["id"]

    @property
    def domain_profile_id(self) -> str:
        return self.domain_profile.data["domain_profile"]["id"]

    @property
    def source_roots(self) -> dict[str, Path]:
        return {item.root_id: item.path for item in self.source_root_items}

    @property
    def marker_path(self) -> Path:
        return self.knowledge_root / Path(*MARKER_RELATIVE_PATH.split("/"))


@dataclass(frozen=True, slots=True)
class WorkspaceValidation:
    context: WorkspaceContext
    diagnostics: tuple[Diagnostic, ...]

    @property
    def errors(self) -> tuple[Diagnostic, ...]:
        return tuple(item for item in self.diagnostics if item.severity != "warning")

    @property
    def warnings(self) -> tuple[Diagnostic, ...]:
        return tuple(item for item in self.diagnostics if item.severity == "warning")

    def require_valid(self) -> WorkspaceContext:
        if self.errors:
            raise ResearchKBError(self.errors[0])
        return self.context


def validate_initialized_workspace(config_path: Path) -> WorkspaceValidation:
    return _validate_workspace(config_path, require_initialized=True)


def _validate_workspace_for_bootstrap(config_path: Path) -> WorkspaceValidation:
    return _validate_workspace(config_path, require_initialized=False)


def _validate_workspace(config_path: Path, *, require_initialized: bool) -> WorkspaceValidation:
    context = _load_context(config_path)
    try:
        diagnostics = _semantic_diagnostics(context)
        diagnostics.extend(_layout_diagnostics(context, require_initialized=require_initialized))
    except OSError:
        diagnostics = [
            _layout_error(context.workspace_id, "workspace paths could not be inspected safely")
        ]
    return WorkspaceValidation(context, tuple(_deduplicate(diagnostics)))


def build_workspace_marker(config_path: Path) -> dict[str, Any]:
    context = _load_context(config_path)
    diagnostics = _semantic_diagnostics(context)
    errors = [item for item in diagnostics if item.severity != "warning"]
    if errors:
        raise ResearchKBError(errors[0])
    return context.expected_marker


def _load_context(config_path: Path) -> WorkspaceContext:
    config = _load_config_redacted(config_path, "workspace", "/workspace")
    workspace = config.data["workspace"]
    knowledge_root = resolve_config_path(config, workspace["knowledge_root"])
    local_inbox = resolve_config_path(config, workspace["local_inbox"])
    domain_profile_path = resolve_config_path(config, workspace["domain_profile"])
    profile = _load_config_redacted(domain_profile_path, "domain-profile", "/workspace/domain_profile")
    source_items = tuple(
        SourceRootBinding(
            root_id=item["root_id"],
            path=resolve_config_path(config, item["path"]),
            read_only_assets=item["read_only_assets"],
        )
        for item in workspace["source_roots"]
    )
    marker = _marker_for(config, profile, knowledge_root, local_inbox, source_items)
    return WorkspaceContext(config, profile, knowledge_root, local_inbox, source_items, marker)


def _load_config_redacted(path: Path, kind: str, json_path: str) -> ConfigDocument:
    try:
        return load_config(path, kind)
    except ResearchKBError:
        raise
    except (OSError, json.JSONDecodeError, yaml.YAMLError, UnicodeError) as error:
        raise ResearchKBError(
            Diagnostic(SCHEMA_VALIDATION_FAILED, kind, None, json_path, f"{kind} could not be read or parsed")
        ) from error


def _marker_for(
    config: ConfigDocument,
    profile: ConfigDocument,
    knowledge_root: Path,
    local_inbox: Path,
    source_items: tuple[SourceRootBinding, ...],
) -> dict[str, Any]:
    workspace = config.data["workspace"]
    profile_identity = profile.data["domain_profile"]
    projection = {
        "workspace_contract_version": config.data["contract_version"],
        "workspace_id": workspace["id"],
        "knowledge_root": _path_identity(knowledge_root),
        "source_roots": [
            {
                "root_id": item.root_id,
                "path": _path_identity(item.path),
                "read_only_assets": item.read_only_assets,
            }
            for item in sorted(source_items, key=lambda value: (value.root_id, _path_identity(value.path)))
        ],
        "local_inbox": _path_identity(local_inbox),
        "domain_profile_path": _path_identity(profile.path),
        "runtime": config.data["runtime"],
        "domain_profile_id": profile_identity["id"],
        "domain_profile_version": profile_identity["version"],
    }
    canonical = json.dumps(projection, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {
        "schema_version": "1.0",
        "workspace_id": workspace["id"],
        "domain_profile_id": profile_identity["id"],
        "domain_profile_version": profile_identity["version"],
        "layout_contract_version": LAYOUT_CONTRACT_VERSION,
        "config_fingerprint": {"algorithm": "sha256", "value": hashlib.sha256(canonical).hexdigest()},
    }


def _semantic_diagnostics(context: WorkspaceContext) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    workspace_id = context.workspace_id
    root_ids: set[str] = set()
    physical_roots: list[SourceRootBinding] = []
    for index, item in enumerate(context.source_root_items):
        json_path = f"/workspace/source_roots/{index}"
        if item.root_id in root_ids:
            diagnostics.append(
                Diagnostic(DUPLICATE_ID, "workspace", workspace_id, json_path + "/root_id", "duplicate source root ID")
            )
        root_ids.add(item.root_id)
        for existing in physical_roots:
            if existing.root_id != item.root_id and _same_physical_path(existing.path, item.path):
                diagnostics.append(
                    Diagnostic(
                        WORKSPACE_LAYOUT_CONFLICT,
                        "workspace",
                        workspace_id,
                        json_path + "/path",
                        "multiple root IDs resolve to the same source directory",
                    )
                )
        physical_roots.append(item)
        if not item.path.is_dir():
            diagnostics.append(
                Diagnostic(
                    WORKSPACE_LAYOUT_CONFLICT,
                    "workspace",
                    item.root_id,
                    json_path + "/path",
                    "declared source root is missing or is not a directory",
                )
            )
        elif not os.access(item.path, os.R_OK | os.X_OK):
            diagnostics.append(
                Diagnostic(
                    WORKSPACE_LAYOUT_CONFLICT,
                    "workspace",
                    item.root_id,
                    json_path + "/path",
                    "declared source root is not readable",
                )
            )
        if _paths_overlap(context.knowledge_root, item.path):
            diagnostics.append(
                Diagnostic(
                    PATH_ESCAPE,
                    "workspace",
                    workspace_id,
                    json_path + "/path",
                    "knowledge_root and source root must not overlap",
                )
            )

    for left_index, left in enumerate(context.source_root_items):
        for right in context.source_root_items[left_index + 1 :]:
            if not _same_physical_path(left.path, right.path) and _paths_overlap(left.path, right.path):
                diagnostics.append(
                    _warning(
                        workspace_id,
                        "/workspace/source_roots",
                        f"nested source roots declared by root_id {left.root_id} and {right.root_id}",
                    )
                )

    inbox_exists = _lexists(context.local_inbox)
    if inbox_exists and not context.local_inbox.is_dir():
        diagnostics.append(
            Diagnostic(
                WORKSPACE_LAYOUT_CONFLICT,
                "workspace",
                workspace_id,
                "/workspace/local_inbox",
                "local_inbox exists but is not a directory",
            )
        )
    elif not inbox_exists:
        diagnostics.append(_warning(workspace_id, "/workspace/local_inbox", "local_inbox is not present"))

    if _paths_overlap(context.knowledge_root, context.local_inbox):
        diagnostics.append(
            Diagnostic(
                PATH_ESCAPE,
                "workspace",
                workspace_id,
                "/workspace/local_inbox",
                "knowledge_root and local_inbox must not overlap",
            )
        )
    else:
        addressable = [
            item.root_id
            for item in context.source_root_items
            if _is_same_or_descendant(context.local_inbox, item.path)
        ]
        if not addressable:
            diagnostics.append(
                _warning(
                    workspace_id,
                    "/workspace/local_inbox",
                    "local_inbox is not addressable through a declared source root",
                )
            )
        elif len(addressable) > 1:
            diagnostics.append(
                _warning(
                    workspace_id,
                    "/workspace/local_inbox",
                    "local_inbox is addressable through multiple nested source roots",
                )
            )
    return diagnostics


def _layout_diagnostics(context: WorkspaceContext, *, require_initialized: bool) -> list[Diagnostic]:
    root = context.knowledge_root
    diagnostics: list[Diagnostic] = []
    declared_root = context.config.data["workspace"]["knowledge_root"]
    if _declared_path_has_unsafe_component(context.config, declared_root):
        return [_layout_error(context.workspace_id, "knowledge_root traverses a symlink, junction or reparse point")]
    if not _lexists(root):
        if require_initialized:
            diagnostics.append(_not_initialized(context.workspace_id))
        return diagnostics
    if _is_unsafe_link(root):
        return [_layout_error(context.workspace_id, "knowledge_root is a symlink, junction or reparse point")]
    if not root.is_dir():
        return [_layout_error(context.workspace_id, "knowledge_root exists but is not a directory")]
    diagnostics.extend(_mode_diagnostics(root, context.workspace_id, "/workspace/knowledge_root", directory=True))

    marker_path = context.marker_path
    marker_is_predecessor = False
    if _lexists(marker_path):
        if _is_unsafe_link(marker_path) or not marker_path.is_file():
            diagnostics.append(_layout_error(context.workspace_id, "workspace marker is not a regular file"))
        else:
            diagnostics.extend(
                _mode_diagnostics(marker_path, context.workspace_id, "/.research-kb/workspace.json", directory=False)
            )
            try:
                marker = read_json_document(marker_path, record_kind="workspace-marker")
            except ResearchKBError:
                diagnostics.append(_layout_error(context.workspace_id, "workspace marker is not valid canonical JSON"))
            else:
                marker_diagnostics = validate_record("workspace-marker", marker, actor="stored")
                if marker_diagnostics:
                    diagnostics.append(_layout_error(context.workspace_id, "workspace marker does not match its public schema"))
                elif marker == context.expected_marker:
                    pass
                elif marker == _predecessor_marker(context):
                    marker_is_predecessor = True
                else:
                    diagnostics.append(
                        Diagnostic(
                            WORKSPACE_IDENTITY_CONFLICT,
                            "workspace-marker",
                            context.workspace_id,
                            "/config_fingerprint",
                            "knowledge_root is bound to a different workspace configuration",
                        )
                    )
    elif require_initialized:
        diagnostics.append(_not_initialized(context.workspace_id))

    required_directories = P7A_1_MANAGED_DIRECTORIES if marker_is_predecessor else MANAGED_DIRECTORIES
    allowed_directories = (
        MANAGED_DIRECTORIES
        + P9_OPTIONAL_MANAGED_DIRECTORIES
        + P10_OPTIONAL_MANAGED_DIRECTORIES
        + P11_OPTIONAL_MANAGED_DIRECTORIES
    )
    expected_names = {
        _normalized_name(Path(value).parts[0]): Path(value).parts[0]
        for value in allowed_directories
    }
    for child in root.iterdir():
        normalized = _normalized_name(child.name)
        expected = expected_names.get(normalized)
        if expected is None:
            diagnostics.append(_layout_error(context.workspace_id, "knowledge_root contains unknown top-level content"))
        elif child.name != expected:
            diagnostics.append(_layout_error(context.workspace_id, "managed top-level name collides after normalization"))

    for relative in allowed_directories:
        path = root / Path(*relative.split("/"))
        if not _lexists(path):
            if require_initialized and _lexists(context.marker_path) and relative in required_directories:
                diagnostics.append(_layout_error(context.workspace_id, f"managed directory {relative} is missing"))
            continue
        if _is_unsafe_link(path):
            diagnostics.append(_layout_error(context.workspace_id, f"managed path {relative} is an unsafe link"))
        elif not path.is_dir():
            diagnostics.append(_layout_error(context.workspace_id, f"managed directory {relative} collides with a file"))
        else:
            diagnostics.extend(_mode_diagnostics(path, context.workspace_id, f"/{relative}", directory=True))

    if root.is_dir():
        for _path, relative, is_directory, is_unsafe in _iter_managed_descendants(
            root,
            allowed_directories,
        ):
            if is_unsafe:
                diagnostics.append(_layout_error(context.workspace_id, f"managed descendant {relative} is an unsafe link"))
                continue
            if not _recognized_descendant(
                relative,
                allowed_directories,
                is_directory=is_directory,
                allow_discovery_store=True,
            ):
                diagnostics.append(_layout_error(context.workspace_id, "managed layout contains an unknown descendant"))
    if marker_is_predecessor and require_initialized and not any(
        item.severity != "warning" for item in diagnostics
    ):
        diagnostics.append(
            Diagnostic(
                WORKSPACE_LAYOUT_UPGRADE_REQUIRED,
                "workspace-marker",
                context.workspace_id,
                "/layout_contract_version",
                "workspace layout requires upgrade; run workspace init",
            )
        )
    return diagnostics


def _recognized_descendant(
    relative: str,
    managed_directories: tuple[str, ...],
    *,
    is_directory: bool,
    allow_discovery_store: bool,
) -> bool:
    if is_directory:
        return _recognized_managed_directory(relative, managed_directories)
    if relative == MARKER_RELATIVE_PATH or relative == ".research-kb/locks/workspace.lock":
        return True
    patterns = (
        (".research-kb/transactions/", ".json"),
        ("parse/by_paper/", ".pages.jsonl"),
        ("paper_cards/by_paper/", ".card.json"),
        ("evidence/by_paper/", ".evidence.jsonl"),
        ("review_memories/by_paper/", ".review.json"),
        ("primary_bundles/by_paper/", ".primary.json"),
        ("review_bundles/by_paper/", ".review-bundle.json"),
        ("organization/directions/by_id/", ".direction-bundle.json"),
        ("organization/field_map/by_id/", ".field-map-bundle.json"),
        ("organization/questions/by_id/", ".question-revision-bundle.json"),
        ("organization/tags/by_id/", ".tag-bundle.json"),
        ("organization/tag_links/by_id/", ".tag-link-bundle.json"),
        ("organization/screening_criteria/by_id/", ".screening-criteria-bundle.json"),
        ("organization/screening_decisions/by_id/", ".screening-decision-bundle.json"),
    )
    if any(relative.startswith(prefix) and "/" not in relative[len(prefix) :] and relative.endswith(suffix) for prefix, suffix in patterns):
        return True
    exact = {
        "registry/papers.jsonl",
        "registry/source_assets.jsonl",
        "registry/identity_corrections.jsonl",
        "review_queue/items.jsonl",
        "process/events.jsonl",
        "process/jobs.jsonl",
        "process/source_adequacy.jsonl",
        "process/agent_tasks.jsonl",
        "process/maintenance.jsonl",
        "guardian/reports.jsonl",
        "guardian/finding_dispositions.jsonl",
        "questions/mappings.jsonl",
        "step7/synthesis.jsonl",
        "step7/review_angles.jsonl",
        "step7/insights.jsonl",
        "step7/cross_views.jsonl",
    }
    if allow_discovery_store:
        exact.add("discovery/candidates.jsonl")
    return (
        relative in exact
        or relative == "views/obsidian/manifest.json"
        or bool(_OBSIDIAN_GENERATED_FILE.fullmatch(relative))
        or bool(_EXCHANGE_IMPORT_FILE.fullmatch(relative))
        or bool(_EXCHANGE_IMPORT_JOURNAL.fullmatch(relative))
        or bool(_EXCHANGE_EXPORT_RECEIPT.fullmatch(relative))
        or bool(_BACKUP_RECEIPT.fullmatch(relative))
        or bool(_RESTORE_RECEIPT.fullmatch(relative))
        or bool(_OPERATIONAL_ARCHIVE_FILE.fullmatch(relative))
    )


def _recognized_managed_directory(
    relative: str,
    managed_directories: tuple[str, ...],
) -> bool:
    return (
        relative in managed_directories
        or bool(_OBSIDIAN_GENERATION_DIRECTORY.fullmatch(relative))
        or bool(_OBSIDIAN_GENERATION_CHILD_DIRECTORY.fullmatch(relative))
        or bool(_EXCHANGE_IMPORT_DIRECTORY.fullmatch(relative))
        or bool(_EXCHANGE_IMPORT_CHILD_DIRECTORY.fullmatch(relative))
        or bool(_OPERATIONAL_ARCHIVE_DIRECTORY.fullmatch(relative))
    )


def _mode_diagnostics(path: Path, workspace_id: str, json_path: str, *, directory: bool) -> list[Diagnostic]:
    if os.name != "posix":
        return []
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        kind = "directory" if directory else "file"
        return [
            Diagnostic(
                UNSAFE_DIRECTORY_MODE,
                "workspace",
                workspace_id,
                json_path,
                f"managed {kind} grants group or other permissions",
            )
        ]
    return []


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


def _declared_path_has_unsafe_component(document: ConfigDocument, value: str) -> bool:
    candidate = Path(value).expanduser()
    current = Path(candidate.anchor) if candidate.is_absolute() else document.base_dir
    parts = candidate.parts[1:] if candidate.is_absolute() else candidate.parts
    for part in parts:
        current = current / part
        if _lexists(current) and _is_unsafe_link(current):
            return True
    return False


def _iter_managed_descendants(
    root: Path,
    managed_directories: tuple[str, ...],
) -> Iterator[tuple[Path, str, bool, bool]]:
    pending = [(root, ())]
    while pending:
        current, parent_parts = pending.pop()
        with os.scandir(current) as entries:
            for entry in sorted(entries, key=lambda item: _normalized_name(item.name)):
                path = Path(entry.path)
                metadata = entry.stat(follow_symlinks=False)
                is_directory = stat.S_ISDIR(metadata.st_mode)
                is_unsafe = entry.is_symlink() or bool(
                    getattr(metadata, "st_file_attributes", 0)
                    & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
                )
                relative_parts = (*parent_parts, entry.name)
                relative = "/".join(relative_parts)
                yield path, relative, is_directory, is_unsafe
                if (
                    _recognized_managed_directory(relative, managed_directories)
                    and is_directory
                    and not is_unsafe
                ):
                    pending.append((path, relative_parts))


def _path_identity(path: Path) -> str:
    normalized = unicodedata.normalize("NFC", str(path.resolve())).replace("\\", "/")
    return normalized.casefold() if os.name == "nt" else normalized


def _normalized_name(value: str) -> str:
    return unicodedata.normalize("NFC", value).casefold()


def _paths_overlap(left: Path, right: Path) -> bool:
    if _same_physical_path(left, right):
        return True
    left_resolved = left.resolve()
    right_resolved = right.resolve()
    return (
        left_resolved == right_resolved
        or left_resolved.is_relative_to(right_resolved)
        or right_resolved.is_relative_to(left_resolved)
    )


def _same_physical_path(left: Path, right: Path) -> bool:
    if left.exists() and right.exists():
        try:
            return os.path.samefile(left, right)
        except OSError:
            pass
    return _path_identity(left) == _path_identity(right)


def _is_same_or_descendant(path: Path, root: Path) -> bool:
    if _same_physical_path(path, root):
        return True
    resolved = path.resolve()
    root_resolved = root.resolve()
    return resolved == root_resolved or resolved.is_relative_to(root_resolved)


def _lexists(path: Path) -> bool:
    return os.path.lexists(path)


def _not_initialized(workspace_id: str) -> Diagnostic:
    return Diagnostic(
        WORKSPACE_NOT_INITIALIZED,
        "workspace",
        workspace_id,
        "/workspace/knowledge_root",
        "workspace is not initialized; run workspace init",
    )


def _predecessor_marker(context: WorkspaceContext) -> dict[str, Any]:
    marker = dict(context.expected_marker)
    marker["layout_contract_version"] = PREVIOUS_LAYOUT_CONTRACT_VERSION
    return marker


def _layout_error(workspace_id: str, message: str) -> Diagnostic:
    return Diagnostic(WORKSPACE_LAYOUT_CONFLICT, "workspace", workspace_id, "/workspace/knowledge_root", message)


def _warning(workspace_id: str, json_path: str, message: str) -> Diagnostic:
    return Diagnostic(WORKSPACE_PATH_WARNING, "workspace", workspace_id, json_path, message, severity="warning")


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
