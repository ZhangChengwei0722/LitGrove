from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path

import pytest

from research_kb.errors import ResearchKBError
from research_kb.catalog.models import canonical_digest
from research_kb.exchange import ExchangeExportService
from research_kb.services.bootstrap import WorkspaceBootstrapService
from research_kb.storage.json_io import serialize_json
from research_kb.workspace import WorkspaceLayout


ROOT = Path(__file__).resolve().parents[2]
PAPER_ID = "paper_f8daed20-fcf0-4ed8-9795-694bd631def9"
QUESTION_ID = "question_272dfde3-ef0f-4205-b9a1-65623487637d"
DIRECTION_ID = "direction_a1111111-1111-4111-8111-111111111111"
EXPORT_ID = "export_a1111111-1111-4111-8111-111111111111"
CREATED_AT = "2026-08-04T00:00:00Z"


def _layout(tmp_path: Path) -> WorkspaceLayout:
    target = tmp_path / "workspace"
    shutil.copytree(ROOT / "tests" / "fixtures" / "p2_small" / "workspace", target)
    config = target / "workspace.yaml"
    assert WorkspaceBootstrapService(config).run().exit_code == 0
    return WorkspaceLayout.load(config)


def _request(scope: str, selector_id: str | None = None) -> dict:
    result = {"scope": scope, "include_sources": False}
    if selector_id is not None:
        result["selector_id"] = selector_id
    return result


def _build_request(preview: dict) -> dict:
    return {
        **preview["selection"],
        "include_sources": False,
        "expected_basis_digest": preview["basis_digest"],
        "export_id": EXPORT_ID,
        "created_at": CREATED_AT,
    }


def _archive_records(path: Path) -> list[dict]:
    with zipfile.ZipFile(path) as archive:
        records = []
        for name in archive.namelist():
            if not name.startswith("records/"):
                continue
            records.extend(json.loads(line) for line in archive.read(name).decode("utf-8").splitlines())
        return records


def test_paper_and_question_closure_are_explicit_and_path_free(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    service = ExchangeExportService(layout)

    paper = service.preview(_request("paper", PAPER_ID))
    question = service.preview(_request("question", QUESTION_ID))

    assert paper["selection"] == {"scope": "paper", "selector_id": PAPER_ID}
    assert paper["record_kind_counts"] == {
        "evidence": 1,
        "exchange-paper-identity": 1,
        "paper-card": 1,
    }
    assert question["record_kind_counts"] == {
        "evidence": 2,
        "exchange-paper-identity": 2,
        "paper-card": 2,
        "question-mapping": 1,
        "step7-cross-view": 1,
        "step7-insight": 1,
        "step7-review-angle": 1,
        "step7-synthesis": 1,
    }
    assert "process-event" not in question["record_kind_counts"]
    assert "parsed-page" not in question["record_kind_counts"]

    archive = tmp_path / "question.rkb-exchange.zip"
    service.build(_build_request(question), target=archive, actor="user")
    records = _archive_records(archive)
    identity = next(item for item in records if item["record_kind"] == "exchange-paper-identity")
    assert "source_ref" not in identity["record"]
    assert identity["record"]["source_fingerprint"]["algorithm"] == "sha256"
    assert not any("relative_path" in json.dumps(item, sort_keys=True) for item in records)


def test_direction_and_workspace_selectors_use_closed_allowlist(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    direction = _direction_bundle()
    path = layout.direction_bundle_path(DIRECTION_ID)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(serialize_json(direction))
    service = ExchangeExportService(layout)

    selected = service.preview(_request("direction", DIRECTION_ID))
    workspace = service.preview(_request("workspace"))

    assert selected["record_kind_counts"] == {"direction-bundle": 1}
    assert workspace["record_count"] > selected["record_count"]
    assert "direction-bundle" in workspace["record_kind_counts"]
    assert "process-event" not in workspace["record_kind_counts"]
    assert "guardian-report" not in workspace["record_kind_counts"]
    assert "parsed-page" not in workspace["record_kind_counts"]


def test_repeated_build_from_same_basis_is_byte_identical_and_create_only(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    service = ExchangeExportService(layout)
    preview = service.preview(_request("workspace"))
    request = _build_request(preview)
    first = tmp_path / "first.rkb-exchange.zip"
    second = tmp_path / "second.rkb-exchange.zip"

    first_result = service.build(request, target=first, actor="user")
    second_result = service.build(request, target=second, actor="user")

    assert first.read_bytes() == second.read_bytes()
    assert first_result["archive_sha256"] == second_result["archive_sha256"]
    local_receipt = json.loads(layout.exchange_export_receipt_path(first_result["export_id"]).read_text(encoding="utf-8"))
    assert local_receipt["archive_sha256"] == first_result["archive_sha256"]
    assert local_receipt["selection"] == {"scope": "workspace"}
    with zipfile.ZipFile(first) as archive:
        assert archive.namelist() == sorted(archive.namelist())
        assert all(item.compress_type == zipfile.ZIP_STORED for item in archive.infolist())
        manifest = json.loads(archive.read("manifest.json"))
        receipt = json.loads(archive.read("receipt.json"))
    assert manifest["bundle_format"] == "research-kb-exchange-bundle@1.0"
    assert receipt["manifest_sha256"] == first_result["manifest_sha256"]

    with pytest.raises(ResearchKBError) as error:
        service.build(request, target=first, actor="user")
    assert error.value.diagnostic.code == "RKBC-017"


def test_changed_workspace_rejects_stale_preview_basis(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    service = ExchangeExportService(layout)
    preview = service.preview(_request("paper", PAPER_ID))
    card_path = layout.paper_card_path(PAPER_ID)
    card = json.loads(card_path.read_text(encoding="utf-8"))
    card["updated_at"] = "2026-08-04T00:00:01Z"
    card_path.write_bytes(serialize_json(card))

    with pytest.raises(ResearchKBError) as error:
        service.build(_build_request(preview), target=tmp_path / "stale.rkb-exchange.zip", actor="user")
    assert error.value.diagnostic.code == "RKBC-026"
    assert not (tmp_path / "stale.rkb-exchange.zip").exists()


def test_preview_rejects_unknown_selector_and_source_inclusion_in_delivery_a(tmp_path: Path) -> None:
    service = ExchangeExportService(_layout(tmp_path))

    with pytest.raises(ResearchKBError):
        service.preview(_request("paper", "paper_a1111111-1111-4111-8111-111111111111"))
    with pytest.raises(ResearchKBError) as error:
        service.preview({"scope": "workspace", "include_sources": True})
    assert error.value.diagnostic.code == "RKBC-006"


def test_preview_rejects_absolute_path_hidden_in_exportable_metadata(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    records = [json.loads(line) for line in layout.registry_path.read_text(encoding="utf-8").splitlines()]
    paper = next(item for item in records if item["paper_id"] == PAPER_ID)
    paper["bibliography"]["title"] = "Leaked C:" + chr(92) + "Users" + chr(92) + "private" + chr(92) + "paper.pdf"
    layout.registry_path.write_text(
        "".join(json.dumps(item, sort_keys=True, separators=(",", ":")) + "\n" for item in records),
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(ResearchKBError) as error:
        ExchangeExportService(layout).preview(_request("paper", PAPER_ID))
    assert error.value.diagnostic.code == "RKBC-012"


def _direction_bundle() -> dict:
    direction = {
        "schema_version": "1.0",
        "direction_id": DIRECTION_ID,
        "name": "Synthetic direction",
        "scope": "Synthetic exchange selector coverage.",
        "status": "active",
        "links": [],
        "gap_notes": [],
    }
    revision = {
        "revision_id": "orgrev_a1111111-1111-4111-8111-111111111111",
        "revision_number": 1,
        "predecessor": None,
        "content_digest": canonical_digest(direction),
        "approval": {
            "receipt_id": "synthetic-receipt",
            "approved_by": "user",
            "approved_at": CREATED_AT,
            "origin": "user_authored",
        },
        "direction": direction,
        "created_at": CREATED_AT,
    }
    return {
        "schema_version": "1.0",
        "direction_id": DIRECTION_ID,
        "active_revision_id": revision["revision_id"],
        "revisions": [revision],
        "created_at": CREATED_AT,
        "updated_at": CREATED_AT,
        "fixture_origin": "synthetic_from_scratch",
    }
