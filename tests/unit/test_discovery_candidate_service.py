from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import pytest

from research_kb.errors import ResearchKBError
from research_kb.identifiers import Namespace
from research_kb.services.discovery_candidate import DiscoveryCandidateService
from research_kb.storage.json_io import read_jsonl, serialize_json, serialize_jsonl
from research_kb.storage.transactions import TransactionManager
from tests.discovery_candidate_helpers import discovery_report, discovery_result, selection_request
from tests.fixture_factory import make_bundle
from tests.runtime_helpers import make_runtime_workspace


NOW = datetime(2026, 7, 21, tzinfo=timezone.utc)
DISCOVERY_ID = "discovery_f0000001-0000-4000-8000-000000000001"
EVENT_ID = "event_f0000001-0000-4000-8000-000000000001"


def _service(layout):
    event_ids = iter(
        (
            EVENT_ID,
            "event_f0000002-0000-4000-8000-000000000002",
            "event_f0000003-0000-4000-8000-000000000003",
        )
    )
    transactions = TransactionManager(layout, clock=lambda: NOW, event_id_factory=lambda: next(event_ids))
    return DiscoveryCandidateService(
        layout,
        transaction_manager=transactions,
        id_allocator=lambda namespace: DISCOVERY_ID if namespace == Namespace.DISCOVERY else None,
    )


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def _seed_question_workspace(tmp_path: Path):
    layout = make_runtime_workspace(tmp_path)
    records = [
        (entry["kind"], deepcopy(entry["record"]))
        for entry in make_bundle("alpha")["records"]
        if entry["kind"]
        not in {
            "workspace",
            "domain-profile",
            "guardian-report",
            "step7-synthesis",
            "step7-review-angle",
            "step7-insight",
            "step7-cross-view",
        }
    ]
    by_kind: dict[str, list[dict]] = {}
    for kind, record in records:
        by_kind.setdefault(kind, []).append(record)
    layout.registry_path.write_bytes(serialize_jsonl(by_kind["registry-paper"]))
    for page in by_kind["parsed-page"]:
        layout.parse_path(page["paper_id"]).write_bytes(serialize_jsonl([page]))
    for card in by_kind["paper-card"]:
        layout.paper_card_path(card["paper_id"]).write_bytes(serialize_json(card))
    for paper in by_kind["registry-paper"]:
        evidence = [item for item in by_kind["evidence"] if item["paper_id"] == paper["paper_id"]]
        layout.evidence_path(paper["paper_id"]).write_bytes(serialize_jsonl(evidence))
    layout.review_queue_path.write_bytes(serialize_jsonl(by_kind["review-queue"]))
    layout.question_mappings_path.write_bytes(serialize_jsonl(by_kind["question-mapping"]))
    layout.process_events_path.write_bytes(serialize_jsonl(by_kind["process-event"]))
    return layout, by_kind["question-mapping"][0]["question_id"]


def test_user_selection_appends_candidate_and_exact_rerun_writes_nothing(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    request = selection_request()
    service = _service(layout)

    first = service.select(request, actor="user")

    assert first.created_candidate_ids == (DISCOVERY_ID,)
    assert first.updated_candidate_ids == ()
    assert first.unchanged_candidate_ids == ()
    assert first.transaction is not None
    stored = read_jsonl(
        layout.discovery_candidates_path,
        record_kind="discovery-candidate",
        id_field="candidate_id",
    )
    assert len(stored) == 1
    candidate = stored[0]
    assert candidate["candidate_id"] == DISCOVERY_ID
    assert candidate["selection_status"] == "user_selected"
    assert candidate["source_status"] == "metadata_only"
    assert candidate["acquisition_status"] == "not_started"
    assert candidate["not_evidence"] is True
    assert candidate["target_question_ids"] == []
    assert len(candidate["selection_contexts"]) == 1

    before = _tree_bytes(layout.knowledge_root)
    second = service.select(request, actor="user")

    assert second.transaction is None
    assert second.unchanged_candidate_ids == (DISCOVERY_ID,)
    assert _tree_bytes(layout.knowledge_root) == before


def test_new_query_context_updates_same_candidate_without_changing_identity(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    service = _service(layout)
    service.select(selection_request(), actor="user")
    rerun = selection_request(discovery_report(date_from="2026-07-13"))

    result = service.select(rerun, actor="user")

    assert result.created_candidate_ids == ()
    assert result.updated_candidate_ids == (DISCOVERY_ID,)
    candidate = service.show(DISCOVERY_ID)["candidate"]
    assert candidate["candidate_id"] == DISCOVERY_ID
    assert len(candidate["selection_contexts"]) == 2


def test_metadata_change_conflicts_and_rolls_back_whole_batch(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    service = _service(layout)
    service.select(selection_request(), actor="user")
    before = _tree_bytes(layout.knowledge_root)
    changed = discovery_result(title="Targeted degradation delivery in a changed invented system")
    new = discovery_result(
        result_key="doi:10.0000/synthetic.second",
        doi="10.0000/synthetic.second",
        record_id="SYNTH-DISCOVERY-2",
        title="Targeted degradation delivery in a second invented system",
    )
    report = discovery_report(changed, new)

    with pytest.raises(ResearchKBError) as caught:
        service.select(selection_request(report, result_keys=[changed["result_key"], new["result_key"]]), actor="user")

    assert caught.value.diagnostic.code == "RKBC-034"
    assert _tree_bytes(layout.knowledge_root) == before


def test_authority_invalid_report_and_unresolved_selection_fail_without_writes(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    service = _service(layout)
    before = _tree_bytes(layout.knowledge_root)

    with pytest.raises(ResearchKBError) as authority:
        service.select(selection_request(), actor="agent")
    assert authority.value.diagnostic.code == "RKBC-006"

    invalid_report = discovery_report()
    invalid_report["persistent_writes"] = 1
    with pytest.raises(ResearchKBError) as report_error:
        service.select(selection_request(invalid_report), actor="user")
    assert report_error.value.diagnostic.code == "RKBC-033"

    with pytest.raises(ResearchKBError) as unresolved:
        service.select(selection_request(result_keys=["doi:10.0000/missing"]), actor="user")
    assert unresolved.value.diagnostic.code == "RKBC-005"
    assert _tree_bytes(layout.knowledge_root) == before


def test_report_rejects_ineligible_or_asymmetric_results(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    service = _service(layout)

    old = discovery_report()
    old["results"][0]["first_publication_date"] = "2026-07-01"
    asymmetric_left = discovery_result()
    asymmetric_right = discovery_result(
        result_key="doi:10.0000/synthetic.second",
        doi="10.0000/synthetic.second",
        record_id="SYNTH-DISCOVERY-2",
        title="Targeted degradation delivery in a second invented system",
    )
    asymmetric_left["possible_duplicate_result_keys"] = [asymmetric_right["result_key"]]
    asymmetric = discovery_report(asymmetric_left, asymmetric_right)
    boolean_write_count = discovery_report()
    boolean_write_count["persistent_writes"] = False
    invalid_source = discovery_report()
    invalid_source["results"][0]["discovery_sources"][0]["record_id"] = None
    non_normalized_title = discovery_report()
    non_normalized_title["results"][0]["title"] += " "
    invalid_paper_type = discovery_report()
    invalid_paper_type["results"][0]["paper_type"] = []
    invalid_full_text_status = discovery_report()
    invalid_full_text_status["results"][0]["full_text_status"] = {}

    for report in (
        old,
        asymmetric,
        boolean_write_count,
        invalid_source,
        non_normalized_title,
        invalid_paper_type,
        invalid_full_text_status,
    ):
        with pytest.raises(ResearchKBError) as caught:
            service.select(selection_request(report), actor="user")
        assert caught.value.diagnostic.code == "RKBC-033"


def test_list_and_show_validate_full_store_and_missing_id_is_explicit(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    service = _service(layout)
    service.select(selection_request(), actor="user")

    listed = service.list()
    shown = service.show(DISCOVERY_ID)

    assert listed["candidate_count"] == 1
    assert listed["candidates"][0]["candidate_id"] == DISCOVERY_ID
    assert listed == service.list()
    assert shown["candidate"]["candidate_id"] == DISCOVERY_ID
    with pytest.raises(ResearchKBError) as caught:
        service.show("discovery_f0000002-0000-4000-8000-000000000002")
    assert caught.value.diagnostic.code == "RKBC-005"


def test_event_and_journal_expose_only_candidate_and_question_ids(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    _service(layout).select(selection_request(), actor="user")

    event_text = layout.process_events_path.read_text(encoding="utf-8")
    journal_text = layout.journal_path(EVENT_ID).read_text(encoding="utf-8")

    for forbidden in (
        "Targeted degradation",
        "Delivery was measured",
        "synthetic-6.9",
        "report_sha256",
        "title_keywords",
    ):
        assert forbidden not in event_text
        assert forbidden not in journal_text
    assert DISCOVERY_ID in event_text
    assert DISCOVERY_ID in journal_text


def test_selection_context_accepts_only_existing_question_ids_and_projects_union(tmp_path: Path) -> None:
    layout, question_id = _seed_question_workspace(tmp_path)

    result = _service(layout).select(
        selection_request(target_question_ids=[question_id]),
        actor="user",
    )

    assert result.created_candidate_ids == (DISCOVERY_ID,)
    candidate = read_jsonl(
        layout.discovery_candidates_path,
        record_kind="discovery-candidate",
        id_field="candidate_id",
    )[0]
    assert candidate["target_question_ids"] == [question_id]
    assert candidate["selection_contexts"][0]["target_question_ids"] == [question_id]
    event = read_jsonl(layout.process_events_path, record_kind="process-event", id_field="event_id")[-1]
    assert event["input_refs"] == [question_id]
