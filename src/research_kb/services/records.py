from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from research_kb.bundle import load_workspace_entries, records_of_kind, validate_workspace_entries
from research_kb.contracts.validator import validate_record
from research_kb.errors import (
    DUPLICATE_PAPER_CARD,
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
from research_kb.services.registry import RegistryService
from research_kb.storage.json_io import file_sha256, read_jsonl, serialize_json, serialize_jsonl
from research_kb.storage.transactions import TransactionManager, TransactionResult
from research_kb.workspace import WorkspaceLayout


IdAllocator = Callable[[Namespace], str]
SUPPORTED_KINDS = {
    "registry-paper",
    "paper-card",
    "evidence",
    "review-queue",
    "review-memory",
    "question-mapping",
}
HUMAN_REVIEW_STATES = {"human_checked", "verified"}
COMMON_OWNED_FIELDS = {"schema_version", "automation_status", "created_at", "updated_at", "paper_id"}
KIND_OWNED_FIELDS = {
    "paper-card": {"domain_profile_id"},
    "evidence": {"evidence_id", "source_fingerprint", "source_type", "canonical"},
    "review-queue": {"queue_id", "not_evidence"},
}


class RecordService:
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
        self.registry = RegistryService(
            layout,
            transaction_manager=self.transactions,
            id_allocator=id_allocator,
        )

    def promote(
        self,
        request: MutationRequest,
        *,
        actor: str = "agent",
    ) -> tuple[dict[str, Any], TransactionResult]:
        self._validate_request_shape(request)
        if request.record_kind == "registry-paper":
            return self._promote_registry(request, actor)
        if request.record_kind == "question-mapping":
            from research_kb.services.question_mapping import QuestionMappingService

            return QuestionMappingService(
                self.layout,
                transaction_manager=self.transactions,
                id_allocator=self.id_allocator,
            ).promote(request, actor=actor)
        if request.record_kind == "review-memory":
            from research_kb.services.review_memory import ReviewMemoryService

            return ReviewMemoryService(
                self.layout,
                transaction_manager=self.transactions,
                id_allocator=self.id_allocator,
            ).promote(request, actor=actor)

        entries = load_workspace_entries(self.layout)
        validate_workspace_entries(entries)
        paper = self._resolve_paper(entries, request)
        if request.record_kind in {"paper-card", "evidence"} and any(
            record["paper_id"] == paper["paper_id"]
            for record in records_of_kind(entries, "review-memory")
        ):
            raise ResearchKBError(
                Diagnostic(
                    GROUNDING_MISMATCH,
                    request.record_kind,
                    request.target_record_id,
                    "/paper_id",
                    "primary research and Review Memory routes are mutually exclusive",
                )
            )
        self._validate_payload_authority(request, actor)
        if request.operation == "append":
            return self._append(request, actor, entries, paper)
        return self._replace(request, actor, entries, paper)

    def _promote_registry(
        self,
        request: MutationRequest,
        actor: str,
    ) -> tuple[dict[str, Any], TransactionResult]:
        payload = dict(request.payload)
        if request.operation == "append":
            source_ref = payload.pop("source_ref", None)
            if not isinstance(source_ref, dict):
                raise ResearchKBError(
                    Diagnostic(SCHEMA_VALIDATION_FAILED, "registry-paper", None, "/payload/source_ref", "source_ref is required")
                )
            return self.registry.add(
                root_id=source_ref.get("root_id", ""),
                relative_path=source_ref.get("relative_path", ""),
                metadata=payload,
                actor=actor,
            )
        assert request.target_record_id is not None
        return self.registry.replace(
            paper_id=request.target_record_id,
            changes=payload,
            actor=actor,
        )

    def _append(
        self,
        request: MutationRequest,
        actor: str,
        entries: list[tuple[str, dict[str, Any]]],
        paper: dict[str, Any],
    ) -> tuple[dict[str, Any], TransactionResult]:
        now = timestamp(self.transactions.clock)
        payload = dict(request.payload)
        if request.record_kind == "paper-card":
            if any(record["paper_id"] == paper["paper_id"] for record in records_of_kind(entries, "paper-card")):
                raise ResearchKBError(
                    Diagnostic(DUPLICATE_PAPER_CARD, "paper-card", paper["paper_id"], "/paper_id", "Paper Card already exists")
                )
            profile = records_of_kind(entries, "domain-profile")[0]
            record = {
                "schema_version": "1.0",
                "paper_id": paper["paper_id"],
                "domain_profile_id": profile["domain_profile"]["id"],
                **payload,
                "sections": self._normalize_sections(payload.get("sections"), existing_unit_ids=set()),
                "automation_status": "pending",
                "created_at": now,
                "updated_at": now,
            }
            target = self.layout.paper_card_path(paper["paper_id"])
            proposed = [record]
        elif request.record_kind == "evidence":
            record = {
                "schema_version": "1.0",
                "evidence_id": self.id_allocator(Namespace.EVIDENCE),
                "paper_id": paper["paper_id"],
                **payload,
                "source_type": "primary",
                "canonical": True,
                "source_fingerprint": dict(paper["source_fingerprint"]),
                "automation_status": "pending",
                "created_at": now,
                "updated_at": now,
            }
            target = self.layout.evidence_path(paper["paper_id"])
            proposed = [*read_jsonl(target, record_kind="evidence", id_field="evidence_id"), record]
        else:
            record = {
                "schema_version": "1.0",
                "queue_id": self.id_allocator(Namespace.QUEUE),
                "paper_id": paper["paper_id"],
                **payload,
                "not_evidence": True,
                "automation_status": "pending",
                "created_at": now,
                "updated_at": now,
            }
            target = self.layout.review_queue_path
            proposed = [*read_jsonl(target, record_kind="review-queue", id_field="queue_id"), record]
        self._validate_candidate(request.record_kind, record, actor)
        record["automation_status"] = "passed_auto_checks"
        return self._promote_store(request, actor, target, proposed, record, paper)

    def _replace(
        self,
        request: MutationRequest,
        actor: str,
        entries: list[tuple[str, dict[str, Any]]],
        paper: dict[str, Any],
    ) -> tuple[dict[str, Any], TransactionResult]:
        assert request.target_record_id is not None
        records = records_of_kind(entries, request.record_kind)
        id_field = "paper_id" if request.record_kind == "paper-card" else (
            "evidence_id" if request.record_kind == "evidence" else "queue_id"
        )
        existing = next((record for record in records if record[id_field] == request.target_record_id), None)
        if existing is None:
            raise ResearchKBError(
                Diagnostic(UNRESOLVED_REFERENCE, request.record_kind, request.target_record_id, f"/{id_field}", "target record does not exist")
            )
        if existing["paper_id"] != paper["paper_id"]:
            raise ResearchKBError(
                Diagnostic(INVALID_AUTHORITY, request.record_kind, request.target_record_id, "/paper_id", "target belongs to another paper")
            )
        if actor != "user" and existing.get("review_status") in HUMAN_REVIEW_STATES:
            raise ResearchKBError(
                Diagnostic(INVALID_AUTHORITY, request.record_kind, request.target_record_id, "/review_status", "replacing a human-reviewed record is user-only")
            )
        updated = {**existing, **request.payload}
        if request.record_kind == "paper-card" and "sections" in request.payload:
            existing_unit_ids = {
                unit["unit_id"]
                for section in existing["sections"]
                for unit in section["units"]
            }
            updated["sections"] = self._normalize_sections(request.payload["sections"], existing_unit_ids=existing_unit_ids)
        updated["automation_status"] = "pending"
        updated["updated_at"] = timestamp(self.transactions.clock)
        self._validate_candidate(request.record_kind, updated, actor)
        updated["automation_status"] = "passed_auto_checks"

        if request.record_kind == "paper-card":
            target = self.layout.paper_card_path(paper["paper_id"])
            proposed = [updated]
        elif request.record_kind == "evidence":
            target = self.layout.evidence_path(paper["paper_id"])
            store = read_jsonl(target, record_kind="evidence", id_field="evidence_id")
            proposed = [updated if item["evidence_id"] == request.target_record_id else item for item in store]
        else:
            target = self.layout.review_queue_path
            store = read_jsonl(target, record_kind="review-queue", id_field="queue_id")
            proposed = [updated if item["queue_id"] == request.target_record_id else item for item in store]
        return self._promote_store(request, actor, target, proposed, updated, paper)

    def _promote_store(
        self,
        request: MutationRequest,
        actor: str,
        target: Path,
        proposed: list[dict[str, Any]],
        record: dict[str, Any],
        paper: dict[str, Any],
    ) -> tuple[dict[str, Any], TransactionResult]:
        validate_source_stability: Callable[[], None] | None = None
        if request.record_kind == "evidence":
            _, source = self.layout.resolve_source(
                paper["source_ref"]["root_id"],
                paper["source_ref"]["relative_path"],
            )
            expected_hash = paper["source_fingerprint"]["value"]

            def validate_source_stability() -> None:
                if file_sha256(source) != expected_hash:
                    raise ResearchKBError(
                        Diagnostic(
                            GROUNDING_MISMATCH,
                            "evidence",
                            self._record_id(request.record_kind, record),
                            "/source_fingerprint",
                            "registered source fingerprint is stale during Evidence promotion",
                        )
                    )

            validate_source_stability()

        target_before = file_sha256(target)
        content = serialize_json(record) if request.record_kind == "paper-card" else serialize_jsonl(proposed)
        output_id = self._record_id(request.record_kind, record)

        def validate_temp(path: Path) -> None:
            if validate_source_stability is not None:
                validate_source_stability()
            if request.record_kind == "paper-card":
                from research_kb.storage.json_io import read_json_document

                temporary = read_json_document(path, record_kind="paper-card")
                override = [("paper-card", temporary)]
            else:
                id_field = "evidence_id" if request.record_kind == "evidence" else "queue_id"
                temporary = read_jsonl(path, record_kind=request.record_kind, missing_ok=False, id_field=id_field)
                override = [(request.record_kind, item) for item in temporary]
            entries = load_workspace_entries(self.layout, overrides={target: override})
            validate_workspace_entries(entries)

        input_refs = [record["paper_id"]] if request.operation == "append" else [output_id]
        result = self.transactions.promote_bytes(
            target=target,
            content=content,
            target_store={
                "paper-card": "paper_cards",
                "evidence": "evidence",
                "review-queue": "review_queue",
            }[request.record_kind],
            operation=f"record_{request.operation}",
            actor=actor,
            input_refs=input_refs,
            output_refs=[output_id],
            validator=validate_temp,
            post_replace_validator=validate_source_stability,
            expected_before_sha256=target_before,
        )
        return record, result

    def _normalize_sections(
        self,
        value: object,
        *,
        existing_unit_ids: set[str],
    ) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            raise ResearchKBError(
                Diagnostic(SCHEMA_VALIDATION_FAILED, "paper-card", None, "/sections", "sections must be an array")
            )
        normalized: list[dict[str, Any]] = []
        for section_index, section in enumerate(value):
            if not isinstance(section, dict) or not isinstance(section.get("units"), list):
                raise ResearchKBError(
                    Diagnostic(SCHEMA_VALIDATION_FAILED, "paper-card", None, f"/sections/{section_index}", "section must contain units")
                )
            units = []
            for unit_index, source_unit in enumerate(section["units"]):
                if not isinstance(source_unit, dict):
                    raise ResearchKBError(
                        Diagnostic(SCHEMA_VALIDATION_FAILED, "paper-card", None, f"/sections/{section_index}/units/{unit_index}", "Card Unit must be an object")
                    )
                unit = dict(source_unit)
                unit_id = unit.get("unit_id")
                if unit_id is None:
                    unit["unit_id"] = self.id_allocator(Namespace.UNIT)
                elif unit_id not in existing_unit_ids:
                    raise ResearchKBError(
                        Diagnostic(INVALID_AUTHORITY, "paper-card", unit_id, f"/sections/{section_index}/units/{unit_index}/unit_id", "Card Unit IDs are CLI-owned")
                    )
                units.append(unit)
            normalized.append({**section, "units": units})
        return normalized

    @staticmethod
    def _validate_request_shape(request: MutationRequest) -> None:
        if request.operation not in {"append", "replace"} or request.record_kind not in SUPPORTED_KINDS:
            raise ResearchKBError(
                Diagnostic(SCHEMA_VALIDATION_FAILED, "mutation-request", request.target_record_id, "", "unsupported mutation request")
            )
        if request.operation == "append" and request.target_record_id is not None:
            raise ResearchKBError(
                Diagnostic(SCHEMA_VALIDATION_FAILED, "mutation-request", request.target_record_id, "/target_record_id", "append target must be null")
            )
        if request.operation == "replace" and request.target_record_id is None:
            raise ResearchKBError(
                Diagnostic(SCHEMA_VALIDATION_FAILED, "mutation-request", None, "/target_record_id", "replace target is required")
            )
        if request.record_kind != "question-mapping" and request.question_origin is not None:
            raise ResearchKBError(
                Diagnostic(
                    SCHEMA_VALIDATION_FAILED,
                    "mutation-request",
                    request.target_record_id,
                    "/context/question_origin",
                    "question_origin is only valid for question mappings",
                )
            )

    @staticmethod
    def _resolve_paper(
        entries: list[tuple[str, dict[str, Any]]],
        request: MutationRequest,
    ) -> dict[str, Any]:
        if request.paper_id is None:
            raise ResearchKBError(
                Diagnostic(SCHEMA_VALIDATION_FAILED, request.record_kind, request.target_record_id, "/context/paper_id", "paper_id is required")
            )
        paper = next(
            (record for record in records_of_kind(entries, "registry-paper") if record["paper_id"] == request.paper_id),
            None,
        )
        if paper is None:
            raise ResearchKBError(
                Diagnostic(UNRESOLVED_REFERENCE, request.record_kind, request.target_record_id, "/paper_id", "paper is not registered")
            )
        return paper

    @staticmethod
    def _validate_payload_authority(request: MutationRequest, actor: str) -> None:
        owned = COMMON_OWNED_FIELDS | KIND_OWNED_FIELDS[request.record_kind]
        forbidden = set(request.payload) & owned
        if forbidden:
            raise ResearchKBError(
                Diagnostic(
                    INVALID_AUTHORITY,
                    request.record_kind,
                    request.target_record_id,
                    "/payload",
                    f"CLI-owned fields cannot be submitted: {', '.join(sorted(forbidden))}",
                )
            )
        if actor != "user" and request.payload.get("review_status") in HUMAN_REVIEW_STATES:
            raise ResearchKBError(
                Diagnostic(INVALID_AUTHORITY, request.record_kind, request.target_record_id, "/review_status", "human-only review state")
            )

    @staticmethod
    def _validate_candidate(kind: str, record: dict[str, Any], actor: str) -> None:
        diagnostics = validate_record(kind, record, actor=actor)
        if diagnostics:
            raise ResearchKBError(diagnostics[0])

    @staticmethod
    def _record_id(kind: str, record: dict[str, Any]) -> str:
        id_field = {
            "paper-card": "paper_id",
            "evidence": "evidence_id",
            "review-queue": "queue_id",
        }[kind]
        return record[id_field]
