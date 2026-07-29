from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from research_kb.contracts.registry import SchemaRegistry
from research_kb.contracts.validator import validate_bundle, validate_record
from research_kb.errors import (
    UNKNOWN_SCHEMA_KIND,
    UNSUPPORTED_VERSION,
    Diagnostic,
    ResearchKBError,
)
from research_kb.storage.json_io import read_jsonl


ID_FIELDS = {
    "registry-paper": "paper_id",
    "evidence": "evidence_id",
    "review-queue": "queue_id",
    "process-event": "event_id",
    "guardian-report": "guardian_report_id",
    "question-mapping": "question_id",
    "step7-synthesis": "candidate_id",
    "step7-review-angle": "candidate_id",
    "step7-insight": "candidate_id",
    "step7-cross-view": "candidate_id",
    "discovery-candidate": "candidate_id",
}


@dataclass(frozen=True, slots=True)
class ContractValidationResult:
    diagnostics: tuple[Diagnostic, ...]
    exit_code: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": "success" if not self.diagnostics else "failure",
            "diagnostics": [item.to_dict() for item in self.diagnostics],
        }


@dataclass(frozen=True, slots=True)
class JsonlValidationResult:
    records: int
    diagnostics: tuple[Diagnostic, ...]
    exit_code: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": "success" if not self.diagnostics else "failure",
            "records": self.records,
            "diagnostics": [item.to_dict() for item in self.diagnostics],
        }


class ContractValidationService:
    def __init__(self, registry: SchemaRegistry | None = None):
        self.registry = registry or SchemaRegistry()

    def validate(
        self,
        *,
        kind: str,
        record: Mapping[str, Any],
        bundle: Mapping[str, Any] | None,
        actor: str,
    ) -> ContractValidationResult:
        diagnostics = validate_record(kind, record, registry=self.registry, actor=actor)
        if bundle is not None:
            diagnostics.extend(validate_bundle(bundle, registry=self.registry, actor=actor))
        unique = _deduplicate_diagnostics(diagnostics)
        return ContractValidationResult(unique, _validation_exit_code(unique))


class JsonlValidationService:
    def check(self, *, path: Path, kind: str, actor: str) -> JsonlValidationResult:
        try:
            records = read_jsonl(
                path,
                record_kind=kind,
                missing_ok=False,
                id_field=ID_FIELDS.get(kind),
            )
        except ResearchKBError as error:
            return JsonlValidationResult(0, (error.diagnostic,), 1)
        diagnostics: list[Diagnostic] = []
        for record in records:
            diagnostics.extend(validate_record(kind, record, actor=actor))
        retained = tuple(diagnostics)
        return JsonlValidationResult(len(records), retained, _validation_exit_code(retained))


def _deduplicate_diagnostics(diagnostics: list[Diagnostic]) -> tuple[Diagnostic, ...]:
    unique: list[Diagnostic] = []
    seen: set[tuple[str, str, str | None, str, str]] = set()
    for diagnostic in diagnostics:
        key = (
            diagnostic.code,
            diagnostic.record_kind,
            diagnostic.record_id,
            diagnostic.json_path,
            diagnostic.message,
        )
        if key not in seen:
            seen.add(key)
            unique.append(diagnostic)
    return tuple(unique)


def _validation_exit_code(diagnostics: tuple[Diagnostic, ...]) -> int:
    if any(item.code in {UNSUPPORTED_VERSION, UNKNOWN_SCHEMA_KIND} for item in diagnostics):
        return 3
    return 0 if not diagnostics else 1
