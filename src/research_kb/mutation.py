from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from research_kb.config.loader import load_config
from research_kb.contracts.validator import validate_record
from research_kb.errors import ResearchKBError


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
    return mutation_request_from_mapping(data)


def mutation_request_from_mapping(data: Mapping[str, Any]) -> MutationRequest:
    value = dict(data)
    diagnostics = validate_record("mutation-request", value)
    if diagnostics:
        raise ResearchKBError(diagnostics[0])
    return MutationRequest(
        operation=value["operation"],
        record_kind=value["record_kind"],
        target_record_id=value["target_record_id"],
        paper_id=value["context"]["paper_id"],
        payload=value["payload"],
        question_origin=value["context"].get("question_origin"),
        fixture_origin=value.get("fixture_origin"),
    )
