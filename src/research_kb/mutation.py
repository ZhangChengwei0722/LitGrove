from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from research_kb.config.loader import load_config


@dataclass(frozen=True, slots=True)
class MutationRequest:
    operation: str
    record_kind: str
    target_record_id: str | None
    paper_id: str | None
    payload: dict[str, Any]
    question_origin: str | None = None
    fixture_origin: str | None = None


def load_mutation_request(path: Path) -> MutationRequest:
    data = load_config(path, "mutation-request").data
    return MutationRequest(
        operation=data["operation"],
        record_kind=data["record_kind"],
        target_record_id=data["target_record_id"],
        paper_id=data["context"]["paper_id"],
        payload=data["payload"],
        question_origin=data["context"].get("question_origin"),
        fixture_origin=data.get("fixture_origin"),
    )
