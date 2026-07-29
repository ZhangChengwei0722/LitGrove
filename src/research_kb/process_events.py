from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from research_kb.contracts.validator import validate_record
from research_kb.errors import SCHEMA_VALIDATION_FAILED, Diagnostic, ResearchKBError
from research_kb.storage.json_io import atomic_write_bytes, read_jsonl, serialize_jsonl


Clock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def timestamp(clock: Clock = utc_now) -> str:
    return clock().isoformat().replace("+00:00", "Z")


def build_process_event(
    *,
    event_id: str,
    operation: str,
    actor: str,
    result: str,
    input_refs: list[str],
    output_refs: list[str],
    created_at: str,
    job_id: str | None = None,
) -> dict[str, Any]:
    event = {
        "schema_version": "1.0",
        "event_id": event_id,
        "operation": operation,
        "actor": actor,
        "result": result,
        "input_refs": input_refs,
        "output_refs": output_refs,
        "created_at": created_at,
    }
    if job_id is not None:
        event["job_id"] = job_id
    diagnostics = validate_record("process-event", event, actor="cli")
    if diagnostics:
        raise ResearchKBError(diagnostics[0])
    return event


def read_process_events(path: Path) -> list[dict[str, Any]]:
    return read_jsonl(path, record_kind="process-event", id_field="event_id")


def append_process_event(path: Path, event: dict[str, Any], *, write_id: str) -> None:
    diagnostics = validate_record("process-event", event, actor="cli")
    if diagnostics:
        raise ResearchKBError(diagnostics[0])
    events = read_process_events(path)
    event_id = event["event_id"]
    if any(item["event_id"] == event_id for item in events):
        existing = next(item for item in events if item["event_id"] == event_id)
        if existing == event:
            return
        raise ResearchKBError(
            Diagnostic(SCHEMA_VALIDATION_FAILED, "process-event", event_id, "/event_id", "event ID already exists with different content")
        )
    atomic_write_bytes(path, serialize_jsonl([*events, event]), write_id)
