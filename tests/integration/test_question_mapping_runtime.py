from __future__ import annotations

import json
from pathlib import Path

import pytest

from research_kb.cli import main
from research_kb.guardian import GuardianService
from research_kb.mutation import MutationRequest
from research_kb.services.question_mapping import QuestionMappingService
from research_kb.services.records import RecordService
from research_kb.storage.json_io import file_sha256, read_jsonl
from tests.runtime_helpers import make_runtime_workspace
from tests.unit.test_question_mapping_service import _append_request, _link, _prepare_paper


@pytest.mark.parametrize("domain", ["alpha", "beta"])
def test_two_domains_promote_and_read_question_mappings_without_source_changes(
    tmp_path: Path,
    capsys,
    domain: str,
) -> None:
    layout = make_runtime_workspace(tmp_path, domain=domain)
    first = _prepare_paper(layout, "first-question-source.txt")
    second = _prepare_paper(layout, "second-question-source.txt")
    source_hashes = {
        path.name: file_sha256(path)
        for path in next(iter(layout.source_roots.values())).glob("*.txt")
    }
    request = _append_request([_link(second), _link(first)])
    request_path = tmp_path / f"{domain}-question-request.json"
    request_path.write_text(
        json.dumps(
            {
                "contract_version": "1.0",
                "operation": request.operation,
                "record_kind": request.record_kind,
                "target_record_id": request.target_record_id,
                "context": {
                    "paper_id": request.paper_id,
                    "question_origin": request.question_origin,
                },
                "payload": request.payload,
                "fixture_origin": request.fixture_origin,
            }
        ),
        encoding="utf-8",
        newline="\n",
    )

    assert main([
        "record", "promote", "--workspace", str(layout.config.path),
        "--request", str(request_path), "--actor", "agent",
    ]) == 0
    first_output = json.loads(capsys.readouterr().out)
    assert first_output["record_kind"] == "question-mapping"
    assert first_output["target"] == "questions/mappings.jsonl"

    second_request = _append_request([_link(first, "interpretive_unit")])
    second_request.payload["question_text"] = "What interpretation remains for the fabricated response?"
    second_mapping, _ = QuestionMappingService(layout).promote(second_request, actor="agent")

    mappings = read_jsonl(
        layout.question_mappings_path,
        record_kind="question-mapping",
        id_field="question_id",
    )
    assert len(mappings) == 2
    assert all(item["fixture_origin"] == "synthetic_from_scratch" for item in mappings)
    first_mapping = next(item for item in mappings if item["question_id"] == first_output["record_id"])
    assert len(first_mapping["paper_links"]) == 2
    assert second_mapping["paper_links"][0]["selected_card_unit_ids"] == [
        first["interpretive_unit"]["unit_id"]
    ]
    assert second_mapping["paper_links"][0]["evidence_ids"] == []

    assert main(["question", "list", "--workspace", str(layout.config.path)]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert [item["question_id"] for item in listed["questions"]] == sorted(
        item["question_id"] for item in mappings
    )
    before_render = {
        path.relative_to(layout.knowledge_root).as_posix(): path.read_bytes()
        for path in layout.knowledge_root.rglob("*")
        if path.is_file()
    }
    assert main([
        "question", "render", "--workspace", str(layout.config.path),
        "--question-id", first_mapping["question_id"],
    ]) == 0
    current_view = capsys.readouterr()
    assert current_view.err == ""
    assert 'freshness_status: "current"' in current_view.out
    assert f'`{first["grounded_unit"]["unit_id"]}`' in current_view.out
    assert f'`{first["evidence"]["evidence_id"]}`' in current_view.out
    assert (
        "> Invented response increased for first\\-question\\-source\\.txt\\."
        in current_view.out
    )
    assert "PDF Page: 1; Section: Results" in current_view.out
    assert "- Locator: `page:1:block:1`" in current_view.out
    assert f'#### Boundary `{first["queue"]["queue_id"]}`' in current_view.out
    assert "These records are risk and unresolved-context boundaries. They are not evidence." in current_view.out
    assert {
        path.relative_to(layout.knowledge_root).as_posix(): path.read_bytes()
        for path in layout.knowledge_root.rglob("*")
        if path.is_file()
    } == before_render

    mapping_bytes = layout.question_mappings_path.read_bytes()
    RecordService(layout).promote(
        MutationRequest(
            operation="replace",
            record_kind="review-queue",
            target_record_id=first["queue"]["queue_id"],
            paper_id=first["paper"]["paper_id"],
            payload={"reason": "The narrowed invented boundary remains limited to one case."},
        ),
        actor="agent",
    )
    assert layout.question_mappings_path.read_bytes() == mapping_bytes
    before_stale_render = {
        path.relative_to(layout.knowledge_root).as_posix(): path.read_bytes()
        for path in layout.knowledge_root.rglob("*")
        if path.is_file()
    }
    assert main([
        "question", "render", "--workspace", str(layout.config.path),
        "--question-id", first_mapping["question_id"],
    ]) == 0
    stale_view = capsys.readouterr()
    assert stale_view.err == ""
    assert 'freshness_status: "stale"' in stale_view.out
    assert "`RKBC-014`" in stale_view.out
    assert {
        path.relative_to(layout.knowledge_root).as_posix(): path.read_bytes()
        for path in layout.knowledge_root.rglob("*")
        if path.is_file()
    } == before_stale_render
    guardian = GuardianService(layout).check().report
    assert guardian["status"] == "warning"
    assert "RKBC-014" in {finding["code"] for finding in guardian["findings"]}
    assert {
        path.name: file_sha256(path)
        for path in next(iter(layout.source_roots.values())).glob("*.txt")
    } == source_hashes
    assert not (layout.knowledge_root / "views").exists()
    assert not (layout.knowledge_root / "step7").exists()
