from __future__ import annotations

import json
from pathlib import Path

from research_kb.cli import main
from research_kb.storage.json_io import serialize_json
from tests.runtime_helpers import make_runtime_workspace


def _write(path: Path, value: dict) -> Path:
    path.write_bytes(serialize_json(value))
    return path


def test_backup_cli_preview_create_inspect_and_restore(tmp_path: Path, capsys) -> None:
    layout = make_runtime_workspace(tmp_path)
    preview_request = _write(tmp_path / "preview.json", {"include_sources": False})
    assert main(["backup", "preview", "--workspace", str(layout.config.path), "--request", str(preview_request)]) == 0
    preview = json.loads(capsys.readouterr().out)
    archive = tmp_path / "cli.rkb-backup.zip"
    create_request = _write(
        tmp_path / "create.json",
        {
            "backup_id": "backup_a1111111-1111-4111-8111-111111111111",
            "include_sources": False,
            "expected_basis_digest": preview["basis_digest"],
            "created_at": "2026-08-04T00:00:00Z",
        },
    )
    assert main([
        "backup", "create", "--workspace", str(layout.config.path), "--request", str(create_request),
        "--output", str(archive), "--actor", "user",
    ]) == 0
    created = json.loads(capsys.readouterr().out)
    assert main(["backup", "inspect", "--archive", str(archive)]) == 0
    inspection = json.loads(capsys.readouterr().out)
    assert inspection["archive_sha256"] == created["archive_sha256"]

    restore_request = _write(
        tmp_path / "restore.json",
        {
            "restore_id": "restore_a1111111-1111-4111-8111-111111111111",
            "expected_archive_sha256": inspection["archive_sha256"],
            "source_root_mappings": {"alpha-sources": str(layout.source_roots["alpha-sources"])},
            "created_at": "2026-08-04T00:00:00Z",
        },
    )
    assert main([
        "backup", "restore", "--archive", str(archive), "--request", str(restore_request),
        "--target-root", str(tmp_path / "restored-cli"), "--actor", "user",
    ]) == 0
    restored = json.loads(capsys.readouterr().out)
    assert restored["result"] == "restored"


def test_maintenance_cli_lists_and_coalesces_explicit_work(tmp_path: Path, capsys) -> None:
    layout = make_runtime_workspace(tmp_path)
    request = _write(
        tmp_path / "maintenance.json",
        {
            "triggers": [
                {
                    "dependent_id": "question_a1111111-1111-4111-8111-111111111111",
                    "upstream_revision": "primaryrev_a1111111-1111-4111-8111-111111111111",
                    "reason": "upstream_revised",
                    "trigger_ref": "event_a1111111-1111-4111-8111-111111111111",
                }
            ]
        },
    )
    assert main([
        "maintenance", "enqueue", "--workspace", str(layout.config.path), "--request", str(request),
        "--actor", "cli",
    ]) == 0
    assert json.loads(capsys.readouterr().out)["open_count"] == 1
    assert main(["maintenance", "list", "--workspace", str(layout.config.path)]) == 0
    assert len(json.loads(capsys.readouterr().out)["items"]) == 1
