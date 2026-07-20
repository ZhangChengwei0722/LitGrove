from copy import deepcopy
from pathlib import Path

import pytest

from research_kb.errors import ResearchKBError
from research_kb.parse.synthetic_text import SyntheticTextAdapter
from research_kb.services.parse import ParseService
from research_kb.services.records import RecordService
from research_kb.services.registry import RegistryService
from research_kb.services.review_context import ReviewContextService
from research_kb.storage.json_io import file_sha256
from tests.runtime_helpers import make_runtime_workspace
from tests.unit.test_review_memory_service import (
    REVIEW_SECTIONS,
    prepare_review_paper,
    review_payload,
    review_request,
)


def _tree_hashes(root: Path) -> dict[str, str | None]:
    return {
        path.relative_to(root).as_posix(): file_sha256(path)
        for path in root.rglob("*")
        if path.is_file()
    }


def test_review_context_returns_absent_for_registered_parsed_review(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    paper, _ = prepare_review_paper(layout)

    result = ReviewContextService(layout).show(paper_id=paper["paper_id"])

    assert result == {
        "status": "success",
        "interface_version": "1.0",
        "paper_id": paper["paper_id"],
        "review_memory": None,
        "freshness": {"state": "absent", "reasons": []},
        "lead_registry_matches": [],
    }


def test_review_context_returns_complete_memory_and_exact_doi_match_read_only(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    paper, _ = prepare_review_paper(layout)
    lead_source = layout.source_roots["alpha-sources"] / "lead.txt"
    lead_source.write_text("Fabricated primary lead.\n", encoding="utf-8", newline="\n")
    lead_paper, _ = RegistryService(layout).add(
        root_id="alpha-sources",
        relative_path="lead.txt",
        metadata={
            "bibliography": {
                "title": "Fabricated primary lead",
                "doi": "doi:10.0000/synthetic.lead",
            },
            "fixture_origin": "synthetic_from_scratch",
        },
    )
    payload = review_payload()
    lead_unit = deepcopy(payload["sections"][2]["units"][0])
    lead_unit["section_id"] = REVIEW_SECTIONS[6]
    lead_unit["unit_type"] = "primary_paper_lead"
    lead_unit["content"] = "Follow the fabricated primary lead before making a comparison."
    lead_unit["workflow_impacts"][0]["action"] = "Ground the fabricated lead before comparison."
    lead_unit["primary_paper_lead"] = {
        "citation_label": "Synthetic Author 2024",
        "title": "Fabricated primary lead",
        "authors": ["Synthetic Author"],
        "year": 2024,
        "doi": "https://doi.org/10.0000/SYNTHETIC.LEAD",
        "related_topics": ["fabricated response"],
        "why_follow": "It is a synthetic foundational example.",
        "priority": "high",
        "priority_reasons": ["method_foundational"],
    }
    payload["sections"][6]["units"].append(lead_unit)
    memory, _ = RecordService(layout).promote(
        review_request(paper["paper_id"], payload=payload),
        actor="agent",
    )
    before = _tree_hashes(layout.knowledge_root)

    result = ReviewContextService(layout).show(paper_id=paper["paper_id"])

    assert result["review_memory"] == memory
    assert result["freshness"] == {"state": "current", "reasons": []}
    assert result["lead_registry_matches"] == [
        {
            "review_unit_id": memory["sections"][6]["units"][0]["review_unit_id"],
            "status": "exact_single_match",
            "matched_paper_ids": [lead_paper["paper_id"]],
        }
    ]
    assert _tree_hashes(layout.knowledge_root) == before


def test_review_context_reports_stale_parse_without_rebinding_old_notes(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    paper, _ = prepare_review_paper(layout)
    memory, _ = RecordService(layout).promote(review_request(paper["paper_id"]), actor="agent")

    pages, _ = ParseService(layout).run(paper_id=paper["paper_id"], adapter=SyntheticTextAdapter())
    result = ReviewContextService(layout).show(paper_id=paper["paper_id"])

    assert pages[0]["parse_run_id"] != memory["parse_snapshot"]["parse_run_id"]
    assert result["review_memory"] == memory
    assert result["freshness"] == {
        "state": "stale_parse",
        "reasons": ["parse_snapshot_changed"],
    }


def test_review_context_rejects_stale_source_before_output(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    paper, _ = prepare_review_paper(layout)
    RecordService(layout).promote(review_request(paper["paper_id"]), actor="agent")
    source = layout.source_roots["alpha-sources"] / "review.txt"
    source.write_text("Changed fabricated review.\n", encoding="utf-8", newline="\n")

    with pytest.raises(ResearchKBError) as caught:
        ReviewContextService(layout).show(paper_id=paper["paper_id"])

    assert caught.value.diagnostic.code == "RKBC-009"


def test_review_context_requires_registered_paper(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)

    with pytest.raises(ResearchKBError) as caught:
        ReviewContextService(layout).show(
            paper_id="paper_a1111111-1111-4111-8111-111111111111"
        )

    assert caught.value.diagnostic.code == "RKBC-005"


def test_review_context_rejects_source_change_during_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    layout = make_runtime_workspace(tmp_path)
    paper, _ = prepare_review_paper(layout)
    expected = paper["source_fingerprint"]["value"]
    calls = 0

    def changing_hash(path: Path) -> str:
        nonlocal calls
        del path
        calls += 1
        return expected if calls == 1 else "f" * 64

    monkeypatch.setattr("research_kb.services.review_context.file_sha256", changing_hash)

    with pytest.raises(ResearchKBError) as caught:
        ReviewContextService(layout).show(paper_id=paper["paper_id"])

    assert caught.value.diagnostic.code == "RKBC-009"
    assert "changed during" in caught.value.diagnostic.message
