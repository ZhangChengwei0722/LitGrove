from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from research_kb.workspace_validation import validate_initialized_workspace


@dataclass(frozen=True, slots=True)
class WorkspaceStorageRoots:
    """Read-only, resolved roots that the App must preflight before opening a session."""

    workspace_config_root: Path
    knowledge_root: Path
    local_inbox: Path

    def paths(self) -> tuple[Path, ...]:
        return (
            self.workspace_config_root,
            self.knowledge_root,
            self.local_inbox,
        )


class WorkspaceStorageInspectionService:
    """Expose Core-owned writable roots without constructing a workspace session."""

    def inspect(self, config_path: Path) -> WorkspaceStorageRoots:
        path = Path(config_path).resolve()
        context = validate_initialized_workspace(path).require_valid()
        return WorkspaceStorageRoots(
            workspace_config_root=path.parent,
            knowledge_root=context.knowledge_root,
            local_inbox=context.local_inbox,
        )


__all__ = ["WorkspaceStorageInspectionService", "WorkspaceStorageRoots"]
