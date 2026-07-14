from pathlib import Path

import pytest
import yaml

from research_kb.errors import ResearchKBError
from research_kb.workspace import WorkspaceLayout
from tests.runtime_helpers import make_runtime_workspace


def test_workspace_layout_resolves_domain_neutral_paths(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    assert layout.registry_path == layout.knowledge_root / "registry" / "papers.jsonl"
    assert layout.target_relative_path(layout.evidence_path("paper-example")) == "evidence/by_paper/paper-example.evidence.jsonl"


def test_workspace_rejects_write_outside_knowledge_root(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    with pytest.raises(ResearchKBError) as caught:
        layout.ensure_writable_target(tmp_path / "outside.jsonl")
    assert caught.value.diagnostic.code == "RKBC-007"


def test_workspace_rejects_overlapping_source_and_knowledge_roots(tmp_path: Path) -> None:
    root = tmp_path / "overlap"
    root.mkdir()
    config = {
        "contract_version": "1.0",
        "workspace": {
            "id": "workspace_a1111111-1111-4111-8111-111111111111",
            "knowledge_root": "./shared/knowledge",
            "source_roots": [{"root_id": "sources", "path": "./shared", "read_only_assets": True}],
            "local_inbox": "./inbox",
            "domain_profile": "./domain-profile.yaml",
        },
        "runtime": {"path_serialization": "workspace_relative_posix", "default_encoding": "utf-8", "line_ending": "lf"},
    }
    path = root / "workspace.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8", newline="\n")
    with pytest.raises(ResearchKBError) as caught:
        WorkspaceLayout.load(path)
    assert caught.value.diagnostic.code == "RKBC-007"
