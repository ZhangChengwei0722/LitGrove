from pathlib import Path

import pytest

from research_kb.errors import ResearchKBError
from research_kb.process_events import (
    append_process_event,
    build_process_event,
    read_process_event_subset,
    read_process_events,
)
from research_kb.storage.json_io import serialize_jsonl


EVENT_ONE = "event_a1111111-1111-4111-8111-111111111111"
EVENT_TWO = "event_b2222222-2222-4222-8222-222222222222"


def _event(event_id: str, *, result: str = "success") -> dict:
    return build_process_event(
        event_id=event_id,
        operation="synthetic_event",
        actor="cli",
        result=result,
        input_refs=[],
        output_refs=[] if result == "failure" else [event_id],
        created_at="2026-08-05T00:00:00Z",
    )


def test_process_event_subset_and_streaming_append_preserve_contract(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    first = _event(EVENT_ONE)
    second = _event(EVENT_TWO)
    path.write_bytes(serialize_jsonl([first]))

    append_process_event(path, second, write_id="append-second")

    assert read_process_events(path) == [first, second]
    assert read_process_event_subset(path, {EVENT_TWO}) == {EVENT_TWO: second}
    before = path.read_bytes()
    append_process_event(path, second, write_id="append-idempotent")
    assert path.read_bytes() == before
    assert not list(tmp_path.glob(".*.tmp"))


def test_streaming_append_rejects_conflicting_existing_event(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    path.write_bytes(serialize_jsonl([_event(EVENT_ONE)]))

    with pytest.raises(ResearchKBError):
        append_process_event(
            path,
            _event(EVENT_ONE, result="failure"),
            write_id="append-conflict",
        )

    assert read_process_events(path) == [_event(EVENT_ONE)]
    assert not list(tmp_path.glob(".*.tmp"))
