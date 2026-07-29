from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from research_kb.errors import ResearchKBError
from research_kb.guardian import GuardianService
from research_kb.identifiers import Namespace, allocate_id
from research_kb.services.guardian_disposition import GuardianFindingDispositionService
from research_kb.storage.json_io import read_jsonl
from research_kb.storage.transactions import TransactionManager
from tests.runtime_helpers import make_runtime_workspace


NOW = datetime(2026, 7, 30, 2, 0, tzinfo=timezone.utc)
DISPOSITION_IDS = (
    "gdisp_11111111-1111-4111-8111-111111111111",
    "gdisp_21111111-1111-4111-8111-111111111111",
)
EVENT_IDS = (
    "event_31111111-1111-4111-8111-111111111111",
    "event_41111111-1111-4111-8111-111111111111",
)


def test_guardian_disposition_is_append_only_and_preserves_report(tmp_path) -> None:
    layout = make_runtime_workspace(tmp_path)

    def interrupt(phase: str) -> None:
        if phase == "prepared":
            raise KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        TransactionManager(
            layout,
            clock=lambda: NOW,
            event_id_factory=lambda: "event_51111111-1111-4111-8111-111111111111",
        ).promote_bytes(
            target=layout.review_queue_path,
            content=b"",
            target_store="review_queue",
            operation="synthetic_interrupt",
            actor="cli",
            input_refs=[],
            output_refs=[],
            phase_hook=interrupt,
        )
    report_result = GuardianService(layout).check(write_report=True)
    report_before = layout.guardian_reports_path.read_bytes()
    disposition_ids = iter(DISPOSITION_IDS)
    event_ids = iter(EVENT_IDS)
    transactions = TransactionManager(
        layout,
        clock=lambda: NOW,
        event_id_factory=lambda: next(event_ids),
    )
    service = GuardianFindingDispositionService(
        layout,
        transaction_manager=transactions,
        id_allocator=lambda namespace: (
            next(disposition_ids)
            if namespace == Namespace.GUARDIAN_DISPOSITION
            else allocate_id(namespace, lambda: uuid.UUID("a1111111-1111-4111-8111-111111111111"))
        ),
    )

    acknowledged = service.record(
        guardian_report_id=report_result.report["guardian_report_id"],
        finding_index=0,
        status="acknowledged",
        rationale="The synthetic recovery finding is being investigated.",
        expected_previous_disposition_id=None,
        actor="user",
        fixture_origin="synthetic_from_scratch",
    )
    resolved = service.record(
        guardian_report_id=report_result.report["guardian_report_id"],
        finding_index=0,
        status="resolved",
        rationale="The synthetic condition has a recorded resolution.",
        expected_previous_disposition_id=acknowledged.disposition["disposition_id"],
        actor="user",
        fixture_origin="synthetic_from_scratch",
    )

    stored = read_jsonl(
        layout.guardian_finding_dispositions_path,
        record_kind="guardian-finding-disposition",
        id_field="disposition_id",
    )
    assert stored == [acknowledged.disposition, resolved.disposition]
    assert resolved.disposition["previous_disposition_id"] == acknowledged.disposition["disposition_id"]
    assert layout.guardian_reports_path.read_bytes() == report_before

    with pytest.raises(ResearchKBError, match="terminal"):
        service.record(
            guardian_report_id=report_result.report["guardian_report_id"],
            finding_index=0,
            status="acknowledged",
            rationale="A resolved finding cannot be reopened in place.",
            expected_previous_disposition_id=resolved.disposition["disposition_id"],
            actor="user",
            fixture_origin="synthetic_from_scratch",
        )


def test_guardian_disposition_rejects_unreferenced_or_illegal_transition(tmp_path) -> None:
    layout = make_runtime_workspace(tmp_path)
    service = GuardianFindingDispositionService(layout)

    with pytest.raises(ResearchKBError, match="report"):
        service.record(
            guardian_report_id="guardian_11111111-1111-4111-8111-111111111111",
            finding_index=0,
            status="acknowledged",
            rationale="No report exists.",
            expected_previous_disposition_id=None,
            actor="user",
        )
