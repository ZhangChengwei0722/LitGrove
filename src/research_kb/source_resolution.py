from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any

from research_kb.bundle import BundleEntry, records_of_kind
from research_kb.errors import PATH_ESCAPE, Diagnostic, ResearchKBError
from research_kb.paths import SourceRef, make_source_ref
from research_kb.source_assets import current_source_asset_heads, source_asset_projection
from research_kb.storage.json_io import file_sha256
from research_kb.workspace import WorkspaceLayout


@dataclass(frozen=True, slots=True)
class PaperSourceObservation:
    source_ref: dict[str, str]
    path: Path
    expected_sha256: str
    live_sha256: str | None
    state: str
    source_asset_id: str | None
    source_asset_state_id: str | None


@dataclass(frozen=True, slots=True)
class SourceRefObservation:
    source_ref: SourceRef
    path: Path
    live_sha256: str | None
    availability: str


def inspect_source_ref(
    layout: WorkspaceLayout,
    *,
    root_id: str,
    relative_path: str,
) -> SourceRefObservation:
    source_ref = make_source_ref(root_id, relative_path)
    try:
        root = layout.source_roots[root_id]
    except KeyError as error:
        raise ResearchKBError(
            Diagnostic(PATH_ESCAPE, "source-ref", None, "/root_id", "source root is not declared by workspace")
        ) from error
    lexical = root.joinpath(*PurePosixPath(source_ref.relative_path).parts)
    if _declared_source_root_is_unsafe(layout, root_id):
        return SourceRefObservation(source_ref, lexical, None, "relink_required")
    current = root
    for part in PurePosixPath(source_ref.relative_path).parts:
        current = current / part
        if os.path.lexists(current) and _is_unsafe_link(current):
            return SourceRefObservation(source_ref, lexical, None, "relink_required")
    try:
        metadata = os.lstat(lexical)
    except FileNotFoundError:
        return SourceRefObservation(source_ref, lexical, None, "missing")
    except OSError:
        return SourceRefObservation(source_ref, lexical, None, "inaccessible")
    if not stat.S_ISREG(metadata.st_mode):
        return SourceRefObservation(source_ref, lexical, None, "not_regular_file")
    if getattr(metadata, "st_nlink", 1) != 1:
        return SourceRefObservation(source_ref, lexical, None, "relink_required")
    try:
        digest = file_sha256(lexical)
    except OSError:
        return SourceRefObservation(source_ref, lexical, None, "inaccessible")
    if digest is None:
        return SourceRefObservation(source_ref, lexical, None, "inaccessible")
    return SourceRefObservation(source_ref, lexical, digest, "available")


def observe_paper_source(
    layout: WorkspaceLayout,
    entries: list[BundleEntry],
    paper: dict[str, Any],
) -> PaperSourceObservation:
    all_states = records_of_kind(entries, "source-asset-state")
    explicit_asset_ids = {
        head["source_asset_id"]
        for head in current_source_asset_heads(all_states)
        if head.get("paper_id") == paper["paper_id"]
        and head.get("asset_role") == "main_pdf"
    }
    if not explicit_asset_ids:
        source_ref = dict(paper["source_ref"])
        inspected = inspect_source_ref(
            layout,
            root_id=source_ref["root_id"],
            relative_path=source_ref["relative_path"],
        )
        live = inspected.live_sha256
        state = _currentness(inspected, paper["source_fingerprint"]["value"])
        return PaperSourceObservation(
            source_ref,
            inspected.path,
            paper["source_fingerprint"]["value"],
            live,
            state,
            None,
            None,
        )

    states = [
        item for item in all_states if item.get("source_asset_id") in explicit_asset_ids
    ]
    projection = source_asset_projection(states)[0]
    head = current_source_asset_heads(states)[0]
    source_ref = dict(head["source_ref"])
    inspected = inspect_source_ref(
        layout,
        root_id=source_ref["root_id"],
        relative_path=source_ref["relative_path"],
    )
    expected = head["source_fingerprint"]["value"]
    live = inspected.live_sha256
    live_state = _currentness(inspected, expected)
    if projection["source_currentness"] == "stale_source":
        state = "stale_source"
    elif projection["source_currentness"] == "unavailable":
        state = projection["source_availability"]
    else:
        state = live_state
    return PaperSourceObservation(
        source_ref,
        inspected.path,
        expected,
        live,
        state,
        head["source_asset_id"],
        head["source_asset_state_id"],
    )


def _currentness(observation: SourceRefObservation, expected_sha256: str) -> str:
    if observation.availability != "available":
        return observation.availability
    return "current" if observation.live_sha256 == expected_sha256 else "fingerprint_mismatch"


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


def _declared_source_root_is_unsafe(layout: WorkspaceLayout, root_id: str) -> bool:
    declared_value = next(
        (
            item.get("path")
            for item in layout.config.data["workspace"]["source_roots"]
            if item.get("root_id") == root_id
        ),
        None,
    )
    if not isinstance(declared_value, str):
        return True
    declared = Path(declared_value).expanduser()
    current = Path(declared.anchor) if declared.is_absolute() else layout.config.base_dir
    parts = declared.parts[1:] if declared.is_absolute() else declared.parts
    for part in parts:
        current = current / part
        if os.path.lexists(current) and _is_unsafe_link(current):
            return True
    return False


__all__ = [
    "PaperSourceObservation",
    "SourceRefObservation",
    "inspect_source_ref",
    "observe_paper_source",
]
