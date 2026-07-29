from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from research_kb.config.loader import load_config
from research_kb.errors import PATH_ESCAPE, SCHEMA_VALIDATION_FAILED, Diagnostic, ResearchKBError
from research_kb.workspace import WorkspaceLayout


WORKSPACE_OPTION_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,63}$")


@dataclass(frozen=True, slots=True)
class WorkspaceSession:
    option_id: str
    workspace_id: str
    domain_profile_id: str
    domain_name: str
    domain_version: str
    _layout: WorkspaceLayout = field(repr=False, compare=False)

    def display(self) -> dict[str, str]:
        return {
            "option_id": self.option_id,
            "workspace_id": self.workspace_id,
            "domain_profile_id": self.domain_profile_id,
            "domain_name": self.domain_name,
            "domain_version": self.domain_version,
        }


class WorkspaceSessionService:
    def __init__(self, configured_workspaces: Mapping[str, Path]):
        normalized: dict[str, Path] = {}
        seen_paths: set[Path] = set()
        for option_id, config_path in configured_workspaces.items():
            if not WORKSPACE_OPTION_PATTERN.fullmatch(option_id):
                raise ResearchKBError(
                    Diagnostic(
                        SCHEMA_VALIDATION_FAILED,
                        "workspace-session",
                        None,
                        "/option_id",
                        "workspace option ID is invalid",
                    )
                )
            path = Path(config_path)
            if not path.is_absolute():
                raise _session_path_error("configured workspace path must be absolute")
            if _has_unsafe_component(path):
                raise _session_path_error(
                    "configured workspace path traverses a symlink, junction or reparse point"
                )
            resolved = path.resolve()
            if resolved in seen_paths:
                raise _session_path_error("configured workspace paths must be unique")
            if not resolved.is_file():
                raise _session_path_error("configured workspace path is not a regular file")
            seen_paths.add(resolved)
            normalized[option_id] = resolved
        self._configured_workspaces = normalized

    def list_options(self) -> dict[str, Any]:
        return {
            "status": "success",
            "workspaces": [
                self._load(option_id, path).display()
                for option_id, path in sorted(self._configured_workspaces.items())
            ],
        }

    def open(self, option_id: str) -> WorkspaceSession:
        try:
            path = self._configured_workspaces[option_id]
        except KeyError as error:
            raise ResearchKBError(
                Diagnostic(
                    SCHEMA_VALIDATION_FAILED,
                    "workspace-session",
                    None,
                    "/option_id",
                    "workspace option is not configured",
                )
            ) from error
        return self._load(option_id, path)

    @staticmethod
    def _load(option_id: str, path: Path) -> WorkspaceSession:
        layout = WorkspaceLayout.load(path)
        profile = load_config(layout.domain_profile_path, "domain-profile").data["domain_profile"]
        return WorkspaceSession(
            option_id,
            layout.workspace_id,
            profile["id"],
            profile["name"],
            profile["version"],
            layout,
        )


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


def _session_path_error(message: str) -> ResearchKBError:
    return ResearchKBError(
        Diagnostic(PATH_ESCAPE, "workspace-session", None, "/configured_workspace", message)
    )


__all__ = ["WorkspaceSession", "WorkspaceSessionService"]
