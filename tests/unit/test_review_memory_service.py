from copy import deepcopy
from pathlib import Path

import pytest

from research_kb.errors import ResearchKBError
from research_kb.mutation import MutationRequest
from research_kb.parse.synthetic_text import SyntheticTextAdapter
from research_kb.process_events import read_process_events
from research_kb.services.parse import ParseService
from research_kb.services.records import RecordService
from research_kb.services.registry import RegistryService
from research_kb.storage.json_io import read_json_document, read_jsonl
from tests.runtime_helpers import make_runtime_workspace


REVIEW_SECTIONS = (
    "review_objective_scope",
    "review_question_search_boundaries",
    "taxonomy_field_structure",
    "major_synthesis",
    "methods_metrics_guardrails",
    "gaps_frontiers",
    "primary_leads_reuse",
)


def prepare_review_paper(layout, name: str = "review.txt") -> tuple[dict, list[dict]]:
    source = layout.source_roots["alpha-sources"] / name
    source.write_text(
        "The fabricated review separates two invented response classes.\n",
        encoding="utf-8",
        newline="\n",
    )
    paper, _ = RegistryService(layout).add(
        root_id="alpha-sources",
        relative_path=name,
        metadata={
            "bibliography": {"title": "Fabricated review"},
            "fixture_origin": "synthetic_from_scratch",
        },
    )
    pages, _ = ParseService(layout).run(paper_id=paper["paper_id"], adapter=SyntheticTextAdapter())
    return paper, pages


def review_payload(*, with_unit: bool = True) -> dict:
    sections = [{"section_id": section_id, "units": []} for section_id in REVIEW_SECTIONS]
    if with_unit:
        sections[2]["units"].append(
            {
                "section_id": REVIEW_SECTIONS[2],
                "unit_type": "field_axis",
                "content": "Separate the two fabricated response classes during later reading.",
                "source_notes": [
                    {
                        "pdf_page": 1,
                        "printed_page": None,
                        "section": "Synthetic taxonomy",
                        "figure_or_table": None,
                        "note_type": "paraphrase",
                        "text": "The fabricated taxonomy contains two response classes.",
                        "locator": None,
                        "reopen_priority": "high",
                    }
                ],
                "workflow_impacts": [
                    {
                        "target": "primary_paper_reading",
                        "action": "Separate the two fabricated response classes during later reading.",
                    }
                ],
                "evidence_use": {
                    "can_support_canonical_evidence": False,
                    "can_guide_primary_grounding": True,
                    "primary_grounding_required_before": ["comparative_claim"],
                },
                "reuse_quality": {
                    "reuse_confidence": "medium",
                    "staleness_risk": "low",
                    "reason": "The taxonomy is explicit in the synthetic source.",
                },
                "primary_paper_lead": None,
            }
        )
    return {
        "review_subtype": "narrative_review",
        "review_subtype_source": "agent_high_confidence",
        "review_subtype_reason": "The synthetic document explicitly presents a secondary synthesis.",
        "read_status": "targeted_read",
        "scope_tags": ["synthetic_review"],
        "one_sentence_reuse_value": "Provides a fabricated taxonomy for later reading.",
        "memory_value": {
            "status": "reusable" if with_unit else "low_value",
            "reason": "One actionable taxonomy is retained." if with_unit else "No reusable material remains.",
        },
        "coverage_limits": {
            "unread_sections": [],
            "weakly_read_sections": [],
            "reason": "The complete synthetic source was inspected.",
        },
        "sections": sections,
        "non_reusable_notes": [],
        "review_status": "ai_checked",
        "fixture_origin": "synthetic_from_scratch",
    }


def review_request(paper_id: str, *, payload: dict | None = None) -> MutationRequest:
    return MutationRequest(
        operation="append",
        record_kind="review-memory",
        target_record_id=None,
        paper_id=paper_id,
        payload=payload or review_payload(),
        fixture_origin="synthetic_from_scratch",
    )


def test_review_memory_append_injects_owned_ids_boundaries_and_snapshot(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    paper, pages = prepare_review_paper(layout)

    memory, transaction = RecordService(layout).promote(review_request(paper["paper_id"]), actor="agent")

    unit = memory["sections"][2]["units"][0]
    assert memory["review_memory_id"].startswith("reviewmem_")
    assert unit["review_unit_id"].startswith("reviewunit_")
    assert memory["source_type"] == "review"
    assert memory["source_fingerprint"] == paper["source_fingerprint"]
    assert memory["parse_snapshot"] == {
        "parse_run_id": pages[0]["parse_run_id"],
        "adapter": pages[0]["parser"]["adapter"],
        "version": pages[0]["parser"]["version"],
    }
    for value in (memory, unit):
        assert value["background_only"] is True
        assert value["can_enter_canonical_evidence"] is False
        assert value["not_fact"] is True
    assert memory["automation_status"] == "passed_auto_checks"
    assert read_json_document(layout.review_memory_path(paper["paper_id"]), record_kind="review-memory") == memory
    journal = read_json_document(layout.journal_path(transaction.event_id), record_kind="transaction-journal")
    assert journal["target_store"] == "review_memories"
    assert journal["input_refs"] == [paper["paper_id"], pages[0]["parse_run_id"]]
    assert journal["output_refs"] == [memory["review_memory_id"]]


def test_second_review_memory_is_rejected_without_changing_target(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    paper, _ = prepare_review_paper(layout)
    service = RecordService(layout)
    service.promote(review_request(paper["paper_id"]), actor="agent")
    target = layout.review_memory_path(paper["paper_id"])
    before = target.read_bytes()

    with pytest.raises(ResearchKBError) as caught:
        service.promote(review_request(paper["paper_id"]), actor="agent")

    assert caught.value.diagnostic.code == "RKBC-031"
    assert target.read_bytes() == before

    source = layout.source_roots["alpha-sources"] / "review.txt"
    source.write_text("Changed after the existing memory.\n", encoding="utf-8", newline="\n")
    with pytest.raises(ResearchKBError) as stale_duplicate:
        service.promote(review_request(paper["paper_id"]), actor="agent")
    assert stale_duplicate.value.diagnostic.code == "RKBC-031"


def test_review_replace_preserves_known_ids_and_allocates_new_unit(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    paper, pages = prepare_review_paper(layout)
    service = RecordService(layout)
    original, _ = service.promote(review_request(paper["paper_id"]), actor="agent")
    sections = deepcopy(original["sections"])
    original_unit_id = sections[2]["units"][0]["review_unit_id"]
    new_unit = deepcopy(sections[2]["units"][0])
    new_unit.pop("review_unit_id")
    new_unit["content"] = "Use a fabricated metric guardrail during later extraction."
    new_unit["unit_type"] = "method_guardrail"
    new_unit["section_id"] = REVIEW_SECTIONS[4]
    new_unit["workflow_impacts"][0]["action"] = "Check the fabricated metric before comparison."
    sections[4]["units"].append(new_unit)

    replaced, _ = service.promote(
        MutationRequest(
            operation="replace",
            record_kind="review-memory",
            target_record_id=original["review_memory_id"],
            paper_id=paper["paper_id"],
            payload={"sections": sections},
        ),
        actor="agent",
    )

    assert replaced["review_memory_id"] == original["review_memory_id"]
    assert replaced["created_at"] == original["created_at"]
    assert replaced["sections"][2]["units"][0]["review_unit_id"] == original_unit_id
    assert replaced["sections"][4]["units"][0]["review_unit_id"].startswith("reviewunit_")
    assert replaced["parse_snapshot"]["parse_run_id"] == pages[0]["parse_run_id"]


def test_review_service_rejects_caller_owned_ids_and_boundaries(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    paper, _ = prepare_review_paper(layout)
    payload = review_payload()
    payload["review_memory_id"] = "reviewmem_a1111111-1111-4111-8111-111111111111"
    payload["sections"][2]["units"][0]["background_only"] = True

    with pytest.raises(ResearchKBError) as caught:
        RecordService(layout).promote(review_request(paper["paper_id"], payload=payload), actor="agent")

    assert caught.value.diagnostic.code == "RKBC-006"
    assert not layout.review_memory_path(paper["paper_id"]).exists()


def test_primary_and_review_promotion_routes_reject_each_other(tmp_path: Path) -> None:
    review_root = tmp_path / "review"
    review_root.mkdir()
    review_layout = make_runtime_workspace(review_root)
    review_paper, _ = prepare_review_paper(review_layout)
    review_records = RecordService(review_layout)
    review_records.promote(review_request(review_paper["paper_id"]), actor="agent")

    with pytest.raises(ResearchKBError) as caught_primary:
        review_records.promote(_evidence_request(review_paper["paper_id"]), actor="agent")

    primary_root = tmp_path / "primary"
    primary_root.mkdir()
    primary_layout = make_runtime_workspace(primary_root)
    primary_paper, _ = prepare_review_paper(primary_layout)
    primary_records = RecordService(primary_layout)
    primary_records.promote(_evidence_request(primary_paper["paper_id"]), actor="agent")

    with pytest.raises(ResearchKBError) as caught_review:
        primary_records.promote(review_request(primary_paper["paper_id"]), actor="agent")

    assert caught_primary.value.diagnostic.code == "RKBC-009"
    assert caught_review.value.diagnostic.code == "RKBC-009"


def test_review_promotion_requires_current_source_and_parse(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    paper, _ = prepare_review_paper(layout)
    target = layout.review_memory_path(paper["paper_id"])
    source = layout.source_roots["alpha-sources"] / "review.txt"
    source.write_text("Changed fabricated source.\n", encoding="utf-8", newline="\n")

    with pytest.raises(ResearchKBError) as caught:
        RecordService(layout).promote(review_request(paper["paper_id"]), actor="agent")

    assert caught.value.diagnostic.code == "RKBC-009"
    assert not target.exists()


def test_review_promotion_requires_an_active_parse(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    source = layout.source_roots["alpha-sources"] / "unparsed-review.txt"
    source.write_text("Fabricated unparsed review.\n", encoding="utf-8", newline="\n")
    paper, _ = RegistryService(layout).add(
        root_id="alpha-sources",
        relative_path=source.name,
        metadata={"fixture_origin": "synthetic_from_scratch"},
    )

    with pytest.raises(ResearchKBError) as caught:
        RecordService(layout).promote(review_request(paper["paper_id"]), actor="agent")

    assert caught.value.diagnostic.code == "RKBC-005"
    assert not layout.review_memory_path(paper["paper_id"]).exists()


def test_invalid_review_replace_preserves_previous_target_bytes(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    paper, _ = prepare_review_paper(layout)
    service = RecordService(layout)
    memory, _ = service.promote(review_request(paper["paper_id"]), actor="agent")
    target = layout.review_memory_path(paper["paper_id"])
    before = target.read_bytes()
    sections = deepcopy(memory["sections"])
    note = sections[2]["units"][0]["source_notes"][0]
    note.update(
        {
            "note_type": "quote_excerpt",
            "text": "Absent fabricated excerpt",
            "locator": "page:1:char:0-5",
        }
    )

    with pytest.raises(ResearchKBError) as caught:
        service.promote(
            MutationRequest(
                operation="replace",
                record_kind="review-memory",
                target_record_id=memory["review_memory_id"],
                paper_id=paper["paper_id"],
                payload={"sections": sections},
            ),
            actor="agent",
        )

    assert caught.value.diagnostic.code == "RKBC-009"
    assert target.read_bytes() == before


def test_review_replace_rejects_cross_paper_target_and_human_owned_record(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    first, _ = prepare_review_paper(layout, "first-review.txt")
    second, _ = prepare_review_paper(layout, "second-review.txt")
    service = RecordService(layout)
    first_memory, _ = service.promote(review_request(first["paper_id"]), actor="agent")
    second_payload = review_payload()
    second_payload["review_status"] = "verified"
    second_memory, _ = service.promote(
        review_request(second["paper_id"], payload=second_payload),
        actor="user",
    )

    with pytest.raises(ResearchKBError) as cross_paper:
        service.promote(
            MutationRequest(
                operation="replace",
                record_kind="review-memory",
                target_record_id=first_memory["review_memory_id"],
                paper_id=second["paper_id"],
                payload={"one_sentence_reuse_value": "Cross-paper replacement must fail."},
            ),
            actor="agent",
        )
    with pytest.raises(ResearchKBError) as human_owned:
        service.promote(
            MutationRequest(
                operation="replace",
                record_kind="review-memory",
                target_record_id=second_memory["review_memory_id"],
                paper_id=second["paper_id"],
                payload={"one_sentence_reuse_value": "Agent replacement must fail."},
            ),
            actor="agent",
        )

    assert cross_paper.value.diagnostic.code == "RKBC-006"
    assert human_owned.value.diagnostic.code == "RKBC-006"


def test_review_parse_change_after_replace_requires_manual_resolution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    layout = make_runtime_workspace(tmp_path)
    paper, _ = prepare_review_paper(layout)
    service = RecordService(layout)
    memory, _ = service.promote(review_request(paper["paper_id"]), actor="agent")
    existing_events = read_process_events(layout.process_events_path)
    from research_kb.storage import transactions

    original_replace = transactions.replace_temp

    def replace_and_change_parse(temporary: Path, target: Path) -> None:
        original_replace(temporary, target)
        if target == layout.review_memory_path(paper["paper_id"]).resolve():
            layout.parse_path(paper["paper_id"]).write_bytes(b"")

    monkeypatch.setattr(transactions, "replace_temp", replace_and_change_parse)

    with pytest.raises(ResearchKBError) as caught:
        service.promote(
            MutationRequest(
                operation="replace",
                record_kind="review-memory",
                target_record_id=memory["review_memory_id"],
                paper_id=paper["paper_id"],
                payload={"one_sentence_reuse_value": "A changed parse must require manual resolution."},
            ),
            actor="agent",
        )

    assert caught.value.diagnostic.code == "RKBC-018"
    assert read_process_events(layout.process_events_path) == existing_events
    journals = [
        read_json_document(path, record_kind="transaction-journal")
        for path in layout.transactions_root.glob("*.json")
    ]
    journal = next(
        item
        for item in journals
        if item["target_store"] == "review_memories" and item["phase"] == "needs_resolution"
    )
    assert journal["result"] == "needs_resolution"


def test_stale_review_memory_does_not_block_unrelated_review_promotion(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    first, _ = prepare_review_paper(layout, "stale-review.txt")
    second, _ = prepare_review_paper(layout, "current-review.txt")
    service = RecordService(layout)
    service.promote(review_request(first["paper_id"]), actor="agent")
    ParseService(layout).run(paper_id=first["paper_id"], adapter=SyntheticTextAdapter())

    second_memory, _ = service.promote(review_request(second["paper_id"]), actor="agent")

    assert second_memory["paper_id"] == second["paper_id"]
    assert second_memory["automation_status"] == "passed_auto_checks"


def test_stale_review_replace_cannot_rebind_omitted_source_notes(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    paper, _ = prepare_review_paper(layout)
    service = RecordService(layout)
    memory, _ = service.promote(review_request(paper["paper_id"]), actor="agent")
    target = layout.review_memory_path(paper["paper_id"])
    before = target.read_bytes()
    ParseService(layout).run(paper_id=paper["paper_id"], adapter=SyntheticTextAdapter())

    with pytest.raises(ResearchKBError) as caught:
        service.promote(
            MutationRequest(
                operation="replace",
                record_kind="review-memory",
                target_record_id=memory["review_memory_id"],
                paper_id=paper["paper_id"],
                payload={"one_sentence_reuse_value": "A stale memory must be reread before refresh."},
            ),
            actor="agent",
        )

    assert caught.value.diagnostic.code == "RKBC-009"
    assert caught.value.diagnostic.json_path == "/payload/sections"
    assert target.read_bytes() == before


def test_misbound_review_memory_filename_blocks_runtime_before_overwrite(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    first, _ = prepare_review_paper(layout, "bound-review.txt")
    second, _ = prepare_review_paper(layout, "other-review.txt")
    service = RecordService(layout)
    service.promote(review_request(first["paper_id"]), actor="agent")
    wrong_target = layout.review_memory_path(second["paper_id"])
    wrong_target.write_bytes(layout.review_memory_path(first["paper_id"]).read_bytes())
    before = wrong_target.read_bytes()

    with pytest.raises(ResearchKBError) as caught:
        service.promote(review_request(second["paper_id"]), actor="agent")

    assert caught.value.diagnostic.code == "RKBC-021"
    assert wrong_target.read_bytes() == before


def _evidence_request(paper_id: str) -> MutationRequest:
    return MutationRequest(
        operation="append",
        record_kind="evidence",
        target_record_id=None,
        paper_id=paper_id,
        payload={
            "claim": "The fabricated review source contains one sentence.",
            "evidence_type": "reported_result",
            "quote": "The fabricated review separates two invented response classes.",
            "source_page": {
                "pdf_page": 1,
                "printed_page": None,
                "section": "Synthetic text",
                "figure_or_table": None,
            },
            "locator": "page:1:block:1",
            "support_scope": "The fabricated sentence only.",
            "what_it_does_not_support": ["Any real scientific claim"],
            "review_status": "ai_checked",
            "fixture_origin": "synthetic_from_scratch",
        },
    )
