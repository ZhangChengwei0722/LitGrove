from __future__ import annotations

from pathlib import Path

import pytest

from research_kb.errors import ResearchKBError
from research_kb.mutation import MutationRequest
from research_kb.parse.synthetic_text import SyntheticTextAdapter
from research_kb.services.parse import ParseService
from research_kb.services.question_mapping import QuestionMappingService
from research_kb.services.research_organization import ResearchOrganizationService
from research_kb.services.records import RecordService
from research_kb.services.registry import RegistryService
from research_kb.storage.json_io import read_jsonl
from research_kb.storage.transactions import TransactionManager
from tests.fixture_factory import SECTIONS
from tests.runtime_helpers import make_runtime_workspace


def _register(layout, name: str) -> dict:
    root_id, source_root = next(iter(layout.source_roots.items()))
    source = source_root / name
    source.write_text(
        f"Invented response increased for {name}.\nInvented source for {name}.\n",
        encoding="utf-8",
        newline="\n",
    )
    paper, _ = RegistryService(layout).add(
        root_id=root_id,
        relative_path=name,
        metadata={"fixture_origin": "synthetic_from_scratch"},
    )
    return paper


def _prepare_paper(layout, name: str) -> dict[str, object]:
    paper = _register(layout, name)
    ParseService(layout).run(paper_id=paper["paper_id"], adapter=SyntheticTextAdapter())
    records = RecordService(layout)
    evidence, _ = records.promote(
        MutationRequest(
            operation="append",
            record_kind="evidence",
            target_record_id=None,
            paper_id=paper["paper_id"],
            payload={
                "claim": f"The invented response for {name} increased.",
                "evidence_type": "reported_result",
                "quote": f"Invented response increased for {name}.",
                "source_page": {
                    "pdf_page": 1,
                    "printed_page": None,
                    "section": "Results",
                    "figure_or_table": None,
                },
                "locator": "page:1:block:1",
                "support_scope": "The fabricated response only.",
                "what_it_does_not_support": ["Other fabricated systems"],
                "review_status": "ai_checked",
                "fixture_origin": "synthetic_from_scratch",
            },
        ),
        actor="agent",
    )
    queue, _ = records.promote(
        MutationRequest(
            operation="append",
            record_kind="review-queue",
            target_record_id=None,
            paper_id=paper["paper_id"],
            payload={
                "issue_type": "overclaim",
                "claim_candidate": "The response is universal.",
                "reason": "The invented source covers one case.",
                "source_page": {
                    "pdf_page": 1,
                    "printed_page": None,
                    "section": "Discussion",
                    "figure_or_table": None,
                },
                "locator": "page:1:block:2",
                "resolution_status": "needs_resolution",
                "review_status": "ai_checked",
                "fixture_origin": "synthetic_from_scratch",
            },
        ),
        actor="agent",
    )
    sections = [{"section_id": section_id, "units": []} for section_id in SECTIONS]
    sections[1]["units"].append({
        "section_id": SECTIONS[1],
        "statement": f"The fabricated study asks about {name}.",
        "statement_type": "reported_result",
        "grounding_status": "grounded",
        "evidence_ids": [evidence["evidence_id"]],
        "boundary_refs": [queue["queue_id"]],
        "source_page": {
            "pdf_page": 1,
            "printed_page": None,
            "section": "Results",
            "figure_or_table": None,
        },
        "confidence": "medium",
    })
    sections[5]["units"].append({
        "section_id": SECTIONS[5],
        "statement": f"The fabricated interpretation for {name} remains bounded.",
        "statement_type": "interpretation",
        "grounding_status": "interpretive",
        "evidence_ids": [],
        "boundary_refs": [],
        "source_page": None,
        "confidence": "low",
    })
    sections[6]["units"].append({
        "section_id": SECTIONS[6],
        "statement": f"The fabricated source for {name} needs resolution.",
        "statement_type": "future_direction",
        "grounding_status": "needs_resolution",
        "evidence_ids": [],
        "boundary_refs": [queue["queue_id"]],
        "source_page": None,
        "confidence": "low",
    })
    card, _ = records.promote(
        MutationRequest(
            operation="append",
            record_kind="paper-card",
            target_record_id=None,
            paper_id=paper["paper_id"],
            payload={
                "card_status": "calibrated",
                "review_status": "ai_checked",
                "sections": sections,
                "fixture_origin": "synthetic_from_scratch",
            },
        ),
        actor="agent",
    )
    units = [unit for section in card["sections"] for unit in section["units"]]
    return {
        "paper": paper,
        "evidence": evidence,
        "queue": queue,
        "grounded_unit": units[0],
        "interpretive_unit": units[1],
        "needs_resolution_unit": units[2],
    }


def _link(prepared: dict[str, object], unit_key: str = "grounded_unit", *, boundaries: list[str] | None = None) -> dict:
    return {
        "paper_id": prepared["paper"]["paper_id"],
        "selected_card_unit_ids": [prepared[unit_key]["unit_id"]],
        "role_in_question": "comparison",
        "relevance_rationale": "The selected fabricated unit addresses the question.",
        "boundary_refs": boundaries or [],
    }


def _append_request(links: list[dict], *, mapping_status: str = "ai_draft") -> MutationRequest:
    return MutationRequest(
        operation="append",
        record_kind="question-mapping",
        target_record_id=None,
        paper_id=None,
        payload={
            "question_text": "How do the fabricated responses compare?",
            "scope": "Synthetic records only.",
            "mapping_status": mapping_status,
            "paper_links": links,
        },
        question_origin="user_supplied",
        fixture_origin="synthetic_from_scratch",
    )


def test_append_projects_evidence_and_boundaries_and_sorts_links(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    first = _prepare_paper(layout, "one.txt")
    second = _prepare_paper(layout, "two.txt")
    allocated = iter([
        "question_a1111111-1111-4111-8111-111111111111",
        "qlink_a1111111-1111-4111-8111-111111111112",
        "qlink_a1111111-1111-4111-8111-111111111113",
    ])
    service = QuestionMappingService(layout, id_allocator=lambda namespace: next(allocated))

    record, transaction = service.promote(_append_request([_link(second), _link(first)]), actor="agent")

    assert record["question_id"].startswith("question_")
    assert [link["paper_id"] for link in record["paper_links"]] == sorted(
        [first["paper"]["paper_id"], second["paper"]["paper_id"]]
    )
    assert [link["question_link_id"] for link in record["paper_links"]] == [
        "qlink_a1111111-1111-4111-8111-111111111112",
        "qlink_a1111111-1111-4111-8111-111111111113",
    ]
    by_paper = {link["paper_id"]: link for link in record["paper_links"]}
    for prepared in (first, second):
        link = by_paper[prepared["paper"]["paper_id"]]
        assert link["evidence_ids"] == [prepared["evidence"]["evidence_id"]]
        assert link["boundary_refs"] == [prepared["queue"]["queue_id"]]
    assert transaction.target == layout.question_mappings_path
    assert read_jsonl(
        layout.question_mappings_path,
        record_kind="question-mapping",
        id_field="question_id",
    ) == [record]


@pytest.mark.parametrize("owned_field", ["question_link_id", "evidence_ids"])
def test_append_rejects_cli_owned_link_fields(tmp_path: Path, owned_field: str) -> None:
    layout = make_runtime_workspace(tmp_path)
    prepared = _prepare_paper(layout, "one.txt")
    link = _link(prepared)
    link[owned_field] = [] if owned_field == "evidence_ids" else "qlink_a1111111-1111-4111-8111-111111111111"

    with pytest.raises(ResearchKBError) as caught:
        QuestionMappingService(layout).promote(_append_request([link]), actor="agent")

    assert caught.value.diagnostic.code == "RKBC-006"
    assert not layout.question_mappings_path.exists()


def test_append_rejects_duplicate_paper_links(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    prepared = _prepare_paper(layout, "one.txt")

    with pytest.raises(ResearchKBError) as caught:
        QuestionMappingService(layout).promote(
            _append_request([_link(prepared), _link(prepared)]),
            actor="agent",
        )

    assert caught.value.diagnostic.code == "RKBC-004"


def test_append_rejects_cross_paper_unit_and_boundary(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    first = _prepare_paper(layout, "one.txt")
    second = _prepare_paper(layout, "two.txt")
    link = _link(first)
    link["selected_card_unit_ids"] = [second["grounded_unit"]["unit_id"]]
    link["boundary_refs"] = [second["queue"]["queue_id"]]

    with pytest.raises(ResearchKBError) as caught:
        QuestionMappingService(layout).promote(_append_request([link]), actor="agent")

    assert caught.value.diagnostic.code == "RKBC-009"


def test_needs_resolution_unit_is_not_admissible_for_new_mapping(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    prepared = _prepare_paper(layout, "one.txt")

    with pytest.raises(ResearchKBError) as caught:
        QuestionMappingService(layout).promote(
            _append_request([_link(prepared, "needs_resolution_unit")]),
            actor="agent",
        )
    assert caught.value.diagnostic.code == "RKBC-009"

    with pytest.raises(ResearchKBError) as caught:
        QuestionMappingService(layout).promote(
            _append_request([_link(prepared, "needs_resolution_unit")], mapping_status="needs_resolution"),
            actor="agent",
        )
    assert caught.value.diagnostic.code == "RKBC-009"


def test_interpretive_unit_is_not_admissible_for_new_mapping(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    prepared = _prepare_paper(layout, "one.txt")

    with pytest.raises(ResearchKBError) as caught:
        QuestionMappingService(layout).promote(
            _append_request([_link(prepared, "interpretive_unit")]),
            actor="agent",
        )
    assert caught.value.diagnostic.code == "RKBC-009"


def test_replace_preserves_ids_and_forbids_link_removal(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    first = _prepare_paper(layout, "one.txt")
    second = _prepare_paper(layout, "two.txt")
    service = QuestionMappingService(layout)
    original, _ = service.promote(_append_request([_link(first)]), actor="agent")
    original_link_id = original["paper_links"][0]["question_link_id"]
    replacement = MutationRequest(
        operation="replace",
        record_kind="question-mapping",
        target_record_id=original["question_id"],
        paper_id=None,
        payload={"paper_links": [_link(first), _link(second)], "mapping_status": "ai_checked"},
        question_origin="existing_question",
    )

    updated, transaction = service.promote(replacement, actor="agent")

    assert updated["question_id"] == original["question_id"]
    assert updated["created_at"] == original["created_at"]
    assert next(link for link in updated["paper_links"] if link["paper_id"] == first["paper"]["paper_id"])[
        "question_link_id"
    ] == original_link_id
    new_link_id = next(
        link["question_link_id"]
        for link in updated["paper_links"]
        if link["paper_id"] == second["paper"]["paper_id"]
    )
    event = next(
        item
        for item in read_jsonl(layout.process_events_path, record_kind="process-event", id_field="event_id")
        if item["event_id"] == transaction.event_id
    )
    assert original_link_id in event["input_refs"]
    assert new_link_id not in event["input_refs"]
    assert new_link_id in event["output_refs"]

    removal = MutationRequest(
        operation="replace",
        record_kind="question-mapping",
        target_record_id=original["question_id"],
        paper_id=None,
        payload={"paper_links": [_link(second)]},
        question_origin="existing_question",
    )
    with pytest.raises(ResearchKBError) as caught:
        service.promote(removal, actor="agent")
    assert caught.value.diagnostic.code == "RKBC-006"


def test_agent_cannot_change_existing_question_text_or_scope(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    prepared = _prepare_paper(layout, "one.txt")
    service = QuestionMappingService(layout)
    original, _ = service.promote(_append_request([_link(prepared)]), actor="agent")
    request = MutationRequest(
        operation="replace",
        record_kind="question-mapping",
        target_record_id=original["question_id"],
        paper_id=None,
        payload={"question_text": "A changed fabricated question."},
        question_origin="existing_question",
    )

    with pytest.raises(ResearchKBError) as caught:
        service.promote(request, actor="agent")

    assert caught.value.diagnostic.code == "RKBC-006"


def test_replace_rejects_non_array_links_with_structured_diagnostic(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    prepared = _prepare_paper(layout, "invalid-replace-links.txt")
    service = QuestionMappingService(layout)
    original, _ = service.promote(_append_request([_link(prepared)]), actor="agent")
    request = MutationRequest(
        operation="replace",
        record_kind="question-mapping",
        target_record_id=original["question_id"],
        paper_id=None,
        payload={"paper_links": "not-an-array"},
        question_origin="existing_question",
    )

    with pytest.raises(ResearchKBError) as caught:
        service.promote(request, actor="agent")

    assert caught.value.diagnostic.code == "RKBC-002"


def test_record_service_dispatches_question_mapping_before_paper_resolution(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    prepared = _prepare_paper(layout, "one.txt")

    record, _ = RecordService(layout).promote(_append_request([_link(prepared)]), actor="agent")

    assert record["question_id"].startswith("question_")


def test_first_mapping_write_detects_concurrent_store_creation(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    prepared = _prepare_paper(layout, "concurrent.txt")
    competing_bytes = b'{"competing":"writer"}\n'

    class ConcurrentFirstWriter(TransactionManager):
        def promote_bytes(self, **kwargs):
            kwargs["target"].write_bytes(competing_bytes)
            return super().promote_bytes(**kwargs)

    service = QuestionMappingService(
        layout,
        transaction_manager=ConcurrentFirstWriter(layout),
    )

    with pytest.raises(ResearchKBError) as caught:
        service.promote(_append_request([_link(prepared)]), actor="agent")

    assert caught.value.diagnostic.code == "RKBC-017"
    assert layout.question_mappings_path.read_bytes() == competing_bytes


def test_legacy_writer_is_disabled_after_p7_question_successor(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    prepared = _prepare_paper(layout, "successor.txt")
    legacy, _ = QuestionMappingService(layout).promote(
        _append_request([_link(prepared)]),
        actor="agent",
    )
    ResearchOrganizationService(layout).promote_question(
        {
            "question_text": legacy["question_text"],
            "scope": legacy["scope"],
            "mapping_status": legacy["mapping_status"],
            "factual_links": [
                {
                    key: link[key]
                    for key in (
                        "paper_id",
                        "selected_card_unit_ids",
                        "role_in_question",
                        "relevance_rationale",
                        "boundary_refs",
                    )
                }
                for link in legacy["paper_links"]
            ],
            "background_links": [],
        },
        question_id=legacy["question_id"],
        approval={
            "receipt_id": "user-authored-successor",
            "approved_by": "user",
            "approved_at": "2026-01-01T00:00:00Z",
            "origin": "user_authored",
        },
        actor="user",
    )

    with pytest.raises(ResearchKBError) as caught:
        QuestionMappingService(layout).promote(
            MutationRequest(
                operation="replace",
                record_kind="question-mapping",
                target_record_id=legacy["question_id"],
                paper_id=None,
                payload={"mapping_status": "ai_checked"},
                question_origin="existing_question",
            ),
            actor="agent",
        )

    assert caught.value.diagnostic.code == "RKBC-006"
