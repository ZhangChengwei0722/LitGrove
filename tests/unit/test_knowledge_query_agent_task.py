from __future__ import annotations

from pathlib import Path

import pytest

from research_kb.errors import ResearchKBError
from research_kb.guardian import GuardianService
from research_kb.mutation import MutationRequest
from research_kb.parse.synthetic_text import SyntheticTextAdapter
from research_kb.services import AgentTaskApplicationService, WorkspaceSessionService
from research_kb.services.knowledge_query_context import KnowledgeQueryContextService
from research_kb.services.parse import ParseService
from research_kb.services.records import RecordService
from research_kb.services.registry import RegistryService
from tests.fixture_factory import SECTIONS
from tests.runtime_helpers import make_runtime_workspace
from tests.unit.test_review_memory_service import prepare_review_paper, review_request


POLICY = {
    "registry_version": "p5c-v1",
    "allowed_content_classes": [
        "metadata",
        "canonical_evidence",
        "paper_card_content",
        "review_background",
        "research_routing_context",
        "operational_context",
    ],
    "execution_scope": "cloud_allowed",
    "max_prompt_bytes": 1_048_576,
    "max_result_bytes": 262_144,
}
APPROVED_CLASSES = list(POLICY["allowed_content_classes"])


def _workspace_with_primary_papers(tmp_path: Path, count: int = 2):
    layout = make_runtime_workspace(tmp_path, agent_policy=POLICY)
    records = RecordService(layout)
    papers = []
    units = []
    evidence_records = []
    sources = []
    for index in range(count):
        quote = f"Synthetic intervention {index + 1} changed the fabricated endpoint."
        source = layout.source_roots["alpha-sources"] / f"query-{index + 1}.txt"
        source.write_text(quote, encoding="utf-8", newline="\n")
        paper, _ = RegistryService(layout).add(
            root_id="alpha-sources",
            relative_path=source.name,
            metadata={
                "bibliography": {
                    "title": f"Synthetic Query Paper {index + 1}",
                    "authors": ["Fixture Author"],
                    "year": 2026,
                    "doi": None,
                },
                "fixture_origin": "synthetic_from_scratch",
            },
        )
        ParseService(layout).run(paper_id=paper["paper_id"], adapter=SyntheticTextAdapter())
        evidence, _ = records.promote(
            MutationRequest(
                operation="append",
                record_kind="evidence",
                target_record_id=None,
                paper_id=paper["paper_id"],
                payload={
                    "claim": f"The synthetic intervention {index + 1} changed the fabricated endpoint.",
                    "evidence_type": "reported_result",
                    "quote": quote,
                    "source_page": {
                        "pdf_page": 1,
                        "printed_page": None,
                        "section": "Synthetic results",
                        "figure_or_table": None,
                    },
                    "locator": f"page:1:char:0-{len(quote)}",
                    "support_scope": "The generated synthetic setting only.",
                    "what_it_does_not_support": ["External scientific conclusions"],
                    "review_status": "ai_checked",
                    "fixture_origin": "synthetic_from_scratch",
                },
            ),
            actor="agent",
        )
        sections = [{"section_id": section_id, "units": []} for section_id in SECTIONS]
        sections[3]["units"].append(
            {
                "section_id": SECTIONS[3],
                "statement": evidence["claim"],
                "statement_type": "reported_result",
                "grounding_status": "grounded",
                "evidence_ids": [evidence["evidence_id"]],
                "boundary_refs": [],
                "source_page": evidence["source_page"],
                "confidence": "high",
            }
        )
        sections[5]["units"].append(
            {
                "section_id": SECTIONS[5],
                "statement": "This unresolved synthetic limitation cannot support factual answers.",
                "statement_type": "limitation",
                "grounding_status": "needs_resolution",
                "evidence_ids": [],
                "boundary_refs": [],
                "source_page": evidence["source_page"],
                "confidence": "low",
            }
        )
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
        papers.append(paper)
        units.append({"paper_id": paper["paper_id"], **card["sections"][3]["units"][0]})
        evidence_records.append(evidence)
        sources.append(source)
    session = WorkspaceSessionService({"alpha": layout.config.path}).open("alpha")
    return layout, session, papers, units, evidence_records, sources


def _expected(task: dict[str, object]) -> dict[str, str]:
    return {"state_id": str(task["state_id"]), "state_digest": str(task["state_digest"])}


def _create_request(paper_ids: list[str], *, query_type: str = "selected_paper_comparison"):
    return {
        "query_type": query_type,
        "query_text": "Compare the current synthetic findings.",
        "paper_ids": paper_ids,
        "include_review_background": False,
        "include_routing_context": False,
        "executor_id": "codex_cli",
        "approved_content_classes": APPROVED_CLASSES,
        "idempotency_key": "query-task-1",
    }


def _report(task: dict[str, object], units, evidence_records):
    return {
        "contract_version": "p5c-knowledge-query-report@1.0",
        "task_id": task["task_id"],
        "input_basis_digest": task["input_basis_digest"],
        "query_type": "selected_paper_comparison",
        "answer_blocks": [
            {
                "block_role": "cross_paper_synthesis",
                "text": "The two synthetic papers report bounded endpoint changes.",
                "support_refs": [
                    {
                        "paper_id": unit["paper_id"],
                        "card_unit_id": unit["unit_id"],
                        "evidence_ids": [evidence["evidence_id"]],
                    }
                    for unit, evidence in zip(units, evidence_records, strict=True)
                ],
                "background_refs": [],
                "background_only": False,
            }
        ],
        "unresolved_items": [],
        "persistence_status": "report_only",
        "canonical_scientific_write": False,
    }


def test_context_filters_non_admissible_units_and_never_exposes_source_paths(tmp_path: Path) -> None:
    layout, _, papers, units, evidence_records, _ = _workspace_with_primary_papers(tmp_path)
    result = KnowledgeQueryContextService(layout).build(
        query_type="selected_paper_comparison",
        query_text="Compare the current synthetic findings.",
        paper_ids=[paper["paper_id"] for paper in papers],
        include_review_background=False,
        include_routing_context=False,
        effective_content_classes=APPROVED_CLASSES,
    )

    assert result.basis["paper_ids"] == [paper["paper_id"] for paper in papers]
    assert {
        item["unit_id"]
        for paper in result.payload["primary_papers"]
        for item in paper["card_units"]
    } == {item["unit_id"] for item in units}
    assert {
        item["evidence_id"]
        for paper in result.payload["primary_papers"]
        for item in paper["evidence"]
    } == {item["evidence_id"] for item in evidence_records}
    rendered = str(result.payload)
    assert "source_ref" not in rendered
    assert "source_fingerprint" not in rendered
    assert str(tmp_path) not in rendered
    assert "unresolved synthetic limitation" not in rendered


@pytest.mark.parametrize(
    ("query_type", "paper_count"),
    [
        ("single_paper_explanation", 2),
        ("seven_section_overview", 2),
        ("methods", 2),
        ("selected_paper_comparison", 1),
        ("trend_problem_discussion", 1),
    ],
)
def test_context_enforces_query_cardinality(tmp_path: Path, query_type: str, paper_count: int) -> None:
    layout, _, papers, _, _, _ = _workspace_with_primary_papers(tmp_path)

    with pytest.raises(ResearchKBError, match="cardinality"):
        KnowledgeQueryContextService(layout).build(
            query_type=query_type,
            query_text="Bounded query",
            paper_ids=[paper["paper_id"] for paper in papers[:paper_count]],
            include_review_background=False,
            include_routing_context=False,
            effective_content_classes=APPROVED_CLASSES,
        )


def test_stale_source_is_reason_only_and_cannot_support_query(tmp_path: Path) -> None:
    layout, _, papers, _, _, sources = _workspace_with_primary_papers(tmp_path)
    sources[0].write_text("Changed source manifestation.", encoding="utf-8", newline="\n")

    result = KnowledgeQueryContextService(layout).build(
        query_type="selected_paper_comparison",
        query_text="Compare the current synthetic findings.",
        paper_ids=[paper["paper_id"] for paper in papers],
        include_review_background=False,
        include_routing_context=False,
        effective_content_classes=APPROVED_CLASSES,
    )

    stale_id = papers[0]["paper_id"]
    assert next(item for item in result.payload["primary_papers"] if item["paper_id"] == stale_id)["card_units"] == []
    assert any(
        item["paper_id"] == stale_id and item["reason"] == "source_not_current"
        for item in result.payload["excluded_context"]
    )
    assert "Changed source manifestation" not in str(result.payload)


def test_query_task_handoff_submit_preview_and_accept_are_report_only(tmp_path: Path) -> None:
    layout, session, papers, units, evidence_records, sources = _workspace_with_primary_papers(tmp_path)
    service = AgentTaskApplicationService()
    request = _create_request([paper["paper_id"] for paper in papers])
    sources_before = {path.name: path.read_bytes() for path in sources}
    scientific_before = {
        path.relative_to(layout.knowledge_root).as_posix(): path.read_bytes()
        for root in ("paper_cards", "evidence", "primary_bundles", "review_memories", "step7")
        for path in sorted((layout.knowledge_root / root).rglob("*"))
        if path.is_file()
    }

    created = service.create_knowledge_query(session, request)
    replay = service.create_knowledge_query(session, request)
    assert replay["persistent_writes"] == 0
    prepared = service.prepare_handoff(
        session,
        created["task"]["task_id"],
        _expected(created["task"]),
        "codex_cli",
    )
    assert prepared["handoff"]["manifest_version"] == "p5c-agent-handoff@1.0"
    assert "parsed_excerpts" not in prepared["handoff"]["payload"]
    assert "source_ref" not in str(prepared["handoff"])
    result = _report(prepared["task"], units, evidence_records)
    submitted = service.submit_result(
        session,
        prepared["task"]["task_id"],
        _expected(prepared["task"]),
        prepared["lease"],
        result,
    )
    preview = service.preview_result(session, submitted["task"]["task_id"])
    assert preview["candidate"]["retention_class"] == "current_task_report"
    assert preview["candidate"]["canonical_scientific_write"] is False
    accepted = service.accept_report(
        session,
        submitted["task"]["task_id"],
        _expected(submitted["task"]),
    )
    assert accepted["task"]["status"] == "approved"
    assert accepted["task"]["retention_class"] == "current_task_report"
    assert accepted["persistent_writes"] == 1
    assert GuardianService(layout).check().report["status"] == "success"
    scientific_after = {
        path.relative_to(layout.knowledge_root).as_posix(): path.read_bytes()
        for root in ("paper_cards", "evidence", "primary_bundles", "review_memories", "step7")
        for path in sorted((layout.knowledge_root / root).rglob("*"))
        if path.is_file()
    }
    assert {path.name: path.read_bytes() for path in sources} == sources_before
    assert scientific_after == scientific_before


def test_query_result_must_close_over_exact_payload_allowlist(tmp_path: Path) -> None:
    _, session, papers, units, evidence_records, _ = _workspace_with_primary_papers(tmp_path)
    service = AgentTaskApplicationService()
    created = service.create_knowledge_query(
        session,
        _create_request([paper["paper_id"] for paper in papers]),
    )
    prepared = service.prepare_handoff(
        session,
        created["task"]["task_id"],
        _expected(created["task"]),
        "codex_cli",
    )
    result = _report(prepared["task"], units, evidence_records)
    result["answer_blocks"][0]["support_refs"][0]["evidence_ids"] = [
        "evidence_aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    ]

    with pytest.raises(ResearchKBError, match="allowlist"):
        service.submit_result(
            session,
            prepared["task"]["task_id"],
            _expected(prepared["task"]),
            prepared["lease"],
            result,
        )


def test_query_result_is_rejected_after_bound_source_changes(tmp_path: Path) -> None:
    _, session, papers, units, evidence_records, sources = _workspace_with_primary_papers(tmp_path)
    service = AgentTaskApplicationService()
    created = service.create_knowledge_query(
        session,
        _create_request([paper["paper_id"] for paper in papers]),
    )
    prepared = service.prepare_handoff(
        session,
        created["task"]["task_id"],
        _expected(created["task"]),
        "codex_cli",
    )
    sources[0].write_text("Changed after handoff.", encoding="utf-8", newline="\n")

    with pytest.raises(ResearchKBError, match="input basis"):
        service.submit_result(
            session,
            prepared["task"]["task_id"],
            _expected(prepared["task"]),
            prepared["lease"],
            _report(prepared["task"], units, evidence_records),
        )


def test_review_background_report_uses_only_allowlisted_review_units(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path, agent_policy=POLICY)
    paper, _ = prepare_review_paper(layout)
    memory, _ = RecordService(layout).promote(review_request(paper["paper_id"]), actor="agent")
    session = WorkspaceSessionService({"alpha": layout.config.path}).open("alpha")
    service = AgentTaskApplicationService()
    request = _create_request([paper["paper_id"]], query_type="single_paper_explanation")
    request["query_text"] = "Summarize the review background without factual promotion."
    request["include_review_background"] = True
    created = service.create_knowledge_query(session, request)
    prepared = service.prepare_handoff(
        session,
        created["task"]["task_id"],
        _expected(created["task"]),
        "codex_cli",
    )
    review_unit = memory["sections"][2]["units"][0]
    assert prepared["handoff"]["payload"]["primary_papers"][0]["card_units"] == []
    assert prepared["handoff"]["payload"]["review_background"][0]["background_only"] is True
    result = {
        "contract_version": "p5c-knowledge-query-report@1.0",
        "task_id": prepared["task"]["task_id"],
        "input_basis_digest": prepared["task"]["input_basis_digest"],
        "query_type": "single_paper_explanation",
        "answer_blocks": [
            {
                "block_role": "background",
                "text": "The synthetic review proposes two fabricated response classes.",
                "support_refs": [],
                "background_refs": [
                    {
                        "paper_id": paper["paper_id"],
                        "review_memory_id": memory["review_memory_id"],
                        "review_unit_id": review_unit["review_unit_id"],
                    }
                ],
                "background_only": True,
            }
        ],
        "unresolved_items": [],
        "persistence_status": "report_only",
        "canonical_scientific_write": False,
    }

    submitted = service.submit_result(
        session,
        prepared["task"]["task_id"],
        _expected(prepared["task"]),
        prepared["lease"],
        result,
    )

    assert submitted["staged_result"]["answer_blocks"][0]["background_only"] is True


def test_zero_match_evidence_find_report_is_valid_and_honest(tmp_path: Path) -> None:
    _, session, papers, _, _, _ = _workspace_with_primary_papers(tmp_path, count=1)
    service = AgentTaskApplicationService()
    request = _create_request([papers[0]["paper_id"]], query_type="evidence_find")
    request["query_text"] = "Find support for a deliberately absent synthetic claim."
    created = service.create_knowledge_query(session, request)
    prepared = service.prepare_handoff(
        session,
        created["task"]["task_id"],
        _expected(created["task"]),
        "codex_cli",
    )
    result = {
        "contract_version": "p5c-knowledge-query-report@1.0",
        "task_id": prepared["task"]["task_id"],
        "input_basis_digest": prepared["task"]["input_basis_digest"],
        "query_type": "evidence_find",
        "answer_blocks": [
            {
                "block_role": "unresolved",
                "text": "No matching support exists in the current eligible Evidence set.",
                "support_refs": [],
                "background_refs": [],
                "background_only": False,
            }
        ],
        "unresolved_items": ["The current eligible set does not support the submitted claim."],
        "persistence_status": "report_only",
        "canonical_scientific_write": False,
    }

    submitted = service.submit_result(
        session,
        prepared["task"]["task_id"],
        _expected(prepared["task"]),
        prepared["lease"],
        result,
    )

    assert submitted["task"]["status"] == "submitted"


def test_query_revision_reject_and_stale_accept_preserve_report_only_lineage(tmp_path: Path) -> None:
    _, session, papers, units, evidence_records, sources = _workspace_with_primary_papers(tmp_path)
    service = AgentTaskApplicationService()
    created = service.create_knowledge_query(
        session,
        _create_request([paper["paper_id"] for paper in papers]),
    )
    prepared = service.prepare_handoff(
        session,
        created["task"]["task_id"],
        _expected(created["task"]),
        "codex_cli",
    )
    submitted = service.submit_result(
        session,
        prepared["task"]["task_id"],
        _expected(prepared["task"]),
        prepared["lease"],
        _report(prepared["task"], units, evidence_records),
    )
    revised = service.request_revision(
        session,
        submitted["task"]["task_id"],
        _expected(submitted["task"]),
        "Narrow the comparison to the exact synthetic endpoints.",
    )
    assert revised["task"]["status"] == "revision_requested"
    assert revised["successor_task"]["lineage"]["predecessor_task_id"] == submitted["task"]["task_id"]

    successor = revised["successor_task"]
    successor_handoff = service.prepare_handoff(
        session,
        successor["task_id"],
        _expected(successor),
        "codex_cli",
    )
    successor_result = _report(successor_handoff["task"], units, evidence_records)
    successor_submitted = service.submit_result(
        session,
        successor["task_id"],
        _expected(successor_handoff["task"]),
        successor_handoff["lease"],
        successor_result,
    )
    rejected = service.reject_result(
        session,
        successor["task_id"],
        _expected(successor_submitted["task"]),
    )
    assert rejected["task"]["status"] == "rejected"

    second_request = _create_request([paper["paper_id"] for paper in papers])
    second_request["idempotency_key"] = "query-task-stale-accept"
    second = service.create_knowledge_query(session, second_request)
    second_handoff = service.prepare_handoff(
        session,
        second["task"]["task_id"],
        _expected(second["task"]),
        "codex_cli",
    )
    second_submitted = service.submit_result(
        session,
        second_handoff["task"]["task_id"],
        _expected(second_handoff["task"]),
        second_handoff["lease"],
        _report(second_handoff["task"], units, evidence_records),
    )
    sources[0].write_text("Changed after report submission.", encoding="utf-8", newline="\n")
    with pytest.raises(ResearchKBError, match="input basis"):
        service.accept_report(
            session,
            second_submitted["task"]["task_id"],
            _expected(second_submitted["task"]),
        )
