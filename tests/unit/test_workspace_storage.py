from __future__ import annotations

from pathlib import Path

from tests.runtime_helpers import make_runtime_workspace
from research_kb.services import WorkspaceStorageInspectionService


def test_workspace_storage_inspection_is_authoritative_and_read_only(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path, local_inbox="./sources/inbox", create_local_inbox=True)
    before = {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }

    roots = WorkspaceStorageInspectionService().inspect(layout.config.path)

    assert roots.workspace_config_root == layout.config.path.parent.resolve()
    assert roots.knowledge_root == layout.knowledge_root.resolve()
    assert roots.local_inbox == layout.local_inbox.resolve()
    assert {path.relative_to(tmp_path).as_posix(): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()} == before
