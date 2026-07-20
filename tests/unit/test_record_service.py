from pathlib import Path

import pytest

from research_kb.errors import ResearchKBError
from research_kb.mutation import MutationRequest
from research_kb.parse.synthetic_text import SyntheticTextAdapter
from research_kb.services.parse import ParseService
from research_kb.services.records import RecordService
from research_kb.services.registry import RegistryService
from research_kb.storage.json_io import read_json_document, read_jsonl
from research_kb.process_events import read_process_events
from tests.fixture_factory import SECTIONS
from tests.runtime_helpers import make_runtime_workspace


def _registered_paper(layout, name: str = "study.txt") -> dict:
    source = layout.source_roots["alpha-sources"] / name
    source.write_text(
        f"The fabricated response increased in chamber A.\nInvented source for {name}.\n",
        encoding="utf-8",
        newline="\n",
    )
    paper, _ = RegistryService(layout).add(
        root_id="alpha-sources",
        relative_path=name,
        metadata={"fixture_origin": "synthetic_from_scratch"},
    )
    ParseService(layout).run(paper_id=paper["paper_id"], adapter=SyntheticTextAdapter())
    return paper


def _evidence_request(paper_id: str, *, review_status: str = "ai_checked") -> MutationRequest:
    return MutationRequest(
        operation="append",
        record_kind="evidence",
        target_record_id=None,
        paper_id=paper_id,
        payload={
            "claim": "The invented chamber response increased.",
            "evidence_type": "reported_result",
            "quote": "The fabricated response increased in chamber A.",
            "source_page": {"pdf_page": 1, "printed_page": None, "section": "Results", "figure_or_table": None},
            "locator": "page:1:block:1",
            "support_scope": "The fabricated chamber comparison only.",
            "what_it_does_not_support": ["Other chambers"],
            "review_status": review_status,
            "fixture_origin": "synthetic_from_scratch",
        },
    )


def _queue_request(paper_id: str) -> MutationRequest:
    return MutationRequest(
        operation="append",
        record_kind="review-queue",
        target_record_id=None,
        paper_id=paper_id,
        payload={
            "issue_type": "overclaim",
            "claim_candidate": "The response applies everywhere.",
            "reason": "The invented source covers one chamber.",
            "source_page": {"pdf_page": 1, "printed_page": None, "section": "Discussion", "figure_or_table": None},
            "locator": "page:1:block:2",
            "resolution_status": "needs_resolution",
            "review_status": "ai_checked",
            "fixture_origin": "synthetic_from_scratch",
        },
    )


def test_record_service_injects_evidence_and_queue_owned_fields(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    paper = _registered_paper(layout)
    service = RecordService(layout)

    evidence, _ = service.promote(_evidence_request(paper["paper_id"]), actor="agent")
    queue, _ = service.promote(_queue_request(paper["paper_id"]), actor="agent")

    assert evidence["evidence_id"].startswith("evidence_")
    assert evidence["source_fingerprint"] == paper["source_fingerprint"]
    assert evidence["canonical"] is True
    assert evidence["automation_status"] == "passed_auto_checks"
    assert queue["queue_id"].startswith("queue_")
    assert queue["not_evidence"] is True
    assert queue["automation_status"] == "passed_auto_checks"


def test_record_service_creates_one_profile_aligned_card_with_cli_unit_ids(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    paper = _registered_paper(layout)
    service = RecordService(layout)
    evidence, _ = service.promote(_evidence_request(paper["paper_id"]), actor="agent")
    queue, _ = service.promote(_queue_request(paper["paper_id"]), actor="agent")
    sections = [{"section_id": section_id, "units": []} for section_id in SECTIONS]
    sections[1]["units"].append({
        "section_id": SECTIONS[1],
        "statement": "The invented study asks whether chamber conditions change the response.",
        "statement_type": "reported_result",
        "grounding_status": "grounded",
        "evidence_ids": [evidence["evidence_id"]],
        "boundary_refs": [queue["queue_id"]],
        "source_page": {"pdf_page": 1, "printed_page": None, "section": "Results", "figure_or_table": None},
        "confidence": "medium",
    })
    request = MutationRequest(
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
    )

    card, _ = service.promote(request, actor="agent")

    stored = read_json_document(layout.paper_card_path(paper["paper_id"]), record_kind="paper-card")
    assert stored == card
    assert [section["section_id"] for section in card["sections"]] == list(SECTIONS)
    assert card["sections"][1]["units"][0]["unit_id"].startswith("unit_")
    with pytest.raises(ResearchKBError) as caught:
        service.promote(request, actor="agent")
    assert caught.value.diagnostic.code == "RKBC-013"


def test_record_service_rejects_agent_owned_ids_and_human_review_state(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    paper = _registered_paper(layout)
    service = RecordService(layout)
    request = _evidence_request(paper["paper_id"], review_status="human_checked")
    request.payload["evidence_id"] = "evidence_a1111111-1111-4111-8111-111111111111"

    with pytest.raises(ResearchKBError) as caught:
        service.promote(request, actor="agent")

    assert caught.value.diagnostic.code == "RKBC-006"
    assert read_jsonl(layout.evidence_path(paper["paper_id"]), record_kind="evidence") == []


def test_record_service_replace_preserves_identity_and_creation_time(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    paper = _registered_paper(layout)
    service = RecordService(layout)
    original, _ = service.promote(_queue_request(paper["paper_id"]), actor="agent")
    request = MutationRequest(
        operation="replace",
        record_kind="review-queue",
        target_record_id=original["queue_id"],
        paper_id=paper["paper_id"],
        payload={
            "reason": "The narrowed invented statement now matches one chamber only.",
            "resolution_status": "resolved_by_narrowing",
        },
    )

    replaced, _ = service.promote(request, actor="agent")

    assert replaced["queue_id"] == original["queue_id"]
    assert replaced["paper_id"] == original["paper_id"]
    assert replaced["created_at"] == original["created_at"]
    assert replaced["reason"].startswith("The narrowed")


def test_record_service_rejects_cross_paper_evidence_reference(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    first = _registered_paper(layout, "study-one.txt")
    second = _registered_paper(layout, "study-two.txt")
    service = RecordService(layout)
    evidence, _ = service.promote(_evidence_request(first["paper_id"]), actor="agent")
    sections = [{"section_id": section_id, "units": []} for section_id in SECTIONS]
    sections[3]["units"].append({
        "section_id": SECTIONS[3],
        "statement": "The second invented paper reports a response.",
        "statement_type": "reported_result",
        "grounding_status": "grounded",
        "evidence_ids": [evidence["evidence_id"]],
        "boundary_refs": [],
        "source_page": {"pdf_page": 1},
        "confidence": "low",
    })
    request = MutationRequest(
        operation="append",
        record_kind="paper-card",
        target_record_id=None,
        paper_id=second["paper_id"],
        payload={"card_status": "calibrated", "review_status": "ai_checked", "sections": sections},
    )

    with pytest.raises(ResearchKBError) as caught:
        service.promote(request, actor="agent")

    assert caught.value.diagnostic.code == "RKBC-009"


def test_evidence_promotion_requires_current_source_and_preserves_existing_store(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    paper = _registered_paper(layout)
    service = RecordService(layout)
    service.promote(_evidence_request(paper["paper_id"]), actor="agent")
    target = layout.evidence_path(paper["paper_id"])
    target_before = target.read_bytes()
    source = layout.source_roots["alpha-sources"] / "study.txt"
    source.write_text("Changed invented source.\n", encoding="utf-8", newline="\n")

    with pytest.raises(ResearchKBError) as caught:
        service.promote(_evidence_request(paper["paper_id"]), actor="agent")

    assert caught.value.diagnostic.code == "RKBC-009"
    assert caught.value.diagnostic.json_path == "/source_fingerprint"
    assert target.read_bytes() == target_before


def test_invalid_evidence_replace_preserves_previous_target_bytes(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    paper = _registered_paper(layout)
    service = RecordService(layout)
    evidence, _ = service.promote(_evidence_request(paper["paper_id"]), actor="agent")
    target = layout.evidence_path(paper["paper_id"])
    target_before = target.read_bytes()

    with pytest.raises(ResearchKBError) as caught:
        service.promote(
            MutationRequest(
                operation="replace",
                record_kind="evidence",
                target_record_id=evidence["evidence_id"],
                paper_id=paper["paper_id"],
                payload={"quote": "An absent invented quote."},
            ),
            actor="agent",
        )

    assert caught.value.diagnostic.code == "RKBC-009"
    assert target.read_bytes() == target_before


def test_evidence_source_change_after_replace_requires_manual_resolution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    layout = make_runtime_workspace(tmp_path)
    paper = _registered_paper(layout)
    source = layout.source_roots["alpha-sources"] / "study.txt"
    existing_events = read_process_events(layout.process_events_path)
    from research_kb.storage import transactions

    original_replace = transactions.replace_temp

    def replace_and_change_source(temporary: Path, target: Path) -> None:
        original_replace(temporary, target)
        if target == layout.evidence_path(paper["paper_id"]).resolve():
            source.write_text("Changed during Evidence commit.\n", encoding="utf-8", newline="\n")

    monkeypatch.setattr(transactions, "replace_temp", replace_and_change_source)

    with pytest.raises(ResearchKBError) as caught:
        RecordService(layout).promote(_evidence_request(paper["paper_id"]), actor="agent")

    assert caught.value.diagnostic.code == "RKBC-018"
    assert read_process_events(layout.process_events_path) == existing_events
    journals = [
        read_json_document(path, record_kind="transaction-journal")
        for path in layout.transactions_root.glob("*.json")
    ]
    journal = next(item for item in journals if item["operation"] == "record_append" and item["phase"] == "needs_resolution")
    assert journal["result"] == "needs_resolution"


def test_record_service_dispatches_registry_mutation_request(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    source = layout.source_roots["alpha-sources"] / "registry-request.txt"
    source.write_text("Invented registry request source.\n", encoding="utf-8", newline="\n")
    service = RecordService(layout)
    append = MutationRequest(
        operation="append",
        record_kind="registry-paper",
        target_record_id=None,
        paper_id=None,
        payload={
            "source_ref": {"root_id": "alpha-sources", "relative_path": "registry-request.txt"},
            "bibliography": {"title": "Invented registry request"},
            "fixture_origin": "synthetic_from_scratch",
        },
    )

    paper, _ = service.promote(append, actor="agent")
    replace = MutationRequest(
        operation="replace",
        record_kind="registry-paper",
        target_record_id=paper["paper_id"],
        paper_id=paper["paper_id"],
        payload={"bibliography": {"year": 2026}},
    )
    updated, _ = service.promote(replace, actor="agent")

    assert updated["paper_id"] == paper["paper_id"]
    assert updated["bibliography"]["title"] == "Invented registry request"
    assert updated["bibliography"]["year"] == 2026


def test_paper_card_replace_preserves_known_units_and_allocates_new_units(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    paper = _registered_paper(layout)
    service = RecordService(layout)
    sections = [{"section_id": section_id, "units": []} for section_id in SECTIONS]
    sections[0]["units"].append({
        "section_id": SECTIONS[0],
        "statement": "Invented background statement.",
        "statement_type": "background",
        "grounding_status": "background_only",
        "evidence_ids": [],
        "boundary_refs": [],
        "source_page": None,
        "confidence": "medium",
    })
    original, _ = service.promote(
        MutationRequest(
            operation="append",
            record_kind="paper-card",
            target_record_id=None,
            paper_id=paper["paper_id"],
            payload={"card_status": "ai_draft", "review_status": "ai_checked", "sections": sections},
        ),
        actor="agent",
    )
    original_unit_id = original["sections"][0]["units"][0]["unit_id"]
    replacement_sections = original["sections"]
    replacement_sections[0]["units"][0]["statement"] = "Revised invented background statement."
    replacement_sections[6]["units"].append({
        "section_id": SECTIONS[6],
        "statement": "Invented future direction.",
        "statement_type": "future_direction",
        "grounding_status": "interpretive",
        "evidence_ids": [],
        "boundary_refs": [],
        "source_page": None,
        "confidence": "low",
    })

    replaced, _ = service.promote(
        MutationRequest(
            operation="replace",
            record_kind="paper-card",
            target_record_id=paper["paper_id"],
            paper_id=paper["paper_id"],
            payload={"sections": replacement_sections},
        ),
        actor="agent",
    )

    assert replaced["sections"][0]["units"][0]["unit_id"] == original_unit_id
    assert replaced["sections"][6]["units"][0]["unit_id"].startswith("unit_")
    assert replaced["sections"][6]["units"][0]["unit_id"] != original_unit_id
