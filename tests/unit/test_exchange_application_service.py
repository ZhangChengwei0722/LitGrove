from __future__ import annotations

import json
import shutil
from pathlib import Path

from research_kb.cli import main
from research_kb.services.exchange_application import ExchangeApplicationService
from research_kb.services.bootstrap import WorkspaceBootstrapService
from research_kb.services.workspace_session import WorkspaceSessionService


ROOT = Path(__file__).resolve().parents[2]


def test_exchange_application_service_is_session_bound_and_source_free(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    shutil.copytree(ROOT / "tests" / "fixtures" / "p2_small" / "workspace", workspace)
    assert WorkspaceBootstrapService(workspace / "workspace.yaml").run().exit_code == 0
    session = WorkspaceSessionService({"synthetic": workspace / "workspace.yaml"}).open("synthetic")
    service = ExchangeApplicationService()

    limits = service.limits(session)
    preview = service.preview_export(session, {"scope": "workspace", "include_sources": False})

    assert limits["selectors"] == ["paper", "question", "direction", "workspace"]
    assert limits["source_inclusion_available"] is True
    assert limits["import_available"] is True
    assert limits["safe_reader_profile"]["profile_id"] == "p10-exchange-safe-reader-v1"
    assert preview["application_service_interface_version"] == "1.23"
    assert preview["persistent_writes"] == 0
    assert preview["canonical_scientific_write"] is False
    assert callable(service.show_import)


def test_exchange_cli_is_thin_application_service_adapter(tmp_path: Path, capsys) -> None:
    workspace = tmp_path / "workspace"
    shutil.copytree(ROOT / "tests" / "fixtures" / "p2_small" / "workspace", workspace)
    assert WorkspaceBootstrapService(workspace / "workspace.yaml").run().exit_code == 0
    request_path = tmp_path / "preview.json"
    request_path.write_text('{"scope":"workspace","include_sources":false}\n', encoding="utf-8")

    assert main([
        "exchange", "export-preview",
        "--workspace", str(workspace / "workspace.yaml"),
        "--request", str(request_path),
    ]) == 0
    preview = json.loads(capsys.readouterr().out)
    build = {
        "scope": "workspace",
        "include_sources": False,
        "expected_basis_digest": preview["basis_digest"],
        "export_id": preview["export_id"],
        "created_at": preview["created_at"],
    }
    build_path = tmp_path / "build.json"
    build_path.write_text(json.dumps(build) + "\n", encoding="utf-8")
    output = tmp_path / "cli.rkb-exchange.zip"

    assert main([
        "exchange", "export",
        "--workspace", str(workspace / "workspace.yaml"),
        "--request", str(build_path),
        "--output", str(output),
        "--actor", "user",
    ]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["result"] == "created"
    assert result["archive_bytes"] == output.stat().st_size
    assert result["canonical_scientific_write"] is False
