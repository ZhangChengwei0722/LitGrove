from __future__ import annotations

import json
import os
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import yaml

import research_kb.services.bootstrap as bootstrap_module
import research_kb.workspace_validation as workspace_validation_module
from research_kb.contracts.validator import validate_record
from research_kb.errors import ResearchKBError
from research_kb.guardian import GuardianService
from research_kb.services.bootstrap import MANAGED_DIRECTORIES, WorkspaceBootstrapService
from research_kb.services.registry import RegistryService
from research_kb.storage.json_io import atomic_write_bytes, file_sha256, read_json_document, serialize_json
from research_kb.storage.transactions import TransactionManager
from research_kb.workspace import WorkspaceLayout
from research_kb.workspace_validation import build_workspace_marker
from tests.runtime_helpers import make_runtime_workspace


def _write_workspace(
    root: Path,
    *,
    workspace_id: str = "workspace_a1111111-1111-4111-8111-111111111111",
    local_inbox: str = "./inbox",
    source_roots: list[dict[str, object]] | None = None,
    config_name: str = "workspace.yaml",
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    sources = root / "sources"
    sources.mkdir(exist_ok=True)
    profile = {
        "contract_version": "1.0",
        "domain_profile": {"id": "domain-alpha", "name": "Synthetic Alpha", "version": "1.0"},
        "paper_card_sections": [
            {"section_id": value, "label": value.replace("_", " ").title()}
            for value in (
                "research_background_significance",
                "research_problem",
                "method_principle_advantages",
                "conclusions_applications",
                "innovation",
                "limitations",
                "future_outlook",
            )
        ],
        "evidence_axes": ["input", "outcome"],
        "question_types": ["comparison"],
        "terminology": {},
        "step7_extensions": {},
    }
    (root / "domain-profile.yaml").write_text(
        yaml.safe_dump(profile, sort_keys=False), encoding="utf-8", newline="\n"
    )
    workspace = {
        "contract_version": "1.0",
        "workspace": {
            "id": workspace_id,
            "knowledge_root": "./knowledge",
            "source_roots": source_roots
            or [{"root_id": "alpha-sources", "path": "./sources", "read_only_assets": True}],
            "local_inbox": local_inbox,
            "domain_profile": "./domain-profile.yaml",
        },
        "runtime": {
            "path_serialization": "workspace_relative_posix",
            "default_encoding": "utf-8",
            "line_ending": "lf",
        },
    }
    path = root / config_name
    path.write_text(yaml.safe_dump(workspace, sort_keys=False), encoding="utf-8", newline="\n")
    return path


def _downgrade_marker_to_m3c_2a(config_path: Path, *, keep_primary_directories: bool = False) -> bytes:
    knowledge_root = config_path.parent / "knowledge"
    marker_path = knowledge_root / ".research-kb" / "workspace.json"
    marker = read_json_document(marker_path, record_kind="workspace-marker")
    marker["layout_contract_version"] = "m3c-2a"
    marker_bytes = serialize_json(marker)
    atomic_write_bytes(marker_path, marker_bytes, "test-downgrade-marker")
    primary_root = knowledge_root / "primary_bundles"
    if not keep_primary_directories:
        (primary_root / "by_paper").rmdir()
        primary_root.rmdir()
    return marker_bytes


def test_workspace_marker_schema_and_serialization_are_deterministic(tmp_path: Path) -> None:
    config_path = _write_workspace(tmp_path / "workspace")
    marker = build_workspace_marker(config_path)

    assert validate_record("workspace-marker", marker, actor="stored") == []
    assert serialize_json(marker) == serialize_json(build_workspace_marker(config_path))
    assert set(marker) == {
        "schema_version",
        "workspace_id",
        "domain_profile_id",
        "domain_profile_version",
        "layout_contract_version",
        "config_fingerprint",
    }
    assert str(tmp_path) not in serialize_json(marker).decode("utf-8")
    assert marker["layout_contract_version"] == "p4b-1"


def test_old_layout_dry_run_plans_upgrade_without_mutation(tmp_path: Path) -> None:
    config_path = _write_workspace(tmp_path / "workspace")
    assert WorkspaceBootstrapService(config_path).run().result == "initialized"
    marker_before = _downgrade_marker_to_m3c_2a(config_path)
    knowledge_root = config_path.parent / "knowledge"
    tree_before = {
        path.relative_to(knowledge_root).as_posix(): path.read_bytes()
        for path in knowledge_root.rglob("*")
        if path.is_file()
    }

    result = WorkspaceBootstrapService(config_path).run(dry_run=True)

    assert result.result == "planned"
    assert result.exit_code == 0
    assert {tuple(item.values()) for item in result.managed_actions} >= {
        ("primary_bundles", "create_directory"),
        ("primary_bundles/by_paper", "create_directory"),
        (".research-kb/workspace.json", "upgrade_identity_marker"),
    }
    assert not (knowledge_root / "primary_bundles").exists()
    assert (knowledge_root / "review_memories" / "by_paper").is_dir()
    assert (knowledge_root / ".research-kb" / "workspace.json").read_bytes() == marker_before
    assert {
        path.relative_to(knowledge_root).as_posix(): path.read_bytes()
        for path in knowledge_root.rglob("*")
        if path.is_file()
    } == tree_before


def test_old_layout_dry_run_rejects_invalid_structured_state(tmp_path: Path) -> None:
    config_path = _write_workspace(tmp_path / "workspace")
    assert WorkspaceBootstrapService(config_path).run().result == "initialized"
    marker_before = _downgrade_marker_to_m3c_2a(config_path)
    knowledge_root = config_path.parent / "knowledge"
    (knowledge_root / "registry" / "papers.jsonl").write_bytes(b"{}")

    result = WorkspaceBootstrapService(config_path).run(dry_run=True)

    assert result.result == "blocked"
    assert result.exit_code == 4
    assert "RKBC-015" in {item.code for item in result.diagnostics}
    assert (knowledge_root / ".research-kb" / "workspace.json").read_bytes() == marker_before
    assert not (knowledge_root / "primary_bundles").exists()


def test_old_layout_apply_upgrades_only_directory_and_marker_then_converges(tmp_path: Path) -> None:
    config_path = _write_workspace(tmp_path / "workspace")
    assert WorkspaceBootstrapService(config_path).run().result == "initialized"
    _downgrade_marker_to_m3c_2a(config_path)
    knowledge_root = config_path.parent / "knowledge"

    upgraded = WorkspaceBootstrapService(config_path).run()

    assert upgraded.result == "initialized"
    assert upgraded.exit_code == 0
    actions = {tuple(item.values()) for item in upgraded.managed_actions}
    assert ("primary_bundles", "create_directory") in actions
    assert ("primary_bundles/by_paper", "create_directory") in actions
    assert (".research-kb/workspace.json", "upgrade_identity_marker") in actions
    assert (knowledge_root / "questions").is_dir()
    assert not (knowledge_root / "questions" / "mappings.jsonl").exists()
    assert (knowledge_root / "review_memories" / "by_paper").is_dir()
    assert (knowledge_root / "step7").is_dir()
    assert (knowledge_root / "discovery").is_dir()
    assert not any((knowledge_root / "discovery").iterdir())
    assert not (knowledge_root / "discovery" / "candidates.jsonl").exists()
    assert not (knowledge_root / "process" / "events.jsonl").exists()
    assert not list((knowledge_root / ".research-kb" / "transactions").glob("*.json"))
    assert (knowledge_root / "primary_bundles" / "by_paper").is_dir()
    assert read_json_document(
        knowledge_root / ".research-kb" / "workspace.json",
        record_kind="workspace-marker",
    )["layout_contract_version"] == "p4b-1"
    assert WorkspaceBootstrapService(config_path).run().result == "no_change"


def test_old_layout_upgrade_resumes_from_safe_empty_primary_directories(tmp_path: Path) -> None:
    config_path = _write_workspace(tmp_path / "workspace")
    assert WorkspaceBootstrapService(config_path).run().result == "initialized"
    _downgrade_marker_to_m3c_2a(config_path, keep_primary_directories=True)

    result = WorkspaceBootstrapService(config_path).run()

    assert result.result == "initialized"
    assert read_json_document(
        config_path.parent / "knowledge" / ".research-kb" / "workspace.json",
        record_kind="workspace-marker",
    )["layout_contract_version"] == "p4b-1"


def test_old_layout_upgrade_rejects_nonempty_primary_directory(tmp_path: Path) -> None:
    config_path = _write_workspace(tmp_path / "workspace")
    assert WorkspaceBootstrapService(config_path).run().result == "initialized"
    marker_before = _downgrade_marker_to_m3c_2a(config_path, keep_primary_directories=True)
    primary_directory = config_path.parent / "knowledge" / "primary_bundles"
    (primary_directory / "unexpected.json").write_text("{}\n", encoding="utf-8", newline="\n")

    result = WorkspaceBootstrapService(config_path).run()

    assert result.result == "blocked"
    assert result.exit_code == 4
    assert "RKBC-021" in {item.code for item in result.diagnostics}
    assert (config_path.parent / "knowledge" / ".research-kb" / "workspace.json").read_bytes() == marker_before


def test_old_layout_marker_write_failure_leaves_only_resumable_state(tmp_path: Path) -> None:
    config_path = _write_workspace(tmp_path / "workspace")
    assert WorkspaceBootstrapService(config_path).run().result == "initialized"
    marker_before = _downgrade_marker_to_m3c_2a(config_path)
    knowledge_root = config_path.parent / "knowledge"

    def fail_marker(path: Path, content: bytes, write_id: str) -> None:
        raise OSError("injected")

    failed = WorkspaceBootstrapService(config_path, marker_writer=fail_marker).run()

    assert failed.result == "blocked"
    assert (knowledge_root / "primary_bundles" / "by_paper").is_dir()
    assert (knowledge_root / ".research-kb" / "workspace.json").read_bytes() == marker_before
    assert WorkspaceBootstrapService(config_path).run().result == "initialized"


def test_runtime_rejects_upgradeable_old_layout(tmp_path: Path) -> None:
    config_path = _write_workspace(tmp_path / "workspace")
    assert WorkspaceBootstrapService(config_path).run().result == "initialized"
    _downgrade_marker_to_m3c_2a(config_path)

    with pytest.raises(ResearchKBError) as caught:
        WorkspaceLayout.load(config_path)

    assert caught.value.diagnostic.code == "RKBC-027"


def test_dry_run_is_read_only_then_apply_is_idempotent(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    config_path = _write_workspace(root)
    source = root / "sources" / "paper.txt"
    source.write_text("Synthetic source.\n", encoding="utf-8", newline="\n")
    source_hash = file_sha256(source)
    service = WorkspaceBootstrapService(config_path)

    dry_run = service.run(dry_run=True)
    assert dry_run.result == "planned"
    assert dry_run.exit_code == 0
    assert not (root / "knowledge").exists()

    initialized = service.run()
    assert initialized.result == "initialized"
    assert initialized.exit_code == 0
    assert file_sha256(source) == source_hash
    knowledge_root = root / "knowledge"
    assert {
        path.relative_to(knowledge_root).as_posix()
        for path in knowledge_root.rglob("*")
        if path.is_dir()
    } == set(MANAGED_DIRECTORIES) - {"."}
    assert (knowledge_root / ".research-kb" / "locks" / "workspace.lock").is_file()
    assert (knowledge_root / ".research-kb" / "workspace.json").is_file()
    assert not (knowledge_root / "process" / "events.jsonl").exists()
    assert not list((knowledge_root / ".research-kb" / "transactions").glob("*.json"))

    marker_before = (knowledge_root / ".research-kb" / "workspace.json").read_bytes()
    repeated = service.run()
    assert repeated.result == "no_change"
    assert (knowledge_root / ".research-kb" / "workspace.json").read_bytes() == marker_before


def test_runtime_load_requires_matching_marker(tmp_path: Path) -> None:
    config_path = _write_workspace(tmp_path / "workspace")

    with pytest.raises(ResearchKBError) as caught:
        WorkspaceLayout.load(config_path)
    assert caught.value.diagnostic.code == "RKBC-019"

    assert WorkspaceBootstrapService(config_path).run().result == "initialized"
    assert WorkspaceLayout.load(config_path).workspace_id.startswith("workspace_")


def test_conflicting_config_cannot_claim_initialized_root(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    first = _write_workspace(root)
    assert WorkspaceBootstrapService(first).run().result == "initialized"
    second = _write_workspace(
        root,
        workspace_id="workspace_b2222222-2222-4222-8222-222222222222",
        config_name="other-workspace.yaml",
    )

    conflict = WorkspaceBootstrapService(second).run()
    assert conflict.result == "blocked"
    assert conflict.exit_code == 4
    assert {item.code for item in conflict.diagnostics if item.severity == "error"} == {"RKBC-020"}
    marker = read_json_document(root / "knowledge" / ".research-kb" / "workspace.json")
    assert marker["workspace_id"] == "workspace_a1111111-1111-4111-8111-111111111111"


def test_duplicate_and_identical_source_roots_block_before_dictionary_conversion(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    duplicate_ids = [
        {"root_id": "same", "path": "./sources", "read_only_assets": True},
        {"root_id": "same", "path": "./other-sources", "read_only_assets": True},
    ]
    (root / "other-sources").mkdir(parents=True)
    duplicate = _write_workspace(root, source_roots=duplicate_ids)
    result = WorkspaceBootstrapService(duplicate).run(dry_run=True)
    assert result.result == "blocked"
    assert "RKBC-004" in {item.code for item in result.diagnostics}

    identical_paths = [
        {"root_id": "first", "path": "./sources", "read_only_assets": True},
        {"root_id": "second", "path": "./sources/../sources", "read_only_assets": True},
    ]
    same_path_config = _write_workspace(root, source_roots=identical_paths, config_name="same-path.yaml")
    result = WorkspaceBootstrapService(same_path_config).run(dry_run=True)
    assert result.result == "blocked"
    assert "RKBC-021" in {item.code for item in result.diagnostics}


def test_local_inbox_and_nested_roots_emit_redacted_warnings(tmp_path: Path) -> None:
    root = tmp_path / "private-workspace"
    (root / "sources" / "nested").mkdir(parents=True)
    config_path = _write_workspace(
        root,
        local_inbox="./outside-inbox",
        source_roots=[
            {"root_id": "outer", "path": "./sources", "read_only_assets": True},
            {"root_id": "inner", "path": "./sources/nested", "read_only_assets": True},
        ],
    )

    result = WorkspaceBootstrapService(config_path).run(dry_run=True)
    assert result.result == "planned"
    assert result.diagnostics
    rendered = json.dumps([item.to_dict() for item in result.diagnostics])
    assert str(tmp_path) not in rendered
    assert {item.code for item in result.diagnostics} == {"RKBC-023"}


def test_unknown_content_and_file_directory_collision_block_without_mutation(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    config_path = _write_workspace(root)
    knowledge = root / "knowledge"
    knowledge.mkdir()
    (knowledge / "unknown.txt").write_text("do not adopt", encoding="utf-8")
    unknown = WorkspaceBootstrapService(config_path).run()
    assert unknown.result == "blocked"
    assert not (knowledge / ".research-kb").exists()

    (knowledge / "unknown.txt").unlink()
    (knowledge / "registry").write_text("collision", encoding="utf-8")
    collision = WorkspaceBootstrapService(config_path).run()
    assert collision.result == "blocked"
    assert not (knowledge / ".research-kb").exists()


def test_marker_fingerprint_changes_when_domain_profile_changes(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    config_path = _write_workspace(root)
    first = deepcopy(build_workspace_marker(config_path))
    profile_path = root / "domain-profile.yaml"
    profile = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
    profile["domain_profile"]["id"] = "domain-beta"
    profile_path.write_text(yaml.safe_dump(profile, sort_keys=True), encoding="utf-8", newline="\n")

    second = build_workspace_marker(config_path)
    assert first["config_fingerprint"] != second["config_fingerprint"]


def test_marker_fingerprint_ignores_yaml_key_order(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    config_path = _write_workspace(root)
    first = build_workspace_marker(config_path)
    workspace = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config_path.write_text(yaml.safe_dump(workspace, sort_keys=True), encoding="utf-8", newline="\n")
    assert build_workspace_marker(config_path) == first


def test_valid_markerless_m1b_store_is_adopted_without_record_or_source_changes(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    source = layout.source_roots["alpha-sources"] / "study.txt"
    source.write_text("Invented adoption source.\n", encoding="utf-8", newline="\n")
    RegistryService(layout).add(
        root_id="alpha-sources",
        relative_path="study.txt",
        metadata={"fixture_origin": "synthetic_from_scratch"},
        actor="cli",
    )
    source_before = source.read_bytes()
    registry_before = layout.registry_path.read_bytes()
    layout.marker_path.unlink()

    result = WorkspaceBootstrapService(layout.config.path).run()

    assert result.result == "initialized"
    assert layout.marker_path.is_file()
    assert source.read_bytes() == source_before
    assert layout.registry_path.read_bytes() == registry_before


def test_invalid_markerless_bundle_blocks_adoption(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    layout.registry_path.parent.mkdir(parents=True, exist_ok=True)
    layout.registry_path.write_text('{}\n', encoding="utf-8", newline="\n")
    layout.marker_path.unlink()

    result = WorkspaceBootstrapService(layout.config.path).run()

    assert result.result == "blocked"
    assert result.exit_code == 4
    assert not layout.marker_path.exists()


def test_markerless_adoption_rejects_stored_workspace_mismatch(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    GuardianService(layout).check(write_report=True)
    config = yaml.safe_load(layout.config.path.read_text(encoding="utf-8"))
    config["workspace"]["id"] = "workspace_b2222222-2222-4222-8222-222222222222"
    layout.config.path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8", newline="\n")
    layout.marker_path.unlink()

    result = WorkspaceBootstrapService(layout.config.path).run()

    assert result.result == "blocked"
    assert result.exit_code == 4
    assert "RKBC-005" in {item.code for item in result.diagnostics}
    assert not layout.marker_path.exists()


def test_markerless_adoption_rejects_journal_filename_mismatch(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    source = layout.source_roots["alpha-sources"] / "study.txt"
    source.write_text("Invented journal source.\n", encoding="utf-8", newline="\n")
    RegistryService(layout).add(
        root_id="alpha-sources",
        relative_path="study.txt",
        metadata={"fixture_origin": "synthetic_from_scratch"},
        actor="cli",
    )
    journal = next(layout.transactions_root.glob("*.json"))
    journal.rename(layout.transactions_root / "mismatched.json")
    layout.marker_path.unlink()

    result = WorkspaceBootstrapService(layout.config.path).run()

    assert result.result == "blocked"
    assert result.exit_code == 4
    assert "RKBC-021" in {item.code for item in result.diagnostics}
    assert not layout.marker_path.exists()


def test_partial_directory_failure_is_reported_and_rerunnable(tmp_path: Path) -> None:
    config_path = _write_workspace(tmp_path / "workspace")

    def fail_on_parse(path: Path) -> None:
        if path.name == "parse":
            raise OSError("injected")
        path.mkdir(mode=0o700, parents=True, exist_ok=True)

    failed = WorkspaceBootstrapService(config_path, directory_creator=fail_on_parse).run()
    assert failed.result == "blocked"
    assert failed.exit_code == 2
    assert any(item["action"] == "create_directory" for item in failed.managed_actions)
    assert not (config_path.parent / "knowledge" / ".research-kb" / "workspace.json").exists()

    recovered = WorkspaceBootstrapService(config_path).run()
    assert recovered.result == "initialized"


def test_marker_write_failure_leaves_no_temporary_file_and_is_rerunnable(tmp_path: Path) -> None:
    config_path = _write_workspace(tmp_path / "workspace")

    def fail_marker(path: Path, content: bytes, write_id: str) -> None:
        raise OSError("injected")

    failed = WorkspaceBootstrapService(config_path, marker_writer=fail_marker).run()
    marker_parent = config_path.parent / "knowledge" / ".research-kb"
    assert failed.result == "blocked"
    assert failed.exit_code == 2
    assert not (marker_parent / "workspace.json").exists()
    assert not list(marker_parent.glob("*.tmp"))
    assert WorkspaceBootstrapService(config_path).run().result == "initialized"


def test_marker_read_back_mismatch_fails_visibly(tmp_path: Path) -> None:
    config_path = _write_workspace(tmp_path / "workspace")

    def write_wrong_marker(path: Path, content: bytes, write_id: str) -> None:
        atomic_write_bytes(path, b'{}\n', write_id)

    result = WorkspaceBootstrapService(config_path, marker_writer=write_wrong_marker).run()
    assert result.result == "blocked"
    assert result.exit_code == 4
    assert "RKBC-021" in {item.code for item in result.diagnostics}
    assert not list((config_path.parent / "knowledge" / ".research-kb").glob("*.tmp"))


def test_incomplete_transaction_blocks_markerless_adoption(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    source = layout.source_roots["alpha-sources"] / "study.txt"
    source.write_text("Invented transaction source.\n", encoding="utf-8", newline="\n")
    paper, _ = RegistryService(layout).add(
        root_id="alpha-sources",
        relative_path="study.txt",
        metadata={"fixture_origin": "synthetic_from_scratch"},
        actor="cli",
    )
    canonical = layout.registry_path.read_bytes()

    class Crash(BaseException):
        pass

    def crash(phase: str) -> None:
        if phase == "target_replaced":
            raise Crash()

    with pytest.raises(Crash):
        TransactionManager(layout).promote_bytes(
            target=layout.registry_path,
            content=canonical,
            target_store="registry",
            operation="registry_append",
            actor="cli",
            input_refs=[],
            output_refs=[paper["paper_id"]],
            phase_hook=crash,
        )
    layout.marker_path.unlink()

    result = WorkspaceBootstrapService(layout.config.path).run()
    assert result.result == "blocked"
    assert "RKBC-018" in {item.code for item in result.diagnostics}
    assert layout.registry_path.read_bytes() == canonical
    assert not layout.marker_path.exists()


def test_same_config_concurrent_apply_is_initialized_then_no_change(tmp_path: Path) -> None:
    config_path = _write_workspace(tmp_path / "workspace")
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: WorkspaceBootstrapService(config_path).run(), range(2)))
    assert sorted(item.result for item in results) == ["initialized", "no_change"]
    assert all(item.exit_code == 0 for item in results)


def test_lock_file_persistence_retries_transient_concurrent_write(tmp_path: Path, monkeypatch) -> None:
    config_path = _write_workspace(tmp_path / "workspace")
    real_atomic_write = bootstrap_module.atomic_write_bytes
    attempts = {"lock": 0}

    def transient_lock_conflict(target: Path, content: bytes, write_id: str) -> None:
        if target.name == "workspace.lock" and attempts["lock"] == 0:
            attempts["lock"] += 1
            raise PermissionError("injected concurrent lock holder")
        real_atomic_write(target, content, write_id)

    monkeypatch.setattr(bootstrap_module, "atomic_write_bytes", transient_lock_conflict)

    result = WorkspaceBootstrapService(config_path).run()

    assert result.result == "initialized"
    assert result.exit_code == 0
    assert attempts["lock"] == 1
    assert (config_path.parent / "knowledge" / ".research-kb" / "locks" / "workspace.lock").is_file()


def test_conflicting_concurrent_apply_never_replaces_winning_marker(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    first = _write_workspace(root, config_name="first.yaml")
    second = _write_workspace(
        root,
        workspace_id="workspace_b2222222-2222-4222-8222-222222222222",
        config_name="second.yaml",
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda path: WorkspaceBootstrapService(path).run(), (first, second)))
    assert sorted(item.result for item in results) == ["blocked", "initialized"]
    marker = read_json_document(root / "knowledge" / ".research-kb" / "workspace.json")
    assert marker["workspace_id"] in {
        "workspace_a1111111-1111-4111-8111-111111111111",
        "workspace_b2222222-2222-4222-8222-222222222222",
    }


def test_casefolded_managed_name_collision_blocks(tmp_path: Path) -> None:
    config_path = _write_workspace(tmp_path / "workspace")
    knowledge = config_path.parent / "knowledge"
    (knowledge / "Registry").mkdir(parents=True)
    result = WorkspaceBootstrapService(config_path).run()
    assert result.result == "blocked"
    assert "RKBC-021" in {item.code for item in result.diagnostics}
    assert not (knowledge / ".research-kb").exists()


def test_managed_unsafe_link_branch_blocks_before_mutation(tmp_path: Path, monkeypatch) -> None:
    config_path = _write_workspace(tmp_path / "workspace")
    knowledge = config_path.parent / "knowledge"
    (knowledge / "registry").mkdir(parents=True)
    monkeypatch.setattr(
        "research_kb.workspace_validation._is_unsafe_link",
        lambda path: path.name == "registry",
    )
    result = WorkspaceBootstrapService(config_path).run()
    assert result.result == "blocked"
    assert not (knowledge / ".research-kb").exists()


def test_lock_scaffold_stops_if_created_root_becomes_reparse_point(tmp_path: Path, monkeypatch) -> None:
    config_path = _write_workspace(tmp_path / "workspace")
    unsafe = {"active": False}

    def create_then_swap(path: Path) -> None:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        if path.name == "knowledge":
            unsafe["active"] = True

    monkeypatch.setattr(
        bootstrap_module,
        "_is_unsafe_link",
        lambda path: unsafe["active"] and path.name == "knowledge",
        raising=False,
    )

    result = WorkspaceBootstrapService(config_path, directory_creator=create_then_swap).run()

    assert result.result == "blocked"
    assert result.exit_code == 4
    assert not (config_path.parent / "knowledge" / ".research-kb").exists()


def test_physical_source_alias_uses_samefile_not_only_path_text(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "workspace"
    (root / "other-sources").mkdir(parents=True)
    config_path = _write_workspace(
        root,
        source_roots=[
            {"root_id": "first", "path": "./sources", "read_only_assets": True},
            {"root_id": "second", "path": "./other-sources", "read_only_assets": True},
        ],
    )
    real_samefile = workspace_validation_module.os.path.samefile

    def samefile(left, right) -> bool:
        names = {Path(left).name, Path(right).name}
        if names == {"sources", "other-sources"}:
            return True
        return real_samefile(left, right)

    monkeypatch.setattr(workspace_validation_module.os.path, "samefile", samefile)

    result = WorkspaceBootstrapService(config_path).run(dry_run=True)

    assert result.result == "blocked"
    assert "RKBC-021" in {item.code for item in result.diagnostics}


def test_missing_source_root_blocks_without_creating_knowledge_root(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    config_path = _write_workspace(
        root,
        source_roots=[{"root_id": "missing", "path": "./absent", "read_only_assets": True}],
    )
    result = WorkspaceBootstrapService(config_path).run()
    assert result.result == "blocked"
    assert "RKBC-021" in {item.code for item in result.diagnostics}
    assert not (root / "knowledge").exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission contract")
def test_posix_bootstrap_modes_are_private(tmp_path: Path) -> None:
    import stat

    config_path = _write_workspace(tmp_path / "workspace")
    assert WorkspaceBootstrapService(config_path).run().exit_code == 0
    knowledge = config_path.parent / "knowledge"
    assert stat.S_IMODE(knowledge.stat().st_mode) == 0o700
    assert stat.S_IMODE((knowledge / ".research-kb" / "workspace.json").stat().st_mode) == 0o600


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission contract")
def test_unsafe_existing_posix_mode_blocks_without_repair(tmp_path: Path) -> None:
    import stat

    config_path = _write_workspace(tmp_path / "workspace")
    knowledge = config_path.parent / "knowledge"
    knowledge.mkdir(mode=0o755)
    knowledge.chmod(0o755)
    result = WorkspaceBootstrapService(config_path).run()
    assert result.result == "blocked"
    assert "RKBC-022" in {item.code for item in result.diagnostics}
    assert stat.S_IMODE(knowledge.stat().st_mode) == 0o755
