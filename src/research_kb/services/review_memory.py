from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from typing import Any

from research_kb.bundle import load_workspace_entries, records_of_kind, validate_workspace_entries
from research_kb.contracts.validator import validate_record
from research_kb.errors import (
    DUPLICATE_REVIEW_MEMORY,
    GROUNDING_MISMATCH,
    INVALID_AUTHORITY,
    SCHEMA_VALIDATION_FAILED,
    UNRESOLVED_REFERENCE,
    Diagnostic,
    ResearchKBError,
)
from research_kb.identifiers import Namespace, allocate_id
from research_kb.mutation import MutationRequest
from research_kb.process_events import timestamp
from research_kb.review_memory_provenance import (
    ActiveReviewParse,
    build_active_parse_index,
    validate_review_memory_provenance,
)
from research_kb.storage.json_io import file_sha256, read_json_document, serialize_json
from research_kb.storage.transactions import TransactionManager, TransactionResult
from research_kb.workspace import WorkspaceLayout


IdAllocator = Callable[[Namespace], str]
HUMAN_REVIEW_STATES = {"human_checked", "verified"}
TOP_LEVEL_OWNED_FIELDS = {
    "schema_version",
    "review_memory_id",
    "paper_id",
    "source_type",
    "source_fingerprint",
    "parse_snapshot",
    "background_only",
    "can_enter_canonical_evidence",
    "not_fact",
    "automation_status",
    "created_at",
    "updated_at",
}
UNIT_BOUNDARIES = {
    "background_only": True,
    "can_enter_canonical_evidence": False,
    "not_fact": True,
}


class ReviewMemoryService:
    def __init__(
        self,
        layout: WorkspaceLayout,
        *,
        transaction_manager: TransactionManager | None = None,
        id_allocator: IdAllocator = allocate_id,
    ):
        self.layout = layout
        self.transactions = transaction_manager or TransactionManager(layout)
        self.id_allocator = id_allocator

    def promote(
        self,
        request: MutationRequest,
        *,
        actor: str = "agent",
    ) -> tuple[dict[str, Any], TransactionResult]:
        if request.record_kind != "review-memory" or request.operation not in {"append", "replace"}:
            raise _schema_error(request.target_record_id, "unsupported Review Memory mutation")
        if request.paper_id is None:
            raise _schema_error(request.target_record_id, "paper_id is required", "/context/paper_id")

        entries = load_workspace_entries(self.layout)
        validate_workspace_entries(entries)
        paper = next(
            (
                record
                for record in records_of_kind(entries, "registry-paper")
                if record["paper_id"] == request.paper_id
            ),
            None,
        )
        if paper is None:
            raise ResearchKBError(
                Diagnostic(
                    UNRESOLVED_REFERENCE,
                    "review-memory",
                    request.target_record_id,
                    "/paper_id",
                    "paper is not registered",
                )
            )
        self._reject_primary_route(entries, paper["paper_id"], request.target_record_id)
        all_memories = records_of_kind(entries, "review-memory")
        existing = [
            record
            for record in all_memories
            if record["paper_id"] == paper["paper_id"]
        ]
        if request.operation == "replace" and request.target_record_id is not None:
            target_memory = next(
                (
                    record
                    for record in all_memories
                    if record["review_memory_id"] == request.target_record_id
                ),
                None,
            )
            if target_memory is not None and target_memory["paper_id"] != paper["paper_id"]:
                raise ResearchKBError(
                    Diagnostic(
                        INVALID_AUTHORITY,
                        "review-memory",
                        request.target_record_id,
                        "/paper_id",
                        "target Review Memory belongs to another paper",
                    )
                )
        current: dict[str, Any] | None = None
        if request.operation == "append":
            if request.target_record_id is not None:
                raise _schema_error(request.target_record_id, "append target must be null", "/target_record_id")
            if existing:
                raise ResearchKBError(
                    Diagnostic(
                        DUPLICATE_REVIEW_MEMORY,
                        "review-memory",
                        existing[0]["review_memory_id"],
                        "/paper_id",
                        "Review Memory already exists for paper",
                    )
                )
        else:
            if request.target_record_id is None:
                raise _schema_error(None, "replace target is required", "/target_record_id")
            current = next(
                (
                    record
                    for record in existing
                    if record["review_memory_id"] == request.target_record_id
                ),
                None,
            )
            if current is None:
                raise ResearchKBError(
                    Diagnostic(
                        UNRESOLVED_REFERENCE,
                        "review-memory",
                        request.target_record_id,
                        "/review_memory_id",
                        "target Review Memory does not exist for paper",
                    )
                )
            if actor != "user" and current.get("review_status") in HUMAN_REVIEW_STATES:
                raise ResearchKBError(
                    Diagnostic(
                        INVALID_AUTHORITY,
                        "review-memory",
                        request.target_record_id,
                        "/review_status",
                        "replacing a human-reviewed Review Memory is user-only",
                    )
                )
        self._validate_payload_authority(request, actor)
        active = self._active_parse(entries, paper["paper_id"], request.target_record_id)
        source, expected_source_hash, parse_path, expected_parse_hash = self._stability_inputs(
            paper,
            request.target_record_id,
        )
        self._validate_stability(
            source,
            expected_source_hash,
            parse_path,
            expected_parse_hash,
            request.target_record_id,
        )

        if request.operation == "append":
            record = self._append_record(request, paper, active)
        else:
            assert current is not None
            if current.get("parse_snapshot") != active.snapshot and "sections" not in request.payload:
                raise ResearchKBError(
                    Diagnostic(
                        GROUNDING_MISMATCH,
                        "review-memory",
                        current["review_memory_id"],
                        "/payload/sections",
                        "refreshing a stale Review Memory requires explicit current-parse sections",
                    )
                )
            record = self._replace_record(request, paper, active, current)

        self._validate_candidate(record, active, actor)
        record["automation_status"] = "passed_auto_checks"
        target = self.layout.review_memory_path(paper["paper_id"])
        target_before = file_sha256(target)

        def validate_stability() -> None:
            self._validate_stability(
                source,
                expected_source_hash,
                parse_path,
                expected_parse_hash,
                record["review_memory_id"],
            )

        def validate_temp(path: Path) -> None:
            validate_stability()
            temporary = read_json_document(path, record_kind="review-memory")
            temporary_entries = load_workspace_entries(
                self.layout,
                overrides={target: [("review-memory", temporary)]},
            )
            validate_workspace_entries(temporary_entries)

        input_refs = (
            [paper["paper_id"], active.parse_run_id]
            if request.operation == "append"
            else [record["review_memory_id"], paper["paper_id"], active.parse_run_id]
        )
        result = self.transactions.promote_bytes(
            target=target,
            content=serialize_json(record),
            target_store="review_memories",
            operation=f"record_{request.operation}",
            actor=actor,
            input_refs=input_refs,
            output_refs=[record["review_memory_id"]],
            validator=validate_temp,
            post_replace_validator=validate_stability,
            expected_before_sha256=target_before,
        )
        return record, result

    def _append_record(
        self,
        request: MutationRequest,
        paper: dict[str, Any],
        active: ActiveReviewParse,
    ) -> dict[str, Any]:
        now = timestamp(self.transactions.clock)
        payload = deepcopy(request.payload)
        return {
            "schema_version": "1.0",
            "review_memory_id": self.id_allocator(Namespace.REVIEW_MEMORY),
            "paper_id": paper["paper_id"],
            "source_type": "review",
            **payload,
            "sections": self._normalize_sections(
                payload.get("sections"),
                existing_unit_ids=set(),
                operation="append",
            ),
            "source_fingerprint": dict(paper["source_fingerprint"]),
            "parse_snapshot": active.snapshot,
            "background_only": True,
            "can_enter_canonical_evidence": False,
            "not_fact": True,
            "automation_status": "pending",
            "created_at": now,
            "updated_at": now,
        }

    def _replace_record(
        self,
        request: MutationRequest,
        paper: dict[str, Any],
        active: ActiveReviewParse,
        current: dict[str, Any],
    ) -> dict[str, Any]:
        payload = deepcopy(request.payload)
        updated = {**deepcopy(current), **payload}
        if "sections" in payload:
            existing_unit_ids = {
                unit["review_unit_id"]
                for section in current["sections"]
                for unit in section["units"]
            }
            updated["sections"] = self._normalize_sections(
                payload["sections"],
                existing_unit_ids=existing_unit_ids,
                operation="replace",
            )
        updated.update(
            {
                "schema_version": "1.0",
                "review_memory_id": current["review_memory_id"],
                "paper_id": paper["paper_id"],
                "source_type": "review",
                "source_fingerprint": dict(paper["source_fingerprint"]),
                "parse_snapshot": active.snapshot,
                "background_only": True,
                "can_enter_canonical_evidence": False,
                "not_fact": True,
                "automation_status": "pending",
                "created_at": current["created_at"],
                "updated_at": timestamp(self.transactions.clock),
            }
        )
        return updated

    def _normalize_sections(
        self,
        value: object,
        *,
        existing_unit_ids: set[str],
        operation: str,
    ) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            raise _schema_error(None, "sections must be an array", "/payload/sections")
        normalized: list[dict[str, Any]] = []
        for section_index, source_section in enumerate(value):
            if not isinstance(source_section, dict) or not isinstance(source_section.get("units"), list):
                raise _schema_error(None, "section must contain units", f"/payload/sections/{section_index}")
            section = deepcopy(source_section)
            units: list[dict[str, Any]] = []
            for unit_index, source_unit in enumerate(section["units"]):
                if not isinstance(source_unit, dict):
                    raise _schema_error(
                        None,
                        "Review Unit must be an object",
                        f"/payload/sections/{section_index}/units/{unit_index}",
                    )
                unit = deepcopy(source_unit)
                unit_id = unit.get("review_unit_id")
                unit_path = f"/payload/sections/{section_index}/units/{unit_index}"
                if unit_id is None:
                    unit["review_unit_id"] = self.id_allocator(Namespace.REVIEW_UNIT)
                elif operation == "append" or unit_id not in existing_unit_ids:
                    raise ResearchKBError(
                        Diagnostic(
                            INVALID_AUTHORITY,
                            "review-memory",
                            unit_id if isinstance(unit_id, str) else None,
                            unit_path + "/review_unit_id",
                            "Review Unit IDs are CLI-owned",
                        )
                    )
                for field, expected in UNIT_BOUNDARIES.items():
                    if field in unit and (operation == "append" or unit[field] != expected):
                        raise ResearchKBError(
                            Diagnostic(
                                INVALID_AUTHORITY,
                                "review-memory",
                                unit.get("review_unit_id"),
                                unit_path + f"/{field}",
                                "Review Unit evidence-boundary constants are CLI-owned",
                            )
                        )
                    unit[field] = expected
                units.append(unit)
            section["units"] = units
            normalized.append(section)
        return normalized

    @staticmethod
    def _validate_payload_authority(
        request: MutationRequest,
        actor: str,
    ) -> None:
        forbidden = set(request.payload) & TOP_LEVEL_OWNED_FIELDS
        if forbidden:
            raise ResearchKBError(
                Diagnostic(
                    INVALID_AUTHORITY,
                    "review-memory",
                    request.target_record_id,
                    "/payload",
                    f"CLI-owned fields cannot be submitted: {', '.join(sorted(forbidden))}",
                )
            )
        if actor != "user" and request.payload.get("review_status") in HUMAN_REVIEW_STATES:
            raise ResearchKBError(
                Diagnostic(
                    INVALID_AUTHORITY,
                    "review-memory",
                    request.target_record_id,
                    "/payload/review_status",
                    "human-only review state",
                )
            )

    @staticmethod
    def _reject_primary_route(
        entries: list[tuple[str, dict[str, Any]]],
        paper_id: str,
        record_id: str | None,
    ) -> None:
        has_card = any(item["paper_id"] == paper_id for item in records_of_kind(entries, "paper-card"))
        has_evidence = any(item["paper_id"] == paper_id for item in records_of_kind(entries, "evidence"))
        if has_card or has_evidence:
            raise ResearchKBError(
                Diagnostic(
                    GROUNDING_MISMATCH,
                    "review-memory",
                    record_id,
                    "/paper_id",
                    "primary research and Review Memory routes are mutually exclusive",
                )
            )

    @staticmethod
    def _active_parse(
        entries: list[tuple[str, dict[str, Any]]],
        paper_id: str,
        record_id: str | None,
    ) -> ActiveReviewParse:
        active_by_paper, failures = build_active_parse_index(
            record for kind, record in entries if kind == "parsed-page"
        )
        if failures:
            failure = next((item for item in failures if item.record_id == paper_id), failures[0])
            raise ResearchKBError(
                Diagnostic(
                    failure.code,
                    failure.record_kind,
                    failure.record_id,
                    failure.json_path,
                    failure.message,
                )
            )
        active = active_by_paper.get(paper_id)
        if active is None:
            raise ResearchKBError(
                Diagnostic(
                    UNRESOLVED_REFERENCE,
                    "review-memory",
                    record_id,
                    "/parse_snapshot",
                    "paper has no active parse",
                )
            )
        return active

    def _stability_inputs(
        self,
        paper: dict[str, Any],
        record_id: str | None,
    ) -> tuple[Path, str, Path, str]:
        _, source = self.layout.resolve_source(
            paper["source_ref"]["root_id"],
            paper["source_ref"]["relative_path"],
        )
        parse_path = self.layout.parse_path(paper["paper_id"])
        parse_hash = file_sha256(parse_path)
        if parse_hash is None:
            raise ResearchKBError(
                Diagnostic(
                    UNRESOLVED_REFERENCE,
                    "review-memory",
                    record_id,
                    "/parse_snapshot",
                    "active parse store is missing",
                )
            )
        return source, paper["source_fingerprint"]["value"], parse_path, parse_hash

    @staticmethod
    def _validate_stability(
        source: Path,
        expected_source_hash: str,
        parse_path: Path,
        expected_parse_hash: str,
        record_id: str | None,
    ) -> None:
        if file_sha256(source) != expected_source_hash:
            raise ResearchKBError(
                Diagnostic(
                    GROUNDING_MISMATCH,
                    "review-memory",
                    record_id,
                    "/source_fingerprint",
                    "registered source fingerprint is stale during Review Memory promotion",
                )
            )
        if file_sha256(parse_path) != expected_parse_hash:
            raise ResearchKBError(
                Diagnostic(
                    GROUNDING_MISMATCH,
                    "review-memory",
                    record_id,
                    "/parse_snapshot",
                    "active parse changed during Review Memory promotion",
                )
            )

    @staticmethod
    def _validate_candidate(
        record: dict[str, Any],
        active: ActiveReviewParse,
        actor: str,
    ) -> None:
        diagnostics = validate_record("review-memory", record, actor=actor)
        if diagnostics:
            raise ResearchKBError(diagnostics[0])
        provenance = validate_review_memory_provenance(record, {active.paper_id: active})
        if provenance:
            failure = provenance[0]
            raise ResearchKBError(
                Diagnostic(
                    failure.code,
                    failure.record_kind,
                    failure.record_id,
                    failure.json_path,
                    failure.message,
                )
            )


def _schema_error(record_id: str | None, message: str, path: str = "") -> ResearchKBError:
    return ResearchKBError(
        Diagnostic(SCHEMA_VALIDATION_FAILED, "review-memory", record_id, path, message)
    )


__all__ = ["ReviewMemoryService"]
