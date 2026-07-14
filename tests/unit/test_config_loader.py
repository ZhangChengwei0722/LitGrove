from pathlib import Path
from copy import deepcopy

from research_kb.config.loader import load_config, resolve_config_path
from research_kb.contracts.validator import validate_record
from tests.fixture_factory import make_bundle


ROOT = Path(__file__).resolve().parents[2]


def test_config_resolution_does_not_depend_on_cwd(tmp_path: Path, monkeypatch) -> None:
    config_path = ROOT / "tests" / "fixtures" / "domain_alpha" / "workspace.yaml"
    monkeypatch.chdir(tmp_path)
    document = load_config(config_path, "workspace")
    assert resolve_config_path(document, document.data["workspace"]["knowledge_root"]) == config_path.parent / "knowledge"


def test_domain_profile_loads_as_yaml() -> None:
    path = ROOT / "tests" / "fixtures" / "domain_beta" / "domain-profile.yaml"
    document = load_config(path, "domain-profile")
    assert document.data["domain_profile"]["id"] == "domain-beta"


def test_workspace_managed_paths_must_be_config_relative() -> None:
    workspace = deepcopy(make_bundle("alpha")["records"][0]["record"])
    workspace["workspace"]["knowledge_root"] = "/external/knowledge"
    diagnostics = validate_record("workspace", workspace, actor="cli")
    assert "RKBC-007" in {item.code for item in diagnostics}
