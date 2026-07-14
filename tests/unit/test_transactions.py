from datetime import datetime, timezone
from pathlib import Path

import pytest
from filelock import FileLock

from research_kb.errors import SCHEMA_VALIDATION_FAILED, Diagnostic, ResearchKBError
from research_kb.process_events import read_process_events
from research_kb.storage.json_io import file_sha256, read_json_document, read_jsonl, serialize_jsonl
from research_kb.storage.transactions import TransactionManager
from tests.runtime_helpers import make_runtime_workspace


EVENT_ID = "event_a1111111-1111-4111-8111-111111111111"
PAPER_ID = "paper_a1111111-1111-4111-8111-111111111111"
FIXED_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


class InjectedCrash(BaseException):
    pass


def manager(layout, *, lock_timeout: float = 30.0) -> TransactionManager:
    return TransactionManager(
        layout,
        lock_timeout=lock_timeout,
        clock=lambda: FIXED_TIME,
        event_id_factory=lambda: EVENT_ID,
    )


def validate_jsonl(path: Path) -> None:
    read_jsonl(path, missing_ok=False)


def test_successful_transaction_replaces_target_records_event_and_retains_journal(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    target = layout.registry_path
    result = manager(layout).promote_bytes(
        target=target,
        content=serialize_jsonl([{"paper_id": PAPER_ID}]),
        target_store="registry",
        operation="registry_append",
        actor="cli",
        input_refs=[],
        output_refs=[PAPER_ID],
        validator=validate_jsonl,
    )
    assert result.event_id == EVENT_ID
    assert target.read_bytes() == serialize_jsonl([{"paper_id": PAPER_ID}])
    assert read_process_events(layout.process_events_path)[0]["result"] == "success"
    assert read_json_document(layout.journal_path(EVENT_ID))["phase"] == "complete"


def test_validation_failure_preserves_original_and_records_failure(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    target = layout.registry_path
    target.parent.mkdir(parents=True)
    target.write_bytes(b'{"old":true}\n')
    before = target.read_bytes()

    def reject(_: Path) -> None:
        raise ResearchKBError(Diagnostic(SCHEMA_VALIDATION_FAILED, "synthetic", None, "", "rejected"))

    with pytest.raises(ResearchKBError) as caught:
        manager(layout).promote_bytes(
            target=target,
            content=b'{"new":true}\n',
            target_store="registry",
            operation="registry_replace",
            actor="cli",
            input_refs=[PAPER_ID],
            output_refs=[PAPER_ID],
            validator=reject,
        )
    assert caught.value.diagnostic.code == "RKBC-002"
    assert target.read_bytes() == before
    assert read_process_events(layout.process_events_path)[0]["result"] == "failure"
    assert read_json_document(layout.journal_path(EVENT_ID))["phase"] == "complete"


def test_lock_timeout_and_digest_conflict_do_not_mutate_target(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    target = layout.registry_path
    target.parent.mkdir(parents=True)
    target.write_bytes(b'{"old":true}\n')
    before = target.read_bytes()
    lock = FileLock(layout.lock_path)
    layout.lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock:
        with pytest.raises(ResearchKBError) as caught:
            manager(layout, lock_timeout=0.01).promote_bytes(
                target=target,
                content=b'{"new":true}\n',
                target_store="registry",
                operation="registry_replace",
                actor="cli",
                input_refs=[],
                output_refs=[PAPER_ID],
            )
    assert caught.value.diagnostic.code == "RKBC-016"
    assert target.read_bytes() == before

    with pytest.raises(ResearchKBError) as conflict:
        manager(layout).promote_bytes(
            target=target,
            content=b'{"new":true}\n',
            target_store="registry",
            operation="registry_replace",
            actor="cli",
            input_refs=[],
            output_refs=[PAPER_ID],
            expected_before_sha256="0" * 64,
        )
    assert conflict.value.diagnostic.code == "RKBC-017"
    assert target.read_bytes() == before


def test_recovery_after_crash_before_replace_records_failure(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    target = layout.registry_path
    target.parent.mkdir(parents=True)
    target.write_bytes(b'{"old":true}\n')

    def crash(phase: str) -> None:
        if phase == "prepared":
            raise InjectedCrash()

    with pytest.raises(InjectedCrash):
        manager(layout).promote_bytes(
            target=target,
            content=b'{"new":true}\n',
            target_store="registry",
            operation="registry_replace",
            actor="cli",
            input_refs=[PAPER_ID],
            output_refs=[PAPER_ID],
            validator=validate_jsonl,
            phase_hook=crash,
        )
    assert read_json_document(layout.journal_path(EVENT_ID))["phase"] == "prepared"
    assert manager(layout).recover(dry_run=True)[0]["action"] == "append_missing_failure_event"
    manager(layout).recover(dry_run=False)
    assert read_process_events(layout.process_events_path)[0]["result"] == "failure"
    assert read_json_document(layout.journal_path(EVENT_ID))["phase"] == "complete"


def test_recovery_after_target_replace_records_success(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    target = layout.registry_path

    def crash(phase: str) -> None:
        if phase == "target_replaced":
            raise InjectedCrash()

    content = b'{"new":true}\n'
    with pytest.raises(InjectedCrash):
        manager(layout).promote_bytes(
            target=target,
            content=content,
            target_store="registry",
            operation="registry_append",
            actor="cli",
            input_refs=[],
            output_refs=[PAPER_ID],
            validator=validate_jsonl,
            phase_hook=crash,
        )
    assert file_sha256(target) == file_sha256_value(content)
    assert read_json_document(layout.journal_path(EVENT_ID))["phase"] == "target_replaced"
    assert manager(layout).recover(dry_run=True)[0]["action"] == "append_missing_success_event"
    manager(layout).recover(dry_run=False)
    assert read_process_events(layout.process_events_path)[0]["result"] == "success"
    assert read_json_document(layout.journal_path(EVENT_ID))["phase"] == "complete"


def test_ambiguous_recovery_never_overwrites_target(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    target = layout.registry_path

    def crash(phase: str) -> None:
        if phase == "target_replaced":
            raise InjectedCrash()

    with pytest.raises(InjectedCrash):
        manager(layout).promote_bytes(
            target=target,
            content=b'{"new":true}\n',
            target_store="registry",
            operation="registry_append",
            actor="cli",
            input_refs=[],
            output_refs=[PAPER_ID],
            phase_hook=crash,
        )
    target.write_bytes(b'{"ambiguous":true}\n')
    before = target.read_bytes()
    assert manager(layout).recover(dry_run=False)[0]["action"] == "target_digest_ambiguous"
    assert target.read_bytes() == before
    assert read_json_document(layout.journal_path(EVENT_ID))["phase"] == "needs_resolution"


def file_sha256_value(content: bytes) -> str:
    import hashlib

    return hashlib.sha256(content).hexdigest()
