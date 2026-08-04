from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from research_kb.guardian import GuardianService
from research_kb.operational_maintenance import MaintenanceWorkService, OperationalMaintenanceService
from research_kb.storage.json_io import read_json_document, read_jsonl, serialize_json
from research_kb.storage.transactions import TransactionManager
from tests.runtime_helpers import make_runtime_workspace


ARCHIVE_ID = "oparchive_a1111111-1111-4111-8111-111111111111"
EVENT_ID = "event_a1111111-1111-4111-8111-111111111111"
FIXED_TIME = datetime(2026, 8, 4, tzinfo=timezone.utc)


class InjectedCrash(BaseException):
    pass


def _completed_transaction(layout) -> None:
    TransactionManager(
        layout,
        clock=lambda: FIXED_TIME,
        event_id_factory=lambda: EVENT_ID,
    ).promote_bytes(
        target=layout.review_queue_path,
        content=b"",
        target_store="review_queue",
        operation="synthetic_noop",
        actor="cli",
        input_refs=[],
        output_refs=[],
    )


def test_completed_journals_archive_and_remain_guardian_traceable(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    _completed_transaction(layout)
    service = OperationalMaintenanceService(
        layout,
        archive_id_factory=lambda: ARCHIVE_ID,
        clock=lambda: FIXED_TIME,
    )
    preview = service.preview_journal_archive()
    assert preview["eligible_journal_count"] == 1
    result = service.archive_journals(
        expected_basis_digest=preview["basis_digest"],
        actor="user",
    )

    assert result["archive_id"] == ARCHIVE_ID
    assert not layout.journal_path(EVENT_ID).exists()
    manifest = read_json_document(layout.operational_archive_path(ARCHIVE_ID) / "manifest.json")
    assert manifest["event_ids"] == [EVENT_ID]
    assert GuardianService(layout).check().report["status"] == "success"
    assert service.preview_journal_archive()["eligible_journal_count"] == 0


def test_published_archive_recovers_idempotently_after_interruption(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    _completed_transaction(layout)
    service = OperationalMaintenanceService(
        layout,
        archive_id_factory=lambda: ARCHIVE_ID,
        clock=lambda: FIXED_TIME,
        phase_hook=lambda phase: (_ for _ in ()).throw(InjectedCrash())
        if phase == "published"
        else None,
    )
    preview = service.preview_journal_archive()
    with pytest.raises(InjectedCrash):
        service.archive_journals(
            expected_basis_digest=preview["basis_digest"],
            actor="user",
        )

    recovered = OperationalMaintenanceService(layout, clock=lambda: FIXED_TIME).archive_journals(
        expected_basis_digest=preview["basis_digest"],
        actor="user",
    )
    assert recovered["result"] == "recovered"
    assert recovered["archive_id"] == ARCHIVE_ID
    assert GuardianService(layout).check().report["status"] == "success"


def test_settled_archive_rerun_is_idempotent_after_final_interruption(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    _completed_transaction(layout)
    service = OperationalMaintenanceService(
        layout,
        archive_id_factory=lambda: ARCHIVE_ID,
        clock=lambda: FIXED_TIME,
        phase_hook=lambda phase: (_ for _ in ()).throw(InjectedCrash())
        if phase == "settled"
        else None,
    )
    preview = service.preview_journal_archive()
    with pytest.raises(InjectedCrash):
        service.archive_journals(expected_basis_digest=preview["basis_digest"], actor="user")

    recovered = OperationalMaintenanceService(layout, clock=lambda: FIXED_TIME).archive_journals(
        expected_basis_digest=preview["basis_digest"],
        actor="user",
    )
    assert recovered["result"] == "already_settled"
    assert recovered["persistent_writes"] == 0
    assert GuardianService(layout).check().report["status"] == "success"


def test_guardian_reports_tampered_archive_and_duplicate_active_journal(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    _completed_transaction(layout)
    service = OperationalMaintenanceService(
        layout,
        archive_id_factory=lambda: ARCHIVE_ID,
        clock=lambda: FIXED_TIME,
    )
    preview = service.preview_journal_archive()
    service.archive_journals(expected_basis_digest=preview["basis_digest"], actor="user")
    archive = layout.operational_archive_path(ARCHIVE_ID)
    journal = read_jsonl(archive / "journals.jsonl", missing_ok=False)[0]
    layout.journal_path(EVENT_ID).write_bytes(serialize_json(journal))
    duplicate_findings = GuardianService(layout).check().report["findings"]
    assert any("still exists" in item["message"] for item in duplicate_findings)

    layout.journal_path(EVENT_ID).unlink()
    (archive / "journals.jsonl").write_bytes(b"{}\n")
    tamper_findings = GuardianService(layout).check().report["findings"]
    assert "RKBC-015" in {item["code"] for item in tamper_findings}


def test_maintenance_work_coalesces_equivalent_triggers_and_pages_stably(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    service = MaintenanceWorkService(layout, clock=lambda: FIXED_TIME)
    result = service.enqueue(
        [
            {
                "dependent_id": "question_a1111111-1111-4111-8111-111111111111",
                "upstream_revision": "primaryrev_a1111111-1111-4111-8111-111111111111",
                "reason": "upstream_revised",
                "trigger_ref": f"event_{index:08x}-1111-4111-8111-111111111111",
            }
            for index in range(1, 101)
        ],
        actor="cli",
    )
    assert result["created_count"] == 1
    assert result["coalesced_count"] == 99
    page = service.list(page_size=1)
    assert page["next_cursor"] is None
    assert len(page["items"]) == 1
    assert len(page["items"][0]["trigger_refs"]) == 100


def test_unrelated_maintenance_keys_do_not_coalesce(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    service = MaintenanceWorkService(layout, clock=lambda: FIXED_TIME)
    service.enqueue(
        [
            {
                "dependent_id": f"question_{suffix}",
                "upstream_revision": "primaryrev_a1111111-1111-4111-8111-111111111111",
                "reason": "upstream_revised",
                "trigger_ref": f"event_{suffix}",
            }
            for suffix in (
                "a1111111-1111-4111-8111-111111111111",
                "b2222222-2222-4222-8222-222222222222",
            )
        ],
        actor="cli",
    )
    assert len(service.list(page_size=10)["items"]) == 2
