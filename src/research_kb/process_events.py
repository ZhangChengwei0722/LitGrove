from __future__ import annotations

import os
import shutil
import stat
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from research_kb.contracts.validator import validate_record
from research_kb.errors import SCHEMA_VALIDATION_FAILED, Diagnostic, ResearchKBError
from research_kb.storage.json_io import (
    ensure_private_directory,
    iter_jsonl,
    read_jsonl,
    replace_temp,
    serialize_json,
)


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


def read_process_event_subset(
    path: Path,
    event_ids: set[str],
) -> dict[str, dict[str, Any]]:
    selected: dict[str, dict[str, Any]] = {}
    for event in iter_jsonl(
        path,
        record_kind="process-event",
        id_field="event_id",
    ):
        if event["event_id"] in event_ids:
            selected[event["event_id"]] = event
    return selected


def append_process_event(path: Path, event: dict[str, Any], *, write_id: str) -> None:
    diagnostics = validate_record("process-event", event, actor="cli")
    if diagnostics:
        raise ResearchKBError(diagnostics[0])
    event_id = event["event_id"]
    matching_event: dict[str, Any] | None = None
    for existing in iter_jsonl(
        path,
        record_kind="process-event",
        id_field="event_id",
    ):
        if existing["event_id"] == event_id:
            matching_event = existing
    if matching_event is not None:
        if matching_event == event:
            return
        raise ResearchKBError(
            Diagnostic(
                SCHEMA_VALIDATION_FAILED,
                "process-event",
                event_id,
                "/event_id",
                "event ID already exists with different content",
            )
        )
    ensure_private_directory(path.parent)
    temporary = path.parent / f".{path.name}.{write_id}.tmp"
    target_mode = (
        stat.S_IMODE(path.stat().st_mode)
        if os.name == "posix" and path.exists()
        else 0o600
    )
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            if path.exists():
                with path.open("rb") as source:
                    shutil.copyfileobj(source, handle, length=1024 * 1024)
            handle.write(serialize_json(event))
            handle.flush()
            if os.name == "posix":
                os.fchmod(handle.fileno(), target_mode)
            os.fsync(handle.fileno())
        replace_temp(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
