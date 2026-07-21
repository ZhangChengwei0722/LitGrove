from __future__ import annotations

from pathlib import Path

import yaml

from research_kb.services.bootstrap import WorkspaceBootstrapService
from research_kb.workspace import WorkspaceLayout
from tests.fixture_factory import SECTIONS


def make_runtime_workspace(
    tmp_path: Path,
    domain: str = "alpha",
    *,
    local_inbox: str = "./inbox",
    create_local_inbox: bool = False,
    source_roots: list[dict[str, object]] | None = None,
) -> WorkspaceLayout:
    root = tmp_path / domain
    root.mkdir()
    (root / "sources").mkdir()
    workspace_id = (
        "workspace_a1111111-1111-4111-8111-111111111111"
        if domain == "alpha"
        else "workspace_b2222222-2222-4222-8222-222222222222"
    )
    workspace = {
        "contract_version": "1.0",
        "workspace": {
            "id": workspace_id,
            "knowledge_root": "./knowledge",
            "source_roots": source_roots
            or [{"root_id": f"{domain}-sources", "path": "./sources", "read_only_assets": True}],
            "local_inbox": local_inbox,
            "domain_profile": "./domain-profile.yaml",
        },
        "runtime": {"path_serialization": "workspace_relative_posix", "default_encoding": "utf-8", "line_ending": "lf"},
    }
    config_path = root / "workspace.yaml"
    config_path.write_text(yaml.safe_dump(workspace, sort_keys=False), encoding="utf-8", newline="\n")
    profile = {
        "contract_version": "1.0",
        "domain_profile": {
            "id": f"domain-{domain}",
            "name": f"Synthetic {domain.title()} Domain",
            "version": "1.0",
        },
        "paper_card_sections": [
            {"section_id": section_id, "label": section_id.replace("_", " ").title()}
            for section_id in SECTIONS
        ],
        "evidence_axes": ["input", "process", "outcome"],
        "question_types": ["mechanism", "comparison"],
        "terminology": {"sample": "synthetic case"},
        "step7_extensions": {},
    }
    (root / "domain-profile.yaml").write_text(
        yaml.safe_dump(profile, sort_keys=False),
        encoding="utf-8",
        newline="\n",
    )
    if create_local_inbox:
        (root / local_inbox).mkdir(parents=True)
    result = WorkspaceBootstrapService(config_path).run()
    if result.exit_code != 0:
        raise AssertionError(result.to_dict())
    return WorkspaceLayout.load(config_path)
