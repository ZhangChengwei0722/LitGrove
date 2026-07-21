from __future__ import annotations

from pathlib import Path

from research_kb.guardian import GuardianService
from research_kb.mutation import MutationRequest
from research_kb.parse.pdfplumber_adapter import PdfPlumberAdapter
from research_kb.services.acquired_candidate_intake import AcquiredCandidateIntakeService
from research_kb.services.paper_context import PaperContextService
from research_kb.services.parse import ParseService
from research_kb.services.question_mapping import QuestionMappingService
from research_kb.services.records import RecordService
from research_kb.services.registry import RegistryService
from research_kb.services.review_context import ReviewContextService
from research_kb.storage.json_io import file_sha256
from tests.fixture_factory import SECTIONS
from tests.pdf_helpers import write_synthetic_pdf
from tests.unit.test_discovery_acquisition_service import (
    CANDIDATE_ID,
    FakeTransport,
    prepared_service,
)
from tests.unit.test_review_memory_service import review_payload, review_request


def _acquire_register_parse(tmp_path: Path, page_text: str):
    provider_pdf = write_synthetic_pdf(tmp_path / "provider.pdf", [page_text])
    layout, _, _, acquisition = prepared_service(
        tmp_path,
        transport=FakeTransport(provider_pdf.read_bytes()),
    )
    acquisition.acquire(CANDIDATE_ID, provider="europe-pmc", actor="user")
    source = layout.local_inbox / f"{CANDIDATE_ID}.pdf"
    source_digest = file_sha256(source)
    candidate_bytes = layout.discovery_candidates_path.read_bytes()

    intake = AcquiredCandidateIntakeService(layout).inspect(CANDIDATE_ID)
    paper, _ = RegistryService(layout).add(
        root_id=intake["source"]["root_id"],
        relative_path=intake["source"]["relative_path"],
        metadata=intake["registry_metadata"],
    )
    pages, _ = ParseService(layout).run(
        paper_id=paper["paper_id"],
        adapter=PdfPlumberAdapter(),
    )
    return layout, paper, pages, source, source_digest, candidate_bytes


def test_acquired_primary_candidate_reuses_existing_pipeline_through_guardian(
    tmp_path: Path,
) -> None:
    quote = "Invented target response increased in the synthetic comparison."
    layout, paper, pages, source, source_digest, candidate_bytes = _acquire_register_parse(
        tmp_path,
        f"Invented experimental context.\n{quote}\nInvented control remained stable.",
    )
    start = pages[0]["text"].index(quote)
    locator = f"page:1:char:{start}-{start + len(quote)}"
    records = RecordService(layout)
    evidence, _ = records.promote(
        MutationRequest(
            operation="append",
            record_kind="evidence",
            target_record_id=None,
            paper_id=paper["paper_id"],
            payload={
                "claim": "The invented target response increased in the synthetic comparison.",
                "evidence_type": "reported_result",
                "quote": quote,
                "source_page": {
                    "pdf_page": 1,
                    "printed_page": None,
                    "section": "Synthetic results",
                    "figure_or_table": None,
                },
                "locator": locator,
                "support_scope": "The generated synthetic comparison only.",
                "what_it_does_not_support": ["Other settings", "A complete mechanism"],
                "review_status": "ai_checked",
                "fixture_origin": "synthetic_from_scratch",
            },
        ),
        actor="agent",
    )
    sections = [{"section_id": section_id, "units": []} for section_id in SECTIONS]
    sections[1]["units"].append(
        {
            "section_id": SECTIONS[1],
            "statement": "The generated study asks whether the invented target response changes.",
            "statement_type": "reported_result",
            "grounding_status": "grounded",
            "evidence_ids": [evidence["evidence_id"]],
            "boundary_refs": [],
            "source_page": {
                "pdf_page": 1,
                "printed_page": None,
                "section": "Synthetic results",
                "figure_or_table": None,
            },
            "confidence": "medium",
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
    unit_id = card["sections"][1]["units"][0]["unit_id"]
    mapping, _ = QuestionMappingService(layout).promote(
        MutationRequest(
            operation="append",
            record_kind="question-mapping",
            target_record_id=None,
            paper_id=None,
            payload={
                "question_text": "How does the invented target response change?",
                "scope": "The generated synthetic study only.",
                "mapping_status": "ai_checked",
                "paper_links": [
                    {
                        "paper_id": paper["paper_id"],
                        "selected_card_unit_ids": [unit_id],
                        "role_in_question": "direct_evidence",
                        "relevance_rationale": "The grounded unit addresses the synthetic question.",
                        "boundary_refs": [],
                    }
                ],
            },
            question_origin="user_supplied",
            fixture_origin="synthetic_from_scratch",
        ),
        actor="agent",
    )

    assert mapping["paper_links"][0]["evidence_ids"] == [evidence["evidence_id"]]
    assert GuardianService(layout).check().report["status"] == "success"
    assert PaperContextService(layout).show(paper_id=paper["paper_id"])["paper_card"] == card
    assert not layout.review_memory_path(paper["paper_id"]).exists()
    assert file_sha256(source) == source_digest
    assert layout.discovery_candidates_path.read_bytes() == candidate_bytes


def test_acquired_review_candidate_reuses_existing_review_pipeline_through_guardian(
    tmp_path: Path,
) -> None:
    layout, paper, _, source, source_digest, candidate_bytes = _acquire_register_parse(
        tmp_path,
        "The fabricated review separates two invented response classes.",
    )
    memory, _ = RecordService(layout).promote(
        review_request(paper["paper_id"], payload=review_payload()),
        actor="agent",
    )

    assert GuardianService(layout).check().report["status"] == "success"
    assert ReviewContextService(layout).show(paper_id=paper["paper_id"])["review_memory"] == memory
    primary = PaperContextService(layout).show(paper_id=paper["paper_id"])
    assert primary["paper_card"] is None
    assert primary["evidence"] == []
    assert file_sha256(source) == source_digest
    assert layout.discovery_candidates_path.read_bytes() == candidate_bytes
