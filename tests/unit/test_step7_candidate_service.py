from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from research_kb.errors import ResearchKBError
from research_kb.identifiers import Namespace
from research_kb.mutation import MutationRequest
from research_kb.services.step7_candidate import Step7CandidateService
from research_kb.storage.json_io import read_jsonl, serialize_json, serialize_jsonl
from research_kb.storage.transactions import TransactionManager
from tests.fixture_factory import make_bundle
from tests.runtime_helpers import make_runtime_workspace


CORE_OWNED = {
    "schema_version",
    "candidate_id",
    "type",
    "evidence_base",
    "review_queue_refs",
    "input_snapshot",
    "not_fact",
    "review_status",
    "automation_status",
    "created_at",
    "updated_at",
    "fixture_origin",
}


def _candidate_id(namespace: Namespace) -> str:
    return f"{namespace.value}_f0000009-0000-4000-8000-000000000009"


def _seed_workspace(tmp_path: Path, *, include_step7_sources: bool = True):
    layout = make_runtime_workspace(tmp_path)
    records = [
        (entry["kind"], deepcopy(entry["record"]))
        for entry in make_bundle("alpha")["records"]
        if entry["kind"] not in {"workspace", "domain-profile", "guardian-report"}
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
    if include_step7_sources:
        for kind in ("step7-synthesis", "step7-review-angle"):
            layout.step7_store_path(kind).write_bytes(serialize_jsonl(by_kind[kind]))
    return layout, by_kind


def _payload(candidate: dict) -> dict:
    return {key: deepcopy(value) for key, value in candidate.items() if key not in CORE_OWNED}


def _request(kind: str, payload: dict, *, operation: str = "append", target: str | None = None) -> MutationRequest:
    return MutationRequest(
        operation=operation,
        record_kind=kind,
        target_record_id=target,
        paper_id=None,
        question_origin="existing_question",
        payload=payload,
        fixture_origin="synthetic_from_scratch",
    )


@pytest.mark.parametrize(
    "kind",
    ["step7-synthesis", "step7-review-angle", "step7-insight", "step7-cross-view"],
)
def test_append_all_candidate_types_derives_owned_closure_and_persists_atomically(tmp_path: Path, kind: str) -> None:
    layout, by_kind = _seed_workspace(tmp_path)
    source = deepcopy(by_kind[kind][0])
    service = Step7CandidateService(layout, id_allocator=_candidate_id)

    record, transaction = service.promote(_request(kind, _payload(source)), actor="agent")

    assert record["candidate_id"] == _candidate_id({
        "step7-synthesis": Namespace.SYNTHESIS,
        "step7-review-angle": Namespace.REVIEW_ANGLE,
        "step7-insight": Namespace.INSIGHT,
        "step7-cross-view": Namespace.CROSS_VIEW,
    }[kind])
    assert record["evidence_base"] == sorted(source["evidence_base"])
    assert record["review_queue_refs"] == sorted(source["review_queue_refs"])
    assert record["input_snapshot"]["evidence_ids"] == record["evidence_base"]
    assert record["not_fact"] is True
    assert record["review_status"] == "ai_draft"
    assert record["automation_status"] == "pending"
    stored = read_jsonl(layout.step7_store_path(kind), record_kind=kind, id_field="candidate_id")
    assert record in stored
    assert transaction.target == layout.step7_store_path(kind).resolve()


def test_replace_preserves_identity_type_question_and_creation_time(tmp_path: Path) -> None:
    layout, by_kind = _seed_workspace(tmp_path)
    existing = by_kind["step7-synthesis"][0]
    payload = _payload(existing)
    payload["title"] = "Revised synthetic synthesis"
    service = Step7CandidateService(layout, id_allocator=_candidate_id)

    record, _ = service.promote(
        _request(
            "step7-synthesis",
            payload,
            operation="replace",
            target=existing["candidate_id"],
        ),
        actor="agent",
    )

    assert record["candidate_id"] == existing["candidate_id"]
    assert record["type"] == existing["type"]
    assert record["question_id"] == existing["question_id"]
    assert record["created_at"] == existing["created_at"]
    assert record["title"] == "Revised synthetic synthesis"


@pytest.mark.parametrize(
    "field",
    ["candidate_id", "type", "evidence_base", "review_queue_refs", "input_snapshot", "review_status"],
)
def test_cli_owned_fields_are_rejected(field: str, tmp_path: Path) -> None:
    layout, by_kind = _seed_workspace(tmp_path)
    source = by_kind["step7-insight"][0]
    payload = _payload(source)
    payload[field] = source[field]

    with pytest.raises(ResearchKBError) as caught:
        Step7CandidateService(layout).promote(_request("step7-insight", payload), actor="agent")

    assert caught.value.diagnostic.code == "RKBC-006"


def test_stale_or_needs_resolution_mapping_blocks_promotion(tmp_path: Path) -> None:
    layout, by_kind = _seed_workspace(tmp_path)
    mappings = by_kind["question-mapping"]
    mappings[0]["mapping_status"] = "needs_resolution"
    layout.question_mappings_path.write_bytes(serialize_jsonl(mappings))
    payload = _payload(by_kind["step7-synthesis"][0])

    with pytest.raises(ResearchKBError) as caught:
        Step7CandidateService(layout).promote(_request("step7-synthesis", payload), actor="agent")

    assert caught.value.diagnostic.code == "RKBC-011"
    assert not any(item["candidate_id"] == _candidate_id(Namespace.SYNTHESIS) for item in read_jsonl(layout.step7_store_path("step7-synthesis"), record_kind="step7-synthesis", id_field="candidate_id"))


def test_cross_view_rejects_rejected_or_cross_question_source(tmp_path: Path) -> None:
    layout, by_kind = _seed_workspace(tmp_path)
    source = by_kind["step7-synthesis"][0]
    source["candidate_status"] = "rejected"
    source["rejection_rationale"] = "Synthetic rejection."
    layout.step7_store_path("step7-synthesis").write_bytes(serialize_jsonl([source]))
    payload = _payload(by_kind["step7-cross-view"][0])

    with pytest.raises(ResearchKBError) as caught:
        Step7CandidateService(layout).promote(_request("step7-cross-view", payload), actor="agent")

    assert caught.value.diagnostic.code == "RKBC-011"


def test_replace_cannot_move_candidate_to_another_question(tmp_path: Path) -> None:
    layout, by_kind = _seed_workspace(tmp_path)
    existing = by_kind["step7-synthesis"][0]
    payload = _payload(existing)
    payload["question_id"] = by_kind["question-mapping"][1]["question_id"]

    with pytest.raises(ResearchKBError) as caught:
        Step7CandidateService(layout).promote(
            _request("step7-synthesis", payload, operation="replace", target=existing["candidate_id"]),
            actor="agent",
        )

    assert caught.value.diagnostic.code == "RKBC-006"


def test_pre_replace_transaction_failure_preserves_target_and_emits_no_false_success(tmp_path: Path) -> None:
    layout, by_kind = _seed_workspace(tmp_path)
    target = layout.step7_store_path("step7-insight")
    assert not target.exists()
    event_id = "event_f0000009-0000-4000-8000-000000000009"

    class FailingTransactions(TransactionManager):
        def promote_bytes(self, **kwargs):
            def fail(phase: str) -> None:
                if phase == "prepared":
                    raise RuntimeError("injected")

            return super().promote_bytes(**kwargs, phase_hook=fail)

    transactions = FailingTransactions(layout, event_id_factory=lambda: event_id)
    service = Step7CandidateService(
        layout,
        transaction_manager=transactions,
        id_allocator=_candidate_id,
    )

    with pytest.raises(RuntimeError, match="injected"):
        service.promote(
            _request("step7-insight", _payload(by_kind["step7-insight"][0])),
            actor="agent",
        )

    assert not target.exists()
    events = read_jsonl(layout.process_events_path, record_kind="process-event", id_field="event_id")
    failure = next(item for item in events if item["event_id"] == event_id)
    assert failure["result"] == "failure"
    assert failure["output_refs"] == []


def test_in_lock_revalidation_rejects_concurrent_upstream_change(tmp_path: Path) -> None:
    layout, by_kind = _seed_workspace(tmp_path)
    target = layout.step7_store_path("step7-insight")

    class MutatingTransactions(TransactionManager):
        def promote_bytes(self, **kwargs):
            mappings = read_jsonl(
                layout.question_mappings_path,
                record_kind="question-mapping",
                id_field="question_id",
            )
            mappings[1]["updated_at"] = "2099-01-01T00:00:00Z"
            layout.question_mappings_path.write_bytes(serialize_jsonl(mappings))
            return super().promote_bytes(**kwargs)

    service = Step7CandidateService(
        layout,
        transaction_manager=MutatingTransactions(layout),
        id_allocator=_candidate_id,
    )

    with pytest.raises(ResearchKBError) as caught:
        service.promote(
            _request("step7-insight", _payload(by_kind["step7-insight"][0])),
            actor="agent",
        )

    assert caught.value.diagnostic.code == "RKBC-011"
    assert not target.exists()
