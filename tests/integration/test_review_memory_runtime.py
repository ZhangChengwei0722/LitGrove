from pathlib import Path

from research_kb.guardian import GuardianService
from research_kb.parse.pdfplumber_adapter import PdfPlumberAdapter
from research_kb.services.parse import ParseService
from research_kb.services.paper_context import PaperContextService
from research_kb.services.records import RecordService
from research_kb.services.review_context import ReviewContextService
from research_kb.services.registry import RegistryService
from research_kb.storage.json_io import file_sha256, read_json_document
from tests.pdf_helpers import write_synthetic_pdf
from tests.runtime_helpers import make_runtime_workspace
from tests.unit.test_review_memory_service import (
    prepare_review_paper,
    review_payload,
    review_request,
)


def test_review_runtime_persists_reusable_and_low_value_memories_without_primary_leak(
    tmp_path: Path,
) -> None:
    layout = make_runtime_workspace(tmp_path)
    records = RecordService(layout)
    reusable_paper, _ = prepare_review_paper(layout, "reusable-review.txt")
    low_value_paper, _ = prepare_review_paper(layout, "low-value-review.txt")

    reusable, _ = records.promote(review_request(reusable_paper["paper_id"]), actor="agent")
    low_value, _ = records.promote(
        review_request(low_value_paper["paper_id"], payload=review_payload(with_unit=False)),
        actor="agent",
    )

    reusable_context = ReviewContextService(layout).show(paper_id=reusable_paper["paper_id"])
    low_value_context = ReviewContextService(layout).show(paper_id=low_value_paper["paper_id"])
    primary_context = PaperContextService(layout).show(paper_id=reusable_paper["paper_id"])
    guardian = GuardianService(layout).check()

    assert reusable_context["review_memory"] == reusable
    assert reusable_context["freshness"]["state"] == "current"
    assert low_value_context["review_memory"] == low_value
    assert low_value["memory_value"]["status"] == "low_value"
    assert sum(len(section["units"]) for section in low_value["sections"]) == 0
    assert primary_context == {
        "status": "success",
        "interface_version": "1.0",
        "paper_id": reusable_paper["paper_id"],
        "paper_card": None,
        "evidence": [],
        "review_queue": [],
    }
    assert guardian.report["status"] == "success"
    assert read_json_document(
        layout.review_memory_path(reusable_paper["paper_id"]),
        record_kind="review-memory",
    ) == reusable
    assert read_json_document(
        layout.review_memory_path(low_value_paper["paper_id"]),
        record_kind="review-memory",
    ) == low_value


def test_real_pdf_review_memory_uses_exact_character_excerpt_without_evidence(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    source = write_synthetic_pdf(
        layout.source_roots["alpha-sources"] / "synthetic-review.pdf",
        ["Invented review taxonomy separates alpha and beta frames."],
    )
    source_before = file_sha256(source)
    paper, _ = RegistryService(layout).add(
        root_id="alpha-sources",
        relative_path=source.name,
        metadata={
            "bibliography": {"title": "Synthetic Real PDF Review"},
            "fixture_origin": "synthetic_from_scratch",
        },
    )
    pages, _ = ParseService(layout).run(paper_id=paper["paper_id"], adapter=PdfPlumberAdapter())
    payload = review_payload()
    quote = "Invented review taxonomy separates alpha and beta frames."
    start = pages[0]["text"].index(quote)
    note = payload["sections"][2]["units"][0]["source_notes"][0]
    note.update(
        {
            "note_type": "quote_excerpt",
            "text": quote,
            "locator": f"page:1:char:{start}-{start + len(quote)}",
        }
    )

    memory, _ = RecordService(layout).promote(
        review_request(paper["paper_id"], payload=payload),
        actor="agent",
    )
    context = ReviewContextService(layout).show(paper_id=paper["paper_id"])

    assert context["review_memory"] == memory
    assert context["freshness"]["state"] == "current"
    assert PaperContextService(layout).show(paper_id=paper["paper_id"])["evidence"] == []
    assert file_sha256(source) == source_before
    assert GuardianService(layout).check().report["status"] == "success"
