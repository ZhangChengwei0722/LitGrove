from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[2] / "tools" / "sync_portable_skill.py"
SPEC = importlib.util.spec_from_file_location("sync_portable_skill", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
sync_portable_skill = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sync_portable_skill)


def test_sync_tree_normalizes_text_and_removes_only_snapshot_extras(tmp_path: Path) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    (source / "references").mkdir(parents=True)
    (source / "SKILL.md").write_bytes(b"# Skill\r\n")
    (source / "references" / "workflow.md").write_text("workflow\n", encoding="utf-8")
    destination.mkdir()
    (destination / "stale.md").write_text("stale\n", encoding="utf-8")

    result = sync_portable_skill.sync_tree(source, destination)

    assert (destination / "SKILL.md").read_bytes() == b"# Skill\n"
    assert not (destination / "stale.md").exists()
    assert sync_portable_skill.comparison(source, destination)["status"] == "current"
    assert result["file_count"] == 2


def test_snapshot_rejects_source_or_destination_symlink(tmp_path: Path) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    target = tmp_path / "target.md"
    target.write_text("target\n", encoding="utf-8")
    try:
        (source / "linked.md").symlink_to(target)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    with pytest.raises(ValueError, match="symlink"):
        sync_portable_skill.snapshot(source)
