from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path

from research_kb.errors import (
    PATH_ESCAPE,
    WORKSPACE_LAYOUT_CONFLICT,
    Diagnostic,
    ResearchKBError,
)
from research_kb.identifiers import Namespace, validate_id
from research_kb.paths import SourceRef, make_source_ref
from research_kb.workspace import WorkspaceLayout


@dataclass(frozen=True, slots=True)
class AcquisitionDestination:
    root_id: str
    inbox: Path
    final_path: Path
    source_ref: SourceRef


def acquisition_destination(
    layout: WorkspaceLayout,
    candidate_id: str,
) -> AcquisitionDestination:
    candidate_id = validate_id(candidate_id, Namespace.DISCOVERY)
    _require_safe_declared_inbox(layout)
    inbox = layout.local_inbox
    if not _lexists(inbox) or not inbox.is_dir():
        raise _layout_error(layout, "local_inbox must already exist as a directory")
    if _is_unsafe_link(inbox):
        raise _path_error(layout, "local_inbox must not be a symlink, junction or reparse point")
    if not os.access(inbox, os.R_OK | os.W_OK | os.X_OK):
        raise _layout_error(layout, "local_inbox is not readable, writable and traversable")

    owners = sorted(
        (
            (root_id, root)
            for root_id, root in layout.source_roots.items()
            if inbox == root or inbox.is_relative_to(root)
        ),
        key=lambda item: item[0],
    )
    if len(owners) != 1:
        raise _layout_error(
            layout,
            "local_inbox must be addressable through exactly one declared source root",
        )
    root_id, root = owners[0]
    final_path = inbox / f"{candidate_id}.pdf"
    if final_path.parent != inbox:
        raise _path_error(layout, "acquisition target escaped local_inbox")
    relative_path = final_path.relative_to(root).as_posix()
    return AcquisitionDestination(
        root_id=root_id,
        inbox=inbox,
        final_path=final_path,
        source_ref=make_source_ref(root_id, relative_path),
    )


def _require_safe_declared_inbox(layout: WorkspaceLayout) -> None:
    declared = Path(layout.config.data["workspace"]["local_inbox"]).expanduser()
    current = Path(declared.anchor) if declared.is_absolute() else layout.config.base_dir
    parts = declared.parts[1:] if declared.is_absolute() else declared.parts
    for part in parts:
        current = current / part
        if _lexists(current) and _is_unsafe_link(current):
            raise _path_error(
                layout,
                "declared local_inbox traverses a symlink, junction or reparse point",
            )


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


def _lexists(path: Path) -> bool:
    return os.path.lexists(path)


def _layout_error(layout: WorkspaceLayout, message: str) -> ResearchKBError:
    return ResearchKBError(
        Diagnostic(
            WORKSPACE_LAYOUT_CONFLICT,
            "workspace",
            layout.workspace_id,
            "/workspace/local_inbox",
            message,
        )
    )


def _path_error(layout: WorkspaceLayout, message: str) -> ResearchKBError:
    return ResearchKBError(
        Diagnostic(
            PATH_ESCAPE,
            "workspace",
            layout.workspace_id,
            "/workspace/local_inbox",
            message,
        )
    )


__all__ = ["AcquisitionDestination", "acquisition_destination"]
