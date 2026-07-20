from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from research_kb.errors import ResearchKBError
from research_kb.mutation import MutationRequest
from research_kb.services.paper_status import PaperStatusService
from research_kb.services.question_mapping import QuestionMappingService
from research_kb.services.records import RecordService
from research_kb.services.registry import RegistryService
from research_kb.storage.json_io import read_json_document, serialize_json
from tests.runtime_helpers import make_runtime_workspace
from tests.unit.test_question_mapping_service import _append_request, _link, _prepare_paper


def _tree_snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _registered_only(tmp_path: Path):
    layout = make_runtime_workspace(tmp_path)
    source = layout.source_roots["alpha-sources"] / "registered-only.txt"
    source.write_text("Invented registered source.\n", encoding="utf-8", newline="\n")
    paper, _ = RegistryService(layout).add(
        root_id="alpha-sources",
        relative_path=source.name,
        metadata={"fixture_origin": "synthetic_from_scratch"},
    )
    return layout, source, paper


def test_registered_only_status_has_explicit_nulls_and_zero_counts(tmp_path: Path) -> None:
    layout, source, paper = _registered_only(tmp_path)
    before = _tree_snapshot(layout.knowledge_root)

    status = PaperStatusService(layout).show(paper_id=paper["paper_id"])

    assert status["status"] == "success"
    assert status["interface_version"] == "1.0"
    assert status["source"] == {
        "registered": True,
        "state": "current",
        "fingerprint_algorithm": "sha256",
    }
    assert status["parse"] == {
        "state": "absent",
        "parse_run_id": None,
        "adapter": None,
        "version": None,
        "page_count": 0,
    }
    assert status["paper_card"] == {
        "present": False,
        "card_status": None,
        "review_status": None,
        "unit_count": 0,
        "grounding_counts": {
            "grounded": 0,
            "revised": 0,
            "interpretive": 0,
            "background_only": 0,
            "needs_resolution": 0,
        },
    }
    assert status["evidence"] == {"count": 0}
    assert status["review_queue"]["count"] == 0
    assert status["question_mappings"] == {"linked_count": 0, "items": []}
    assert status["integrity"] == {
        "guardian_status": "success",
        "paper_finding_codes": [],
        "workspace_finding_count": 0,
        "incomplete_transaction_count": 0,
        "needs_resolution_transaction_count": 0,
        "mutation_safe": True,
    }
    assert source.is_file()
    assert _tree_snapshot(layout.knowledge_root) == before


def test_status_projects_card_evidence_queue_and_mapping_without_scientific_payload(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    prepared = _prepare_paper(layout, "status-complete.txt")
    mapping, _ = QuestionMappingService(layout).promote(
        _append_request([_link(prepared)]),
        actor="agent",
    )
    paper = prepared["paper"]

    status = PaperStatusService(layout).show(paper_id=paper["paper_id"])

    assert status["parse"]["state"] == "current"
    assert status["parse"]["page_count"] == 1
    assert status["paper_card"]["present"] is True
    assert status["paper_card"]["card_status"] == "calibrated"
    assert status["paper_card"]["unit_count"] == 3
    assert status["paper_card"]["grounding_counts"] == {
        "grounded": 1,
        "revised": 0,
        "interpretive": 1,
        "background_only": 0,
        "needs_resolution": 1,
    }
    assert status["evidence"] == {"count": 1}
    assert status["review_queue"] == {
        "count": 1,
        "resolution_counts": {
            "needs_resolution": 1,
            "resolved_by_narrowing": 0,
            "needs_source_reopen": 0,
        },
    }
    assert status["question_mappings"] == {
        "linked_count": 1,
        "items": [
            {
                "question_id": mapping["question_id"],
                "mapping_status": "ai_draft",
                "freshness": "current",
            }
        ],
    }
    serialized = json.dumps(status)
    assert "Invented" not in serialized
    assert str(tmp_path) not in serialized


def test_status_reports_stale_mapping_warning_without_blocking_storage_mutation(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    prepared = _prepare_paper(layout, "status-stale.txt")
    mapping, _ = QuestionMappingService(layout).promote(
        _append_request([_link(prepared)]),
        actor="agent",
    )
    time.sleep(0.002)
    RecordService(layout).promote(
        MutationRequest(
            operation="replace",
            record_kind="review-queue",
            target_record_id=prepared["queue"]["queue_id"],
            paper_id=prepared["paper"]["paper_id"],
            payload={
                "reason": "The revised synthetic boundary remains narrow.",
                "resolution_status": "resolved_by_narrowing",
            },
        ),
        actor="agent",
    )

    status = PaperStatusService(layout).show(paper_id=prepared["paper"]["paper_id"])

    assert status["question_mappings"]["items"] == [
        {
            "question_id": mapping["question_id"],
            "mapping_status": "ai_draft",
            "freshness": "stale",
        }
    ]
    assert status["integrity"]["guardian_status"] == "warning"
    assert status["integrity"]["paper_finding_codes"] == ["RKBC-014"]
    assert status["integrity"]["mutation_safe"] is True


@pytest.mark.parametrize(
    ("source_action", "expected_state"),
    [
        ("missing", "missing"),
        ("mismatch", "fingerprint_mismatch"),
        ("directory", "not_regular_file"),
    ],
)
def test_status_reports_stable_non_current_source(
    tmp_path: Path,
    source_action: str,
    expected_state: str,
) -> None:
    layout = make_runtime_workspace(tmp_path)
    prepared = _prepare_paper(layout, "source-state.txt")
    paper = prepared["paper"]
    source = layout.source_roots["alpha-sources"] / "source-state.txt"
    if source_action == "missing":
        source.unlink()
    elif source_action == "mismatch":
        source.write_text("Changed synthetic source.\n", encoding="utf-8", newline="\n")
    else:
        source.unlink()
        source.mkdir()

    status = PaperStatusService(layout).show(paper_id=paper["paper_id"])

    assert status["source"]["state"] == expected_state
    assert status["parse"]["state"] == "source_stale"
    assert status["integrity"]["guardian_status"] == "failure"
    assert "RKBC-009" in status["integrity"]["paper_finding_codes"]
    assert status["integrity"]["mutation_safe"] is False


@pytest.mark.parametrize(
    ("phase", "result", "incomplete", "needs_resolution"),
    [
        ("prepared", None, 1, 0),
        ("needs_resolution", "needs_resolution", 0, 1),
    ],
)
def test_status_counts_transaction_safety_states(
    tmp_path: Path,
    phase: str,
    result: str | None,
    incomplete: int,
    needs_resolution: int,
) -> None:
    layout, _, paper = _registered_only(tmp_path)
    journal_path = next(layout.transactions_root.glob("*.json"))
    journal = read_json_document(journal_path, record_kind="transaction-journal")
    journal["phase"] = phase
    journal["result"] = result
    journal_path.write_bytes(serialize_json(journal))

    status = PaperStatusService(layout).show(paper_id=paper["paper_id"])

    assert status["integrity"]["incomplete_transaction_count"] == incomplete
    assert status["integrity"]["needs_resolution_transaction_count"] == needs_resolution
    assert status["integrity"]["guardian_status"] == "failure"
    assert status["integrity"]["mutation_safe"] is False


def test_unrelated_paper_finding_is_counted_without_leaking_its_id(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    first = _prepare_paper(layout, "selected.txt")["paper"]
    second = _prepare_paper(layout, "unrelated.txt")["paper"]
    second_source = layout.source_roots["alpha-sources"] / "unrelated.txt"
    second_source.write_text("Changed unrelated source.\n", encoding="utf-8", newline="\n")

    status = PaperStatusService(layout).show(paper_id=first["paper_id"])

    assert status["integrity"]["paper_finding_codes"] == []
    assert status["integrity"]["workspace_finding_count"] == 1
    assert second["paper_id"] not in json.dumps(status)
    assert status["integrity"]["mutation_safe"] is False


def test_status_rejects_unknown_paper_and_source_change_during_projection(tmp_path: Path, monkeypatch) -> None:
    layout, _, paper = _registered_only(tmp_path)
    service = PaperStatusService(layout)
    with pytest.raises(ResearchKBError) as caught:
        service.show(paper_id="paper_b2222222-2222-4222-8222-222222222222")
    assert caught.value.diagnostic.code == "RKBC-005"

    original_observe = service._observe_source
    observations = iter((original_observe(paper), ("missing", None)))
    monkeypatch.setattr(service, "_observe_source", lambda _: next(observations))
    with pytest.raises(ResearchKBError) as caught:
        service.show(paper_id=paper["paper_id"])
    assert caught.value.diagnostic.code == "RKBC-009"
