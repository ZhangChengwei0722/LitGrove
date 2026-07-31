from __future__ import annotations

import re
import uuid
from collections.abc import Callable, Iterable
from enum import StrEnum

from research_kb.errors import DUPLICATE_ID, SCHEMA_VALIDATION_FAILED, Diagnostic, ResearchKBError


class Namespace(StrEnum):
    WORKSPACE = "workspace"
    PAPER = "paper"
    UNIT = "unit"
    REVIEW_MEMORY = "reviewmem"
    REVIEW_UNIT = "reviewunit"
    EVIDENCE = "evidence"
    QUEUE = "queue"
    QUESTION = "question"
    QUESTION_LINK = "qlink"
    SYNTHESIS = "synthesis"
    REVIEW_ANGLE = "angle"
    INSIGHT = "insight"
    CROSS_VIEW = "crossview"
    DISCOVERY = "discovery"
    PROCESS_EVENT = "event"
    GUARDIAN_REPORT = "guardian"
    JOB = "job"
    JOB_STATE = "jobstate"
    GUARDIAN_DISPOSITION = "gdisp"
    SOURCE_ASSET = "sourceasset"
    SOURCE_ASSET_STATE = "sourceassetstate"
    IDENTITY_CORRECTION = "identitycorr"
    SOURCE_ADEQUACY = "adequacy"
    AGENT_TASK = "task"
    AGENT_TASK_STATE = "taskstate"
    PRIMARY_REVISION = "primaryrev"


UUID4_PATTERN = r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
ID_PATTERN = re.compile(rf"^(?P<namespace>{'|'.join(item.value for item in Namespace)})_(?P<uuid>{UUID4_PATTERN})$")


def allocate_id(namespace: Namespace | str, uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4) -> str:
    namespace_value = Namespace(namespace).value
    generated = uuid_factory()
    if generated.version != 4:
        raise ValueError("uuid_factory must return a UUID4 value")
    return f"{namespace_value}_{generated}"


def validate_id(value: str, namespace: Namespace | str | None = None) -> str:
    match = ID_PATTERN.fullmatch(value) if isinstance(value, str) else None
    expected = Namespace(namespace).value if namespace is not None else None
    if match is None or (expected is not None and match.group("namespace") != expected):
        raise ResearchKBError(
            Diagnostic(
                code=SCHEMA_VALIDATION_FAILED,
                record_kind="identifier",
                record_id=value if isinstance(value, str) else None,
                json_path="",
                message=f"invalid identifier for namespace {expected or 'any'}",
            )
        )
    return value


def ensure_unique(values: Iterable[str], record_kind: str = "identifier") -> None:
    seen: set[str] = set()
    for value in values:
        if value in seen:
            raise ResearchKBError(
                Diagnostic(
                    code=DUPLICATE_ID,
                    record_kind=record_kind,
                    record_id=value,
                    json_path="",
                    message="duplicate identifier",
                )
            )
        seen.add(value)
