from pathlib import Path

import pytest

from research_kb.errors import ResearchKBError
from research_kb.mutation import MutationRequest
from research_kb.parse.synthetic_text import SyntheticTextAdapter
from research_kb.services.paper_context import PaperContextService
from research_kb.services.parse import ParseService
from research_kb.services.records import RecordService
from research_kb.services.registry import RegistryService
from research_kb.storage.json_io import file_sha256
from tests.fixture_factory import SECTIONS
from tests.runtime_helpers import make_runtime_workspace


def _tree_snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _register(layout, name: str, text: str):
    source = layout.source_roots["alpha-sources"] / name
    source.write_text(text, encoding="utf-8", newline="\n")
    paper, _ = RegistryService(layout).add(
        root_id="alpha-sources",
        relative_path=source.name,
        metadata={
            "bibliography": {"title": f"Synthetic {name}"},
            "fixture_origin": "synthetic_from_scratch",
        },
    )
    return source, paper


def _grounded_context(tmp_path: Path):
    layout = make_runtime_workspace(tmp_path)
    quote = "Invented response increased."
    source, paper = _register(layout, "context.txt", quote)
    ParseService(layout).run(paper_id=paper["paper_id"], adapter=SyntheticTextAdapter())
    records = RecordService(layout)
    locator = f"page:1:char:0-{len(quote)}"
    evidence = []
    for index in range(2):
        record, _ = records.promote(
            MutationRequest(
                operation="append",
                record_kind="evidence",
                target_record_id=None,
                paper_id=paper["paper_id"],
                payload={
                    "claim": f"The invented response increased in synthetic case {index + 1}.",
                    "evidence_type": "reported_result",
                    "quote": quote,
                    "source_page": {
                        "pdf_page": 1,
                        "printed_page": None,
                        "section": "Synthetic results",
                        "figure_or_table": None,
                    },
                    "locator": locator,
                    "support_scope": "The generated synthetic setting only.",
                    "what_it_does_not_support": ["Other settings"],
                    "review_status": "ai_checked",
                    "fixture_origin": "synthetic_from_scratch",
                },
            ),
            actor="agent",
        )
        evidence.append(record)
    queue = []
    for index in range(2):
        record, _ = records.promote(
            MutationRequest(
                operation="append",
                record_kind="review-queue",
                target_record_id=None,
                paper_id=paper["paper_id"],
                payload={
                    "issue_type": "overclaim",
                    "claim_candidate": f"The invented response is universal case {index + 1}.",
                    "reason": "The synthetic source covers one setting only.",
                    "source_page": {
                        "pdf_page": 1,
                        "printed_page": None,
                        "section": "Synthetic results",
                        "figure_or_table": None,
                    },
                    "locator": locator,
                    "resolution_status": "needs_resolution",
                    "review_status": "ai_checked",
                    "fixture_origin": "synthetic_from_scratch",
                },
            ),
            actor="agent",
        )
        queue.append(record)
    return layout, source, paper, evidence, queue


def _promote_card(layout, paper_id: str, evidence_id: str, queue_id: str):
    sections = [{"section_id": section_id, "units": []} for section_id in SECTIONS]
    sections[1]["units"].append(
        {
            "section_id": SECTIONS[1],
            "statement": "The generated study asks whether the invented response changes.",
            "statement_type": "reported_result",
            "grounding_status": "grounded",
            "evidence_ids": [evidence_id],
            "boundary_refs": [queue_id],
            "source_page": {
                "pdf_page": 1,
                "printed_page": None,
                "section": "Synthetic results",
                "figure_or_table": None,
            },
            "confidence": "medium",
        }
    )
    card, _ = RecordService(layout).promote(
        MutationRequest(
            operation="append",
            record_kind="paper-card",
            target_record_id=None,
            paper_id=paper_id,
            payload={
                "card_status": "calibrated",
                "review_status": "ai_checked",
                "sections": sections,
                "fixture_origin": "synthetic_from_scratch",
            },
        ),
        actor="agent",
    )
    return card


def test_paper_context_returns_registered_only_state_without_mutation(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    source, paper = _register(layout, "registered-only.txt", "Invented registered source.")
    source_before = source.read_bytes()
    knowledge_before = _tree_snapshot(layout.knowledge_root)

    result = PaperContextService(layout).show(paper_id=paper["paper_id"])

    assert result == {
        "status": "success",
        "interface_version": "1.0",
        "paper_id": paper["paper_id"],
        "paper_card": None,
        "evidence": [],
        "review_queue": [],
    }
    assert source.read_bytes() == source_before
    assert _tree_snapshot(layout.knowledge_root) == knowledge_before


def test_paper_context_returns_exact_sorted_same_paper_records(tmp_path: Path) -> None:
    layout, source, paper, evidence, queue = _grounded_context(tmp_path)
    card = _promote_card(
        layout,
        paper["paper_id"],
        evidence[0]["evidence_id"],
        queue[0]["queue_id"],
    )
    other_source, _ = _register(layout, "other.txt", "Unrelated invented source.")
    source_before = source.read_bytes()
    other_before = other_source.read_bytes()
    knowledge_before = _tree_snapshot(layout.knowledge_root)

    result = PaperContextService(layout).show(paper_id=paper["paper_id"])

    assert result["paper_card"] == card
    assert result["evidence"] == sorted(evidence, key=lambda item: item["evidence_id"])
    assert result["review_queue"] == sorted(queue, key=lambda item: item["queue_id"])
    assert {item["paper_id"] for item in result["evidence"]} == {paper["paper_id"]}
    assert {item["paper_id"] for item in result["review_queue"]} == {paper["paper_id"]}
    assert "source_ref" not in str(result)
    assert str(tmp_path) not in str(result)
    assert source.read_bytes() == source_before
    assert other_source.read_bytes() == other_before
    assert _tree_snapshot(layout.knowledge_root) == knowledge_before


def test_paper_context_recovers_partial_run_ids(tmp_path: Path) -> None:
    layout, _, paper, evidence, queue = _grounded_context(tmp_path)

    result = PaperContextService(layout).show(paper_id=paper["paper_id"])

    assert result["paper_card"] is None
    assert {item["evidence_id"] for item in result["evidence"]} == {
        item["evidence_id"] for item in evidence
    }
    assert {item["queue_id"] for item in result["review_queue"]} == {
        item["queue_id"] for item in queue
    }


def test_paper_context_rejects_unknown_stale_and_changing_sources(tmp_path: Path, monkeypatch) -> None:
    layout = make_runtime_workspace(tmp_path)
    source, paper = _register(layout, "source-state.txt", "Invented current source.")
    service = PaperContextService(layout)

    with pytest.raises(ResearchKBError) as caught:
        service.show(paper_id="not-a-paper-id")
    assert caught.value.diagnostic.code == "RKBC-002"

    with pytest.raises(ResearchKBError) as caught:
        service.show(paper_id="paper_b2222222-2222-4222-8222-222222222222")
    assert caught.value.diagnostic.code == "RKBC-005"

    original = source.read_bytes()
    source.unlink()
    with pytest.raises(ResearchKBError) as caught:
        service.show(paper_id=paper["paper_id"])
    assert caught.value.diagnostic.code == "RKBC-009"
    source.write_bytes(original)

    source.write_text("Changed source.\n", encoding="utf-8", newline="\n")
    with pytest.raises(ResearchKBError) as caught:
        service.show(paper_id=paper["paper_id"])
    assert caught.value.diagnostic.code == "RKBC-009"

    source.write_text("Invented current source.", encoding="utf-8", newline="\n")
    expected = paper["source_fingerprint"]["value"]
    calls = iter((expected, "f" * 64))
    monkeypatch.setattr("research_kb.services.paper_context.file_sha256", lambda _: next(calls))
    with pytest.raises(ResearchKBError) as caught:
        service.show(paper_id=paper["paper_id"])
    assert caught.value.diagnostic.code == "RKBC-009"
    assert file_sha256(source) == expected
