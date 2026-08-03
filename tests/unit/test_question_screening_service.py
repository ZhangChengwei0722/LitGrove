from __future__ import annotations

from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from research_kb.catalog.models import canonical_digest
from research_kb.contracts.registry import SchemaRegistry
from research_kb.contracts.validator import validate_record
from research_kb.bundle import load_workspace_entries, validate_workspace_entries
from research_kb.errors import ResearchKBError
from research_kb.screening_bundles import decision_freshness, require_screening_eligible_links, screening_entries_diagnostics
from research_kb.services.question_screening import QuestionScreeningService
from research_kb.services.question_mapping import mapping_freshness_diagnostics
from research_kb.storage.transactions import TransactionManager
from research_kb.storage.json_io import read_jsonl, serialize_json, serialize_jsonl
from tests.fixture_factory import make_bundle
from tests.runtime_helpers import make_runtime_workspace


def _entries() -> list[tuple[str, dict]]:
    return [(item["kind"], deepcopy(item["record"])) for item in make_bundle("alpha")["records"]]


def _approval() -> dict[str, str]:
    return {"receipt_id": "screening-receipt", "approved_by": "user", "approved_at": "2026-08-03T00:00:00Z", "origin": "user_authored"}


def _service(tmp_path: Path) -> tuple[QuestionScreeningService, list[tuple[str, dict]]]:
    layout = make_runtime_workspace(tmp_path)
    entries = _entries()
    return QuestionScreeningService(layout, entries_loader=lambda _: deepcopy(entries)), entries


def _create_criteria(service: QuestionScreeningService, entries: list[tuple[str, dict]]) -> dict:
    question_id = next(record["question_id"] for kind, record in entries if kind == "question-mapping")
    bundle, transaction = service.promote_criteria(
        {"question_id": question_id, "title": "Synthetic eligibility", "scope": "Synthetic screening scope.", "inclusion_criteria": ["Includes synthetic intervention."], "exclusion_criteria": ["Excludes unrelated studies."], "notes": ""},
        approval=_approval(), actor="user", fixture_origin="synthetic_from_scratch",
    )
    assert transaction is not None
    return bundle


def test_screening_contracts_are_registered_and_closed() -> None:
    registry = SchemaRegistry()
    assert {"screening-criteria-bundle", "screening-decision-bundle"} <= set(registry.kinds)


def test_criteria_create_revise_archive_and_exact_replay(tmp_path: Path) -> None:
    service, entries = _service(tmp_path)
    first = _create_criteria(service, entries)
    current = first["revisions"][-1]["criteria"]
    repeated, repeated_tx = service.promote_criteria(current, criteria_id=first["criteria_id"], expected_revision_id=first["active_revision_id"], approval=_approval(), actor="user", fixture_origin="synthetic_from_scratch")
    assert repeated_tx is None
    assert repeated == first

    revised_payload = {**current, "scope": "Narrowed synthetic scope.", "status": "archived"}
    revised, transaction = service.promote_criteria(revised_payload, criteria_id=first["criteria_id"], expected_revision_id=first["active_revision_id"], approval=_approval(), actor="user", fixture_origin="synthetic_from_scratch")
    assert transaction is not None
    assert len(revised["revisions"]) == 2
    assert revised["revisions"][1]["predecessor"]["revision_digest"] == canonical_digest(revised["revisions"][0])
    assert service.list_criteria() == []


def test_criteria_create_rejects_caller_supplied_criterion_id(tmp_path: Path) -> None:
    service, entries = _service(tmp_path)
    question_id = next(record["question_id"] for kind, record in entries if kind == "question-mapping")
    with pytest.raises(ResearchKBError) as error:
        service.promote_criteria(
            {
                "question_id": question_id,
                "title": "Synthetic eligibility",
                "scope": "Synthetic screening scope.",
                "inclusion_criteria": [
                    {
                        "criterion_id": "criterion_f1111111-1111-4111-8111-111111111111",
                        "text": "Caller-owned identity.",
                    }
                ],
                "exclusion_criteria": [],
                "notes": "",
            },
            approval=_approval(),
            actor="user",
        )
    assert error.value.diagnostic.code == "RKBC-006"


def test_decision_binds_current_criteria_and_becomes_stale_after_successor(tmp_path: Path) -> None:
    service, entries = _service(tmp_path)
    criteria = _create_criteria(service, entries)
    question_id = criteria["question_id"]
    paper_id = next(record["paper_id"] for kind, record in entries if kind == "registry-paper")
    criteria_record = criteria["revisions"][-1]["criteria"]
    dispositions = [
        {"criterion_id": item["criterion_id"], "disposition": "met", "rationale": "Synthetic basis."}
        for field in ("inclusion_criteria", "exclusion_criteria")
        for item in criteria_record[field]
    ]
    decision, transaction = service.promote_decision(
        {"question_id": question_id, "paper_id": paper_id, "outcome": "included", "criteria_revision_id": criteria["active_revision_id"], "criteria_digest": criteria["revisions"][-1]["content_digest"], "criterion_dispositions": dispositions, "basis_scope": "paper_card", "rationale": "Synthetic inclusion.", "known_limitations": []},
        approval=_approval(), actor="user", fixture_origin="synthetic_from_scratch",
    )
    assert transaction is not None
    assert service.read_decision(decision["decision_id"])["freshness"]["state"] == "current"

    retained = criteria_record["inclusion_criteria"]
    successor, _ = service.promote_criteria(
        {**criteria_record, "scope": "Changed scope.", "inclusion_criteria": retained}, criteria_id=criteria["criteria_id"], expected_revision_id=criteria["active_revision_id"], approval=_approval(), actor="user", fixture_origin="synthetic_from_scratch",
    )
    all_entries = [*entries, ("screening-criteria-bundle", successor), ("screening-decision-bundle", decision)]
    assert decision_freshness(decision, all_entries)["state"] == "stale_criteria"


def test_decision_rejects_stale_digest_incomplete_dispositions_and_pair_duplicate(tmp_path: Path) -> None:
    service, entries = _service(tmp_path)
    criteria = _create_criteria(service, entries)
    question_id = criteria["question_id"]
    paper_id = next(record["paper_id"] for kind, record in entries if kind == "registry-paper")
    with pytest.raises(ResearchKBError) as stale:
        service.promote_decision({"question_id": question_id, "paper_id": paper_id, "outcome": "included", "criteria_revision_id": criteria["active_revision_id"], "criteria_digest": "0" * 64, "criterion_dispositions": [], "basis_scope": "metadata", "rationale": "Synthetic.", "known_limitations": []}, approval=_approval(), actor="user")
    assert stale.value.diagnostic.code == "RKBC-017"

    criteria_record = criteria["revisions"][-1]["criteria"]
    dispositions = [
        {"criterion_id": item["criterion_id"], "disposition": "met", "rationale": "Synthetic."}
        for field in ("inclusion_criteria", "exclusion_criteria")
        for item in criteria_record[field]
    ]
    dispositions[0]["rationale"] = ""
    with pytest.raises(ResearchKBError) as empty_rationale:
        service.promote_decision(
            {
                "question_id": question_id,
                "paper_id": paper_id,
                "outcome": "included",
                "criteria_revision_id": criteria["active_revision_id"],
                "criteria_digest": criteria["revisions"][-1]["content_digest"],
                "criterion_dispositions": dispositions,
                "basis_scope": "metadata",
                "rationale": "Synthetic.",
                "known_limitations": [],
            },
            approval=_approval(),
            actor="user",
        )
    assert empty_rationale.value.diagnostic.code == "RKBC-002"


def test_screening_global_diagnostics_detect_duplicate_pair_and_bad_digest(tmp_path: Path) -> None:
    service, entries = _service(tmp_path)
    criteria = _create_criteria(service, entries)
    paper_id = next(record["paper_id"] for kind, record in entries if kind == "registry-paper")
    criteria_record = criteria["revisions"][-1]["criteria"]
    dispositions = [{"criterion_id": item["criterion_id"], "disposition": "met", "rationale": "Synthetic."} for field in ("inclusion_criteria", "exclusion_criteria") for item in criteria_record[field]]
    decision, _ = service.promote_decision({"question_id": criteria["question_id"], "paper_id": paper_id, "outcome": "included", "criteria_revision_id": criteria["active_revision_id"], "criteria_digest": criteria["revisions"][-1]["content_digest"], "criterion_dispositions": dispositions, "basis_scope": "metadata", "rationale": "Synthetic.", "known_limitations": []}, approval=_approval(), actor="user", fixture_origin="synthetic_from_scratch")
    duplicate = deepcopy(decision)
    duplicate["decision_id"] = "screendecision_f1111111-1111-4111-8111-111111111111"
    duplicate["revisions"][0]["decision"]["decision_id"] = duplicate["decision_id"]
    duplicate["revisions"][0]["content_digest"] = canonical_digest(duplicate["revisions"][0]["decision"])
    diagnostics = screening_entries_diagnostics([*entries, ("screening-criteria-bundle", criteria), ("screening-decision-bundle", decision), ("screening-decision-bundle", duplicate)])
    assert any(item.code == "RKBC-004" for item in diagnostics)


def test_screening_records_validate_as_stored(tmp_path: Path) -> None:
    service, entries = _service(tmp_path)
    criteria = _create_criteria(service, entries)
    assert validate_record("screening-criteria-bundle", criteria, actor="stored") == []


def test_strict_mapping_gate_is_optional_then_requires_current_included_decision(tmp_path: Path) -> None:
    service, entries = _service(tmp_path)
    question_id = next(record["question_id"] for kind, record in entries if kind == "question-mapping")
    paper_id = next(record["paper_id"] for kind, record in entries if kind == "registry-paper")
    require_screening_eligible_links(question_id, [paper_id], entries)

    criteria = _create_criteria(service, entries)
    with pytest.raises(ResearchKBError):
        require_screening_eligible_links(question_id, [paper_id], [*entries, ("screening-criteria-bundle", criteria)])
    mapping = next(record for kind, record in entries if kind == "question-mapping" and record["question_id"] == question_id)
    assert any("screening" in item.message for item in mapping_freshness_diagnostics(mapping, [*entries, ("screening-criteria-bundle", criteria)]))

    criteria_record = criteria["revisions"][-1]["criteria"]
    dispositions = [{"criterion_id": item["criterion_id"], "disposition": "met", "rationale": "Synthetic."} for field in ("inclusion_criteria", "exclusion_criteria") for item in criteria_record[field]]
    decision, _ = service.promote_decision({"question_id": question_id, "paper_id": paper_id, "outcome": "included", "criteria_revision_id": criteria["active_revision_id"], "criteria_digest": criteria["revisions"][-1]["content_digest"], "criterion_dispositions": dispositions, "basis_scope": "metadata", "rationale": "Synthetic.", "known_limitations": []}, approval=_approval(), actor="user", fixture_origin="synthetic_from_scratch")
    require_screening_eligible_links(question_id, [paper_id], [*entries, ("screening-criteria-bundle", criteria), ("screening-decision-bundle", decision)])


class _BarrierTransactionManager(TransactionManager):
    def __init__(self, layout, barrier: Barrier):
        super().__init__(layout)
        self.barrier = barrier

    def promote_bytes(self, **kwargs):
        self.barrier.wait(timeout=5)
        return super().promote_bytes(**kwargs)


def test_concurrent_active_criteria_creation_has_one_winner(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    entries = _entries()
    question_id = next(record["question_id"] for kind, record in entries if kind == "question-mapping")
    barrier = Barrier(2)
    services = [QuestionScreeningService(layout, transaction_manager=_BarrierTransactionManager(layout, barrier), entries_loader=lambda _: deepcopy(entries)) for _ in range(2)]

    def create(service):
        try:
            service.promote_criteria({"question_id": question_id, "title": "Concurrent", "scope": "Synthetic scope.", "inclusion_criteria": ["Synthetic."], "exclusion_criteria": [], "notes": "", "status": "active"}, approval=_approval(), actor="user")
            return "committed"
        except ResearchKBError as error:
            return error.diagnostic.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(create, services))
    assert results.count("committed") == 1
    assert results.count("RKBC-004") == 1


def test_committed_screening_records_close_over_workspace_and_transactions(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    by_kind: dict[str, list[dict]] = {}
    for entry in make_bundle("alpha")["records"]:
        if entry["kind"] not in {"workspace", "domain-profile", "guardian-report"}:
            by_kind.setdefault(entry["kind"], []).append(deepcopy(entry["record"]))
    layout.registry_path.write_bytes(serialize_jsonl(by_kind["registry-paper"]))
    for page in by_kind["parsed-page"]:
        layout.parse_path(page["paper_id"]).write_bytes(serialize_jsonl([page]))
    for card in by_kind["paper-card"]:
        layout.paper_card_path(card["paper_id"]).write_bytes(serialize_json(card))
    for paper in by_kind["registry-paper"]:
        layout.evidence_path(paper["paper_id"]).write_bytes(serialize_jsonl([item for item in by_kind["evidence"] if item["paper_id"] == paper["paper_id"]]))
    layout.review_queue_path.write_bytes(serialize_jsonl(by_kind["review-queue"]))
    layout.question_mappings_path.write_bytes(serialize_jsonl(by_kind["question-mapping"]))
    layout.process_events_path.write_bytes(serialize_jsonl(by_kind["process-event"]))
    for kind in ("step7-synthesis", "step7-review-angle", "step7-insight", "step7-cross-view"):
        layout.step7_store_path(kind).write_bytes(serialize_jsonl(by_kind[kind]))

    service = QuestionScreeningService(layout)
    question_id = by_kind["question-mapping"][0]["question_id"]
    paper_id = by_kind["registry-paper"][0]["paper_id"]
    criteria, _ = service.promote_criteria({"question_id": question_id, "title": "Closed criteria", "scope": "Synthetic scope.", "inclusion_criteria": ["Synthetic inclusion."], "exclusion_criteria": [], "notes": "", "status": "active"}, approval=_approval(), actor="user", fixture_origin="synthetic_from_scratch")
    criterion = criteria["revisions"][-1]["criteria"]["inclusion_criteria"][0]
    _, decision_transaction = service.promote_decision({"question_id": question_id, "paper_id": paper_id, "outcome": "included", "criteria_revision_id": criteria["active_revision_id"], "criteria_digest": criteria["revisions"][-1]["content_digest"], "criterion_dispositions": [{"criterion_id": criterion["criterion_id"], "disposition": "met", "rationale": "Synthetic."}], "basis_scope": "paper_card", "rationale": "Synthetic inclusion.", "known_limitations": []}, approval=_approval(), actor="user", fixture_origin="synthetic_from_scratch")

    assert decision_transaction is not None
    decision_event = next(
        item
        for item in read_jsonl(layout.process_events_path, record_kind="process-event", id_field="event_id")
        if item["event_id"] == decision_transaction.event_id
    )
    assert criteria["active_revision_id"] in decision_event["input_refs"]

    validate_workspace_entries(load_workspace_entries(layout))
