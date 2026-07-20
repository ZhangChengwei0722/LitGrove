from __future__ import annotations

import json
from importlib.metadata import version
from pathlib import Path

import pytest

from research_kb.errors import ResearchKBError
from research_kb.guardian import GuardianService
from research_kb.mutation import MutationRequest
from research_kb.parse.pdfplumber_adapter import PdfPlumberAdapter
from research_kb.services.parse import ParseService
from research_kb.services.question_mapping import QuestionMappingService
from research_kb.services.records import RecordService
from research_kb.services.registry import RegistryService
from research_kb.storage.json_io import file_sha256, read_json_document, read_jsonl
from tests.fixture_factory import SECTIONS
from tests.pdf_helpers import write_synthetic_pdf
from tests.runtime_helpers import make_runtime_workspace


def test_real_pdf_runs_from_registry_to_guardian_with_exact_character_provenance(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    source = write_synthetic_pdf(
        layout.source_roots["alpha-sources"] / "synthetic-primary.pdf",
        [
            "Invented experimental context.\n"
            "Invented target response increased.\n"
            "Invented matched control remained stable.",
            "Invented limitations remain bounded.",
        ],
    )
    source_before = file_sha256(source)
    paper, _ = RegistryService(layout).add(
        root_id="alpha-sources",
        relative_path=source.name,
        metadata={
            "bibliography": {"title": "Synthetic Real PDF Study"},
            "fixture_origin": "synthetic_from_scratch",
        },
    )
    pages, parse_transaction = ParseService(layout).run(
        paper_id=paper["paper_id"],
        adapter=PdfPlumberAdapter(),
    )
    assert file_sha256(source) == source_before
    assert pages[0]["parser"] == {"adapter": "pdfplumber", "version": version("pdfplumber")}
    assert {item["parse_run_id"] for item in pages} == {parse_transaction.event_id}

    quote = "Invented target response increased."
    start = pages[0]["text"].index(quote)
    end = start + len(quote)
    records = RecordService(layout)
    evidence, _ = records.promote(
        _evidence_request(
            paper["paper_id"],
            quote=quote,
            locator=f"page:1:char:{start}-{end}",
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
                "claim_candidate": "The invented response is universal.",
                "reason": "The generated PDF contains one fabricated setting only.",
                "source_page": {
                    "pdf_page": 1,
                    "printed_page": None,
                    "section": "Synthetic results",
                    "figure_or_table": None,
                },
                "locator": f"page:1:char:{start}-{end}",
                "resolution_status": "needs_resolution",
                "review_status": "ai_checked",
                "fixture_origin": "synthetic_from_scratch",
            },
        ),
        actor="agent",
    )
    assert file_sha256(source) == source_before

    evidence_target = layout.evidence_path(paper["paper_id"])
    evidence_before_invalid_attempts = evidence_target.read_bytes()
    invalid_cases = [
        ("Invented target response increased.", f"page:3:char:{start}-{end}", "RKBC-005"),
        ("Invented target response increased.", f"page:1:char:{start + 1}-{end}", "RKBC-009"),
        ("Invented wrong quote.", f"page:1:char:{start}-{end}", "RKBC-009"),
    ]
    for invalid_quote, invalid_locator, expected_code in invalid_cases:
        with pytest.raises(ResearchKBError) as caught:
            records.promote(
                _evidence_request(
                    paper["paper_id"],
                    quote=invalid_quote,
                    locator=invalid_locator,
                ),
                actor="agent",
            )
        assert caught.value.diagnostic.code == expected_code
        assert str(tmp_path) not in caught.value.diagnostic.message
        assert invalid_quote not in caught.value.diagnostic.message
        assert evidence_target.read_bytes() == evidence_before_invalid_attempts

    sections = [{"section_id": section_id, "units": []} for section_id in SECTIONS]
    sections[1]["units"].append(
        {
            "section_id": SECTIONS[1],
            "statement": "The generated study asks whether the invented target response changes.",
            "statement_type": "reported_result",
            "grounding_status": "grounded",
            "evidence_ids": [evidence["evidence_id"]],
            "boundary_refs": [queue["queue_id"]],
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
                        "relevance_rationale": "The grounded unit directly addresses the generated question.",
                        "boundary_refs": [queue["queue_id"]],
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
    assert file_sha256(source) == source_before

    stored_payload = json.dumps(
        {
            "pages": read_jsonl(layout.parse_path(paper["paper_id"]), record_kind="parsed-page"),
            "evidence": read_jsonl(evidence_target, record_kind="evidence"),
            "card": read_json_document(layout.paper_card_path(paper["paper_id"]), record_kind="paper-card"),
            "mapping": mapping,
        }
    )
    assert str(tmp_path) not in stored_payload


def _evidence_request(paper_id: str, *, quote: str, locator: str) -> MutationRequest:
    return MutationRequest(
        operation="append",
        record_kind="evidence",
        target_record_id=None,
        paper_id=paper_id,
        payload={
            "claim": "The invented target response increased in the generated comparison.",
            "evidence_type": "reported_result",
            "quote": quote,
            "source_page": {
                "pdf_page": int(locator.split(":", 2)[1]),
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
    )
