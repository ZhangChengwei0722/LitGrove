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


def _common(mapping: dict, papers: list[dict], *, operator: str) -> dict:
    return {
        "question_id": mapping["question_id"],
        "title": "Synthetic Step 7 candidate",
        "candidate_status": "keep",
        "analysis_operator": operator,
        "paper_card_base": [
            {
                "paper_id": item["paper"]["paper_id"],
                "card_unit_ids": [item["grounded_unit"]["unit_id"]],
            }
            for item in papers
        ],
        "missing_evidence": ["An independent fabricated observation"],
        "assumptions": ["The selected synthetic records are comparable"],
        "risk": ["The fabricated fixture has limited scope"],
        "testability": "Add one synthetic discriminating observation.",
        "next_action": "Retain the candidate for deterministic testing.",
        "trace_status": "traceable",
    }


def _request(kind: str, payload: dict) -> MutationRequest:
    return MutationRequest(
        operation="append",
        record_kind=kind,
        target_record_id=None,
        paper_id=None,
        question_origin="existing_question",
        payload=payload,
        fixture_origin="synthetic_from_scratch",
    )


@pytest.mark.parametrize("domain", ["alpha", "beta"])
def test_two_domains_run_two_papers_through_all_step7_types_and_reads(
    tmp_path: Path,
    capsys,
    domain: str,
) -> None:
    layout = make_runtime_workspace(tmp_path, domain=domain)
    first = _prepare_paper(layout, "first-step7.txt")
    second = _prepare_paper(layout, "second-step7.txt")
    sources_before = {
        path.name: file_sha256(path)
        for path in next(iter(layout.source_roots.values())).glob("*.txt")
    }
    mapping, _ = QuestionMappingService(layout).promote(
        _append_request([_link(first), _link(second)]),
        actor="agent",
    )
    records = RecordService(layout)

    synthesis_payload = _common(mapping, [first, second], operator="aggregate")
    synthesis_payload.update(
        {
            "claim": "Both fabricated papers report a bounded response.",
            "scope": "The two selected synthetic records.",
            "agreement_pattern": "The fabricated response direction agrees.",
            "conflict_pattern": "No direct synthetic conflict is represented.",
            "boundary_statement": "The records do not support universal generalization.",
        }
    )
    synthesis, _ = records.promote(_request("step7-synthesis", synthesis_payload), actor="agent")

    angle_payload = _common(mapping, [first, second], operator="compare")
    angle_payload.update(
        {
            "thesis": "Compare the fabricated studies by response and boundary.",
            "organizing_axes": ["response", "boundary"],
            "included_clusters": ["bounded synthetic agreement"],
            "excluded_scope": ["unrepresented settings"],
            "why_this_angle_adds_value": "It separates agreement from generalization.",
        }
    )
    angle, _ = records.promote(_request("step7-review-angle", angle_payload), actor="agent")

    insight_payload = _common(mapping, [first], operator="experiment_design")
    insight_payload.update(
        {
            "insight_type": "experimental_idea",
            "hypothesis_or_idea": "One extra fabricated control may narrow interpretation.",
            "rationale": "The selected Card Unit retains an explicit boundary.",
            "falsification_condition": "The added control does not change interpretation.",
            "minimum_test": "Add one fabricated control arm.",
        }
    )
    insight, _ = records.promote(_request("step7-insight", insight_payload), actor="agent")

    cross_payload = _common(mapping, [first, second], operator="contrast")
    cross_payload.update(
        {
            "source_views": [synthesis["candidate_id"], angle["candidate_id"]],
            "relation_type": "complements",
            "why_interesting": "The synthesis and angle expose different bounded dimensions.",
            "shared_dimension": "synthetic response",
            "non_equivalence_warning": "The fabricated methods are not identical.",
        }
    )
    cross_view, _ = records.promote(_request("step7-cross-view", cross_payload), actor="agent")

    before_reads = {
        path.relative_to(layout.knowledge_root).as_posix(): path.read_bytes()
        for path in layout.knowledge_root.rglob("*")
        if path.is_file()
    }
    assert main([
        "step7",
        "context",
        "--workspace",
        str(layout.config.path),
        "--question-id",
        mapping["question_id"],
    ]) == 0
    context_output = capsys.readouterr()
    context = json.loads(context_output.out)
    assert context_output.err == ""
    assert context["summary"]["total"] == 4
    assert context["summary"]["stale_count"] == 0
    assert [item["candidate"]["candidate_id"] for item in context["candidates"]] == [
        synthesis["candidate_id"],
        angle["candidate_id"],
        insight["candidate_id"],
        cross_view["candidate_id"],
    ]

    assert main([
        "step7",
        "render",
        "--workspace",
        str(layout.config.path),
        "--question-id",
        mapping["question_id"],
    ]) == 0
    render_output = capsys.readouterr()
    assert render_output.err == ""
    assert "## Synthesis" in render_output.out
    assert "## Review Angles" in render_output.out
    assert "## Insights" in render_output.out
    assert "## Cross-Views" in render_output.out
    assert "Review Queue Boundaries (Not Evidence)" in render_output.out
    assert {
        path.relative_to(layout.knowledge_root).as_posix(): path.read_bytes()
        for path in layout.knowledge_root.rglob("*")
        if path.is_file()
    } == before_reads
    assert GuardianService(layout).check().report["status"] == "success"
    assert {
        path.name: file_sha256(path)
        for path in next(iter(layout.source_roots.values())).glob("*.txt")
    } == sources_before
    for kind in (
        "step7-synthesis",
        "step7-review-angle",
        "step7-insight",
        "step7-cross-view",
    ):
        assert len(read_jsonl(layout.step7_store_path(kind), record_kind=kind, id_field="candidate_id")) == 1
