import json
import shutil
from pathlib import Path

import pytest

from research_kb.config.loader import load_config
from research_kb.guardian import GuardianService
from research_kb.mutation import MutationRequest
from research_kb.parse.synthetic_text import SyntheticTextAdapter
from research_kb.services.parse import ParseService
from research_kb.services.records import RecordService
from research_kb.services.registry import RegistryService
from research_kb.services.bootstrap import WorkspaceBootstrapService
from research_kb.storage.json_io import file_sha256, read_json_document, read_jsonl
from research_kb.workspace import WorkspaceLayout


ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "workspaces"


@pytest.mark.parametrize("domain", ["domain_alpha", "domain_beta"])
def test_two_domains_run_same_core_from_intake_to_guardian(tmp_path: Path, domain: str) -> None:
    source_fixture = FIXTURE_ROOT / domain
    runtime_root = tmp_path / domain
    shutil.copytree(source_fixture, runtime_root)
    bootstrap = WorkspaceBootstrapService(runtime_root / "workspace.yaml").run()
    assert bootstrap.result == "initialized"
    layout = WorkspaceLayout.load(runtime_root / "workspace.yaml")
    expected = json.loads((runtime_root / "expected.json").read_text(encoding="utf-8"))
    source_paths = sorted((runtime_root / "sources").glob("*.txt"))
    source_hashes_before = {path.name: file_sha256(path) for path in source_paths}
    registry = RegistryService(layout)
    parser = ParseService(layout)
    records = RecordService(layout)

    papers = []
    for source in source_paths:
        paper, _ = registry.add(
            root_id=f"{domain.removeprefix('domain_')}-sources",
            relative_path=source.name,
            metadata={
                "bibliography": {"title": f"Invented {source.stem.replace('-', ' ')}"},
                "fixture_origin": "synthetic_from_scratch",
            },
            actor="cli",
        )
        papers.append(paper)
    registered_after_intake = read_jsonl(layout.registry_path, record_kind="registry-paper", id_field="paper_id")
    assert sum(bool(paper["duplicate_candidate_ids"]) for paper in registered_after_intake) == expected["duplicate_records"]

    profile = load_config(layout.domain_profile_path, "domain-profile").data
    section_ids = [item["section_id"] for item in profile["paper_card_sections"]]
    for paper in papers:
        pages, _ = parser.run(paper_id=paper["paper_id"], adapter=SyntheticTextAdapter())
        first_page = pages[0]
        evidence, _ = records.promote(
            MutationRequest(
                operation="append",
                record_kind="evidence",
                target_record_id=None,
                paper_id=paper["paper_id"],
                payload={
                    "claim": "The invented study reports a bounded response in its fabricated comparison.",
                    "evidence_type": "reported_result",
                    "quote": first_page["text"],
                    "source_page": {"pdf_page": 1, "printed_page": None, "section": "Synthetic observation", "figure_or_table": None},
                    "locator": first_page["locator"],
                    "support_scope": "The single invented comparison represented by this fixture.",
                    "what_it_does_not_support": ["Unrepresented settings", "A complete causal mechanism"],
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
                    "claim_candidate": "The invented response generalizes to every setting.",
                    "reason": "The fixture contains one fabricated comparison only.",
                    "source_page": {"pdf_page": 1, "printed_page": None, "section": "Synthetic observation", "figure_or_table": None},
                    "locator": first_page["locator"],
                    "resolution_status": "needs_resolution",
                    "review_status": "ai_checked",
                    "fixture_origin": "synthetic_from_scratch",
                },
            ),
            actor="agent",
        )
        sections = [{"section_id": section_id, "units": []} for section_id in section_ids]
        sections[1]["units"].append({
            "section_id": section_ids[1],
            "statement": "The paper tests a bounded response in one invented setting.",
            "statement_type": "reported_result",
            "grounding_status": "grounded",
            "evidence_ids": [evidence["evidence_id"]],
            "boundary_refs": [queue["queue_id"]],
            "source_page": {"pdf_page": 1, "printed_page": None, "section": "Synthetic observation", "figure_or_table": None},
            "confidence": "medium",
        })
        records.promote(
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

    guardian = GuardianService(layout).check(write_report=True)
    assert guardian.report["status"] == expected["guardian_status"]
    assert GuardianService(layout).check().report["status"] == "success"
    assert {path.name: file_sha256(path) for path in source_paths} == source_hashes_before

    stored_papers = read_jsonl(layout.registry_path, record_kind="registry-paper", id_field="paper_id")
    assert len(stored_papers) == expected["papers"]
    assert len(list((layout.knowledge_root / "parse" / "by_paper").glob("*.pages.jsonl"))) == expected["parsed_papers"]
    assert len(list((layout.knowledge_root / "paper_cards" / "by_paper").glob("*.card.json"))) == expected["paper_cards"]
    assert sum(
        len(read_jsonl(path, record_kind="evidence", id_field="evidence_id"))
        for path in (layout.knowledge_root / "evidence" / "by_paper").glob("*.evidence.jsonl")
    ) == expected["evidence"]
    assert len(read_jsonl(layout.review_queue_path, record_kind="review-queue", id_field="queue_id")) == expected["review_queue"]
    assert len(read_jsonl(layout.process_events_path, record_kind="process-event", id_field="event_id")) == expected["process_events_after_written_guardian"]
    assert all(
        read_json_document(layout.paper_card_path(paper["paper_id"]), record_kind="paper-card")["domain_profile_id"]
        == profile["domain_profile"]["id"]
        for paper in papers
    )
    assert not (layout.knowledge_root / "questions").exists()
    assert not (layout.knowledge_root / "step7").exists()
