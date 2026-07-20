from pathlib import Path

import pytest
import yaml

from research_kb.config.loader import load_config
from research_kb.errors import ResearchKBError
from research_kb.services.bootstrap import WorkspaceBootstrapService
from research_kb.services.intake_inspect import IntakeInspectService
from research_kb.services.registry import RegistryService
from research_kb.storage.json_io import file_sha256
from research_kb.workspace import WorkspaceLayout
from tests.fixture_factory import SECTIONS
from tests.runtime_helpers import make_runtime_workspace


def _tree_snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _source(layout, name: str, text: str = "Invented intake source.\n") -> Path:
    path = layout.source_roots["alpha-sources"] / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def _register(layout, source: Path):
    return RegistryService(layout).add(
        root_id="alpha-sources",
        relative_path=source.relative_to(layout.source_roots["alpha-sources"]).as_posix(),
        metadata={
            "bibliography": {"title": f"Synthetic {source.stem}"},
            "fixture_origin": "synthetic_from_scratch",
        },
    )[0]


def _nested_roots_workspace(tmp_path: Path) -> WorkspaceLayout:
    root = tmp_path / "nested-roots"
    (root / "sources" / "inner").mkdir(parents=True)
    workspace = {
        "contract_version": "1.0",
        "workspace": {
            "id": "workspace_c3333333-3333-4333-8333-333333333333",
            "knowledge_root": "./knowledge",
            "source_roots": [
                {"root_id": "outer-sources", "path": "./sources", "read_only_assets": True},
                {"root_id": "inner-sources", "path": "./sources/inner", "read_only_assets": True},
            ],
            "local_inbox": "./inbox",
            "domain_profile": "./domain-profile.yaml",
        },
        "runtime": {
            "path_serialization": "workspace_relative_posix",
            "default_encoding": "utf-8",
            "line_ending": "lf",
        },
    }
    profile = {
        "contract_version": "1.0",
        "domain_profile": {"id": "domain-nested", "name": "Synthetic Nested Domain", "version": "1.0"},
        "paper_card_sections": [
            {"section_id": section_id, "label": section_id.replace("_", " ").title()}
            for section_id in SECTIONS
        ],
        "evidence_axes": ["input", "outcome"],
        "question_types": ["comparison"],
        "terminology": {},
        "step7_extensions": {},
    }
    config_path = root / "workspace.yaml"
    config_path.write_text(yaml.safe_dump(workspace, sort_keys=False), encoding="utf-8", newline="\n")
    (root / "domain-profile.yaml").write_text(
        yaml.safe_dump(profile, sort_keys=False),
        encoding="utf-8",
        newline="\n",
    )
    result = WorkspaceBootstrapService(config_path).run()
    assert result.exit_code == 0
    return WorkspaceLayout.load(config_path)


def test_intake_inspect_maps_unregistered_source_and_profile_without_mutation(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    source = _source(layout, "nested/new-study.pdf")
    profile = load_config(layout.domain_profile_path, "domain-profile").data
    source_before = source.read_bytes()
    knowledge_before = _tree_snapshot(layout.knowledge_root)

    result = IntakeInspectService(layout).inspect(source=source)

    assert result == {
        "status": "success",
        "interface_version": "1.0",
        "workspace_id": layout.workspace_id,
        "source": {
            "root_id": "alpha-sources",
            "relative_path": "nested/new-study.pdf",
            "fingerprint_algorithm": "sha256",
        },
        "registration": {"state": "unregistered", "paper_ids": []},
        "domain_profile": {
            "id": profile["domain_profile"]["id"],
            "version": profile["domain_profile"]["version"],
            "paper_card_sections": profile["paper_card_sections"],
        },
    }
    assert str(tmp_path) not in str(result)
    assert file_sha256(source) not in str(result)
    assert source.read_bytes() == source_before
    assert _tree_snapshot(layout.knowledge_root) == knowledge_before


def test_intake_inspect_reports_current_then_stale_exact_registration(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    source = _source(layout, "registered.pdf")
    paper = _register(layout, source)
    service = IntakeInspectService(layout)

    current = service.inspect(source=source)

    assert current["registration"] == {
        "state": "registered_current",
        "paper_ids": [paper["paper_id"]],
    }
    assert paper["source_fingerprint"]["value"] not in str(current)

    source.write_text("Invented changed intake source.\n", encoding="utf-8", newline="\n")
    stale = service.inspect(source=source)

    assert stale["registration"] == {
        "state": "registered_stale",
        "paper_ids": [paper["paper_id"]],
    }


def test_intake_inspect_reports_ambiguous_exact_registration(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    source = _source(layout, "ambiguous.pdf")
    first = _register(layout, source)
    second = _register(layout, source)

    result = IntakeInspectService(layout).inspect(source=source)

    assert result["registration"] == {
        "state": "ambiguous",
        "paper_ids": sorted((first["paper_id"], second["paper_id"])),
    }


def test_intake_inspect_does_not_treat_same_hash_at_other_path_as_exact(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    first_source = _source(layout, "first.pdf", "Invented same bytes.\n")
    second_source = _source(layout, "second.pdf", "Invented same bytes.\n")
    _register(layout, first_source)

    result = IntakeInspectService(layout).inspect(source=second_source)

    assert result["registration"] == {"state": "unregistered", "paper_ids": []}
    assert result["source"]["relative_path"] == "second.pdf"


@pytest.mark.parametrize(
    ("case", "expected_code"),
    (
        ("relative", "RKBC-007"),
        ("missing", "RKBC-002"),
        ("directory", "RKBC-002"),
        ("outside", "RKBC-007"),
    ),
)
def test_intake_inspect_rejects_unsafe_source_paths(
    case: str,
    expected_code: str,
    tmp_path: Path,
) -> None:
    layout = make_runtime_workspace(tmp_path)
    if case == "relative":
        source = Path("relative.pdf")
    elif case == "missing":
        source = layout.source_roots["alpha-sources"] / "missing.pdf"
    elif case == "directory":
        source = layout.source_roots["alpha-sources"]
    else:
        source = tmp_path / "outside.pdf"
        source.write_text("Invented outside source.\n", encoding="utf-8", newline="\n")

    with pytest.raises(ResearchKBError) as caught:
        IntakeInspectService(layout).inspect(source=source)

    assert caught.value.diagnostic.code == expected_code
    assert str(tmp_path) not in caught.value.diagnostic.message


def test_intake_inspect_rejects_link_escape_when_supported(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    outside = tmp_path / "outside-link-target.pdf"
    outside.write_text("Invented link target.\n", encoding="utf-8", newline="\n")
    link = layout.source_roots["alpha-sources"] / "escape.pdf"
    try:
        link.symlink_to(outside)
    except OSError as error:
        pytest.skip(f"host cannot create test symlink: {error}")

    with pytest.raises(ResearchKBError) as caught:
        IntakeInspectService(layout).inspect(source=link)

    assert caught.value.diagnostic.code == "RKBC-007"
    assert str(tmp_path) not in caught.value.diagnostic.message


def test_intake_inspect_rejects_ambiguous_nested_root_ownership(tmp_path: Path) -> None:
    layout = _nested_roots_workspace(tmp_path)
    source = layout.source_roots["inner-sources"] / "ambiguous.pdf"
    source.write_text("Invented nested-root source.\n", encoding="utf-8", newline="\n")
    before = _tree_snapshot(layout.config.path.parent)

    with pytest.raises(ResearchKBError) as caught:
        IntakeInspectService(layout).inspect(source=source)

    assert caught.value.diagnostic.code == "RKBC-007"
    assert str(tmp_path) not in caught.value.diagnostic.message
    assert _tree_snapshot(layout.config.path.parent) == before


def test_intake_inspect_rejects_source_change_during_projection(tmp_path: Path, monkeypatch) -> None:
    layout = make_runtime_workspace(tmp_path)
    source = _source(layout, "changing.pdf")
    expected = file_sha256(source)
    calls = iter((expected, "f" * 64))
    monkeypatch.setattr("research_kb.services.intake_inspect.file_sha256", lambda _: next(calls))

    with pytest.raises(ResearchKBError) as caught:
        IntakeInspectService(layout).inspect(source=source)

    assert caught.value.diagnostic.code == "RKBC-009"
