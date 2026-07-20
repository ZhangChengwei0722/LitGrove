from pathlib import Path

import pytest

from research_kb.bundle import load_workspace_entries
from research_kb.guardian import GuardianService, status_for_findings
from research_kb.mutation import MutationRequest
from research_kb.parse.synthetic_text import SyntheticTextAdapter
from research_kb.services.parse import ParseService
from research_kb.services.question_mapping import QuestionMappingService, mapping_freshness_diagnostics
from research_kb.services.records import RecordService
from research_kb.services.registry import RegistryService
from research_kb.storage.json_io import file_sha256, read_json_document, read_jsonl, serialize_json, serialize_jsonl
from research_kb.storage.transactions import TransactionManager
from tests.runtime_helpers import make_runtime_workspace
from tests.unit.test_question_mapping_service import _append_request, _link, _prepare_paper
from tests.unit.test_review_memory_service import prepare_review_paper, review_request


def _register_source(layout) -> tuple[dict, Path]:
    source = layout.source_roots["alpha-sources"] / "study.txt"
    source.write_text("Invented guardian source.\n", encoding="utf-8", newline="\n")
    paper, _ = RegistryService(layout).add(root_id="alpha-sources", relative_path="study.txt", metadata={})
    return paper, source


def _tree_hashes(root: Path) -> dict[str, str | None]:
    return {
        path.relative_to(root).as_posix(): file_sha256(path)
        for path in root.rglob("*")
        if path.is_file()
    }


def test_guardian_check_only_is_read_only_and_succeeds(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    _register_source(layout)
    before = _tree_hashes(layout.knowledge_root)

    result = GuardianService(layout).check()

    assert result.report["status"] == "success"
    assert result.report["findings"] == []
    assert result.transaction is None
    assert _tree_hashes(layout.knowledge_root) == before


def test_guardian_detects_changed_source_fingerprint(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    paper, source = _register_source(layout)
    source.write_text("Changed invented source.\n", encoding="utf-8", newline="\n")

    result = GuardianService(layout).check()

    assert result.report["status"] == "failure"
    finding = next(item for item in result.report["findings"] if item["code"] == "RKBC-009")
    assert finding["record_ref"] == paper["paper_id"]


def test_guardian_detects_stored_evidence_provenance_failure_without_payload_leak(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    prepared = _prepare_paper(layout, "stored-provenance.txt")
    evidence = prepared["evidence"]
    target = layout.evidence_path(prepared["paper"]["paper_id"])
    stored = read_jsonl(target, record_kind="evidence", id_field="evidence_id")
    stored[0]["quote"] = "SENSITIVE INVENTED QUOTE"
    target.write_bytes(serialize_jsonl(stored))

    result = GuardianService(layout).check()

    assert result.report["status"] == "failure"
    finding = next(item for item in result.report["findings"] if item["record_ref"] == evidence["evidence_id"])
    assert finding["code"] == "RKBC-009"
    assert "SENSITIVE" not in finding["message"]


def test_guardian_detects_incomplete_transaction(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    manager = TransactionManager(layout)

    def interrupt(phase: str) -> None:
        if phase == "prepared":
            raise KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        manager.promote_bytes(
            target=layout.review_queue_path,
            content=b"",
            target_store="review_queue",
            operation="synthetic_interrupt",
            actor="cli",
            input_refs=[],
            output_refs=[],
            phase_hook=interrupt,
        )

    result = GuardianService(layout).check()

    assert result.report["status"] == "failure"
    assert "RKBC-018" in {item["code"] for item in result.report["findings"]}


def test_guardian_writes_report_only_when_requested(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    _register_source(layout)

    result = GuardianService(layout).check(write_report=True)

    reports = read_jsonl(layout.guardian_reports_path, record_kind="guardian-report", id_field="guardian_report_id")
    assert reports == [result.report]
    assert result.transaction is not None
    assert result.transaction.event_id in {
        item["event_id"] for item in read_jsonl(layout.process_events_path, record_kind="process-event", id_field="event_id")
    }


def test_guardian_status_is_derived_from_finding_severity() -> None:
    assert status_for_findings([]) == "success"
    assert status_for_findings([{"severity": "warning"}]) == "warning"
    assert status_for_findings([{"severity": "warning"}, {"severity": "error"}]) == "failure"


def test_guardian_detects_missing_event_for_completed_journal(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    _register_source(layout)
    layout.process_events_path.unlink()

    result = GuardianService(layout).check()

    assert result.report["status"] == "failure"
    assert "RKBC-018" in {item["code"] for item in result.report["findings"]}


def test_guardian_detects_tampered_event_for_completed_journal(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    _register_source(layout)
    events = read_jsonl(layout.process_events_path, record_kind="process-event", id_field="event_id")
    events[0]["operation"] = "tampered_operation"
    layout.process_events_path.write_bytes(serialize_jsonl(events))

    result = GuardianService(layout).check()

    assert result.report["status"] == "failure"
    assert "RKBC-018" in {item["code"] for item in result.report["findings"]}


def test_guardian_reports_stale_question_mapping_without_rewriting_it(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    prepared = _prepare_paper(layout, "mapping-staleness.txt")
    mapping, _ = QuestionMappingService(layout).promote(
        _append_request([_link(prepared)]),
        actor="agent",
    )
    mapping_before = layout.question_mappings_path.read_bytes()
    evidence = prepared["evidence"]
    RecordService(layout).promote(
        MutationRequest(
            operation="replace",
            record_kind="evidence",
            target_record_id=evidence["evidence_id"],
            paper_id=prepared["paper"]["paper_id"],
            payload={"claim": "The revised invented response remains bounded."},
        ),
        actor="agent",
    )
    unrelated = layout.source_roots["alpha-sources"] / "unrelated-after-stale.txt"
    unrelated.write_text("Unrelated invented source.\n", encoding="utf-8", newline="\n")
    registered, _ = RegistryService(layout).add(
        root_id="alpha-sources",
        relative_path=unrelated.name,
        metadata={"fixture_origin": "synthetic_from_scratch"},
        actor="cli",
    )
    assert registered["paper_id"]

    result = GuardianService(layout).check()

    assert result.report["status"] == "warning"
    finding = next(item for item in result.report["findings"] if item["code"] == "RKBC-014")
    assert finding["record_ref"] == mapping["question_id"]
    assert layout.question_mappings_path.read_bytes() == mapping_before


def test_mapping_freshness_compares_timestamps_not_isoformat_strings(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    prepared = _prepare_paper(layout, "fractional-timestamp.txt")
    mapping, _ = QuestionMappingService(layout).promote(
        _append_request([_link(prepared)]),
        actor="agent",
    )
    mapping["updated_at"] = "2026-01-01T00:00:00Z"
    entries = load_workspace_entries(layout)
    for kind, record in entries:
        if kind == "evidence" and record["evidence_id"] == prepared["evidence"]["evidence_id"]:
            record["updated_at"] = "2026-01-01T00:00:00.500000Z"

    diagnostics = mapping_freshness_diagnostics(mapping, entries)

    assert [item.code for item in diagnostics] == ["RKBC-014"]


def test_upstream_card_update_is_allowed_then_mapping_refresh_clears_stale_warning(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    prepared = _prepare_paper(layout, "upstream-refresh.txt")
    mapping, _ = QuestionMappingService(layout).promote(
        _append_request([_link(prepared)]),
        actor="agent",
    )
    records = RecordService(layout)
    extra_queue, _ = records.promote(
        MutationRequest(
            operation="append",
            record_kind="review-queue",
            target_record_id=None,
            paper_id=prepared["paper"]["paper_id"],
            payload={
                "issue_type": "ambiguous",
                "claim_candidate": "A second invented boundary may apply.",
                "reason": "The fabricated interpretation remains bounded.",
                "source_page": None,
                "locator": None,
                "resolution_status": "needs_resolution",
                "review_status": "ai_checked",
            },
        ),
        actor="agent",
    )
    card = read_json_document(
        layout.paper_card_path(prepared["paper"]["paper_id"]),
        record_kind="paper-card",
    )
    selected = next(
        unit
        for section in card["sections"]
        for unit in section["units"]
        if unit["unit_id"] == prepared["grounded_unit"]["unit_id"]
    )
    selected["boundary_refs"].append(extra_queue["queue_id"])

    records.promote(
        MutationRequest(
            operation="replace",
            record_kind="paper-card",
            target_record_id=prepared["paper"]["paper_id"],
            paper_id=prepared["paper"]["paper_id"],
            payload={"sections": card["sections"]},
        ),
        actor="agent",
    )

    stale = GuardianService(layout).check()
    assert stale.report["status"] == "warning"
    assert "RKBC-014" in {item["code"] for item in stale.report["findings"]}

    refreshed, _ = QuestionMappingService(layout).promote(
        MutationRequest(
            operation="replace",
            record_kind="question-mapping",
            target_record_id=mapping["question_id"],
            paper_id=None,
            payload={"paper_links": [_link(prepared)]},
            question_origin="existing_question",
        ),
        actor="agent",
    )

    assert extra_queue["queue_id"] in refreshed["paper_links"][0]["boundary_refs"]
    assert GuardianService(layout).check().report["status"] == "success"


def test_guardian_reports_stale_review_memory_as_warning_without_rewrite(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    paper, _ = prepare_review_paper(layout)
    memory, _ = RecordService(layout).promote(review_request(paper["paper_id"]), actor="agent")
    target = layout.review_memory_path(paper["paper_id"])
    before = target.read_bytes()
    ParseService(layout).run(paper_id=paper["paper_id"], adapter=SyntheticTextAdapter())

    result = GuardianService(layout).check()

    assert result.report["status"] == "warning"
    finding = next(item for item in result.report["findings"] if item["code"] == "RKBC-014")
    assert finding["record_ref"] == memory["review_memory_id"]
    assert target.read_bytes() == before


def test_guardian_reports_stale_step7_candidate_without_rewriting_it(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    prepared = _prepare_paper(layout, "step7-staleness.txt")
    mapping, _ = QuestionMappingService(layout).promote(
        _append_request([_link(prepared)]),
        actor="agent",
    )
    candidate, _ = RecordService(layout).promote(
        MutationRequest(
            operation="append",
            record_kind="step7-insight",
            target_record_id=None,
            paper_id=None,
            question_origin="existing_question",
            payload={
                "question_id": mapping["question_id"],
                "title": "Synthetic stale-candidate check",
                "candidate_status": "keep",
                "analysis_operator": "experiment_design",
                "paper_card_base": [
                    {
                        "paper_id": prepared["paper"]["paper_id"],
                        "card_unit_ids": [prepared["grounded_unit"]["unit_id"]],
                    }
                ],
                "missing_evidence": ["One independent fabricated observation"],
                "assumptions": ["The synthetic record remains comparable"],
                "risk": ["The fixture represents one setting"],
                "testability": "Add one fabricated observation.",
                "next_action": "Refresh only after upstream review.",
                "trace_status": "traceable",
                "insight_type": "experimental_idea",
                "hypothesis_or_idea": "One added control may narrow the synthetic interpretation.",
                "rationale": "The selected Card Unit retains one explicit boundary.",
                "falsification_condition": "The added control leaves interpretation unchanged.",
                "minimum_test": "Add one fabricated control arm.",
            },
            fixture_origin="synthetic_from_scratch",
        ),
        actor="agent",
    )
    target = layout.step7_store_path("step7-insight")
    before = target.read_bytes()
    RecordService(layout).promote(
        MutationRequest(
            operation="replace",
            record_kind="evidence",
            target_record_id=prepared["evidence"]["evidence_id"],
            paper_id=prepared["paper"]["paper_id"],
            payload={"claim": "The revised fabricated response remains bounded."},
        ),
        actor="agent",
    )
    unrelated = layout.source_roots["alpha-sources"] / "unrelated-step7-stale.txt"
    unrelated.write_text("Unrelated invented source.\n", encoding="utf-8", newline="\n")
    registered, _ = RegistryService(layout).add(
        root_id="alpha-sources",
        relative_path=unrelated.name,
        metadata={"fixture_origin": "synthetic_from_scratch"},
        actor="cli",
    )
    assert registered["paper_id"]

    result = GuardianService(layout).check()

    assert result.report["status"] == "warning"
    finding = next(item for item in result.report["findings"] if item["record_ref"] == candidate["candidate_id"])
    assert finding["code"] == "RKBC-014"
    assert "evidence_newer" in finding["message"]
    assert "do not rewrite it automatically" in finding["remediation"]
    assert target.read_bytes() == before


def test_guardian_rejects_broken_current_review_quote_provenance(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    paper, _ = prepare_review_paper(layout)
    memory, _ = RecordService(layout).promote(review_request(paper["paper_id"]), actor="agent")
    target = layout.review_memory_path(paper["paper_id"])
    note = memory["sections"][2]["units"][0]["source_notes"][0]
    note.update(
        {
            "note_type": "quote_excerpt",
            "text": "SENSITIVE INVENTED EXCERPT",
            "locator": "page:1:char:0-5",
        }
    )
    target.write_bytes(serialize_json(memory))

    result = GuardianService(layout).check()

    assert result.report["status"] == "failure"
    finding = next(item for item in result.report["findings"] if item["record_ref"] == memory["review_memory_id"])
    assert finding["code"] == "RKBC-009"
    assert "SENSITIVE" not in finding["message"]
