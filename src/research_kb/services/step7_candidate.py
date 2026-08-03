from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from typing import Any

from research_kb.bundle import BundleEntry, load_workspace_entries, records_of_kind, validate_workspace_entries
from research_kb.contracts.validator import validate_record
from research_kb.errors import (
    INVALID_AUTHORITY,
    SCHEMA_VALIDATION_FAILED,
    STEP7_BOUNDARY,
    UNRESOLVED_REFERENCE,
    Diagnostic,
    ResearchKBError,
)
from research_kb.identifiers import Namespace, allocate_id, validate_id
from research_kb.mutation import MutationRequest
from research_kb.primary_bundles import expand_active_primary_entries
from research_kb.process_events import timestamp
from research_kb.step7_support import (
    STEP7_KIND_TO_NAMESPACE,
    STEP7_KIND_TO_STORE,
    STEP7_KIND_TO_TYPE,
    STEP7_RECORD_KINDS,
    SupportClosure,
    candidate_freshness,
    derive_support_closure,
    validate_cross_view_sources,
)
from research_kb.storage.json_io import file_sha256, read_jsonl, serialize_jsonl
from research_kb.storage.transactions import TransactionManager, TransactionResult
from research_kb.workspace import WorkspaceLayout


IdAllocator = Callable[[Namespace], str]
COMMON_FIELDS = {
    "question_id",
    "title",
    "candidate_status",
    "analysis_operator",
    "paper_card_base",
    "missing_evidence",
    "assumptions",
    "risk",
    "testability",
    "next_action",
    "trace_status",
}
OPTIONAL_FIELDS = {"rejection_rationale", "review_background_unit_ids"}
TYPE_FIELDS = {
    "step7-synthesis": {
        "claim",
        "scope",
        "agreement_pattern",
        "conflict_pattern",
        "boundary_statement",
    },
    "step7-review-angle": {
        "thesis",
        "organizing_axes",
        "included_clusters",
        "excluded_scope",
        "why_this_angle_adds_value",
    },
    "step7-insight": {
        "insight_type",
        "hypothesis_or_idea",
        "rationale",
        "falsification_condition",
        "minimum_test",
    },
    "step7-cross-view": {
        "source_views",
        "relation_type",
        "why_interesting",
        "shared_dimension",
        "non_equivalence_warning",
    },
}
OWNED_FIELDS = {
    "schema_version",
    "candidate_id",
    "type",
    "evidence_base",
    "review_queue_refs",
    "review_background_base",
    "input_snapshot",
    "not_fact",
    "review_status",
    "automation_status",
    "created_at",
    "updated_at",
    "fixture_origin",
    "approval",
}


class Step7CandidateService:
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
        approval: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], TransactionResult]:
        if approval is not None and actor != "user":
            raise ResearchKBError(
                Diagnostic(
                    INVALID_AUTHORITY,
                    request.record_kind,
                    request.target_record_id,
                    "/approval",
                    "Research Synthesis approval receipts require user authority",
                )
            )
        self._validate_request(request)
        self._validate_payload(request)
        entries = load_workspace_entries(self.layout)
        validate_workspace_entries(entries)

        existing = self._existing_candidate(request, entries)
        if existing is not None and existing.get("approval") is not None and approval is None:
            raise ResearchKBError(
                Diagnostic(
                    INVALID_AUTHORITY,
                    request.record_kind,
                    existing["candidate_id"],
                    "/approval",
                    "an approved Research Synthesis candidate requires a new user-approved proposal to replace it",
                )
            )
        question_id = request.payload["question_id"]
        if existing is not None and question_id != existing["question_id"]:
            raise ResearchKBError(
                Diagnostic(
                    INVALID_AUTHORITY,
                    request.record_kind,
                    existing["candidate_id"],
                    "/payload/question_id",
                    "Research Synthesis replacement cannot move a candidate to another question",
                )
            )

        closure = derive_support_closure(
            entries,
            question_id=question_id,
            paper_card_base=request.payload["paper_card_base"],
            review_background_unit_ids=request.payload.get("review_background_unit_ids", []),
            record_kind=request.record_kind,
            record_id=request.target_record_id,
        )
        source_views: tuple[str, ...] = ()
        if request.record_kind == "step7-cross-view":
            source_views = validate_cross_view_sources(
                entries,
                question_id=question_id,
                source_views=request.payload["source_views"],
                record_kind=request.record_kind,
                record_id=request.target_record_id,
            )
        if request.record_kind == "step7-synthesis" and len(closure.paper_card_base) < 2:
            raise ResearchKBError(
                Diagnostic(
                    STEP7_BOUNDARY,
                    request.record_kind,
                    request.target_record_id,
                    "/payload/paper_card_base",
                    "Synthesis requires at least two distinct papers",
                )
            )

        record = self._build_record(request, closure, existing, source_views, approval)
        diagnostics = validate_record(request.record_kind, record, actor=actor)
        if diagnostics:
            raise ResearchKBError(diagnostics[0])
        if candidate_freshness(record, [*entries, (request.record_kind, record)])["state"] != "current":
            raise ResearchKBError(
                Diagnostic(
                    STEP7_BOUNDARY,
                    request.record_kind,
                    record["candidate_id"],
                    "/input_snapshot",
                    "new Research Synthesis candidate is stale before promotion",
                )
            )
        return self._promote_store(request, entries, record, closure, source_views, actor)

    def _existing_candidate(
        self,
        request: MutationRequest,
        entries: list[BundleEntry],
    ) -> dict[str, Any] | None:
        if request.operation == "append":
            return None
        assert request.target_record_id is not None
        validate_id(request.target_record_id, STEP7_KIND_TO_NAMESPACE[request.record_kind])
        existing = next(
            (
                item
                for item in records_of_kind(entries, request.record_kind)
                if item["candidate_id"] == request.target_record_id
            ),
            None,
        )
        if existing is None:
            raise ResearchKBError(
                Diagnostic(
                    UNRESOLVED_REFERENCE,
                    request.record_kind,
                    request.target_record_id,
                    "/target_record_id",
                    "target Research Synthesis candidate does not exist",
                )
            )
        return existing

    def _build_record(
        self,
        request: MutationRequest,
        closure: SupportClosure,
        existing: dict[str, Any] | None,
        source_views: tuple[str, ...],
        approval: dict[str, Any] | None,
    ) -> dict[str, Any]:
        now = timestamp(self.transactions.clock)
        payload = deepcopy(request.payload)
        payload.pop("review_background_unit_ids", None)
        payload["paper_card_base"] = [dict(item) for item in closure.paper_card_base]
        if request.record_kind == "step7-cross-view":
            payload["source_views"] = list(source_views)
        payload.setdefault("rejection_rationale", None)
        record = {
            "schema_version": "1.0",
            "candidate_id": (
                existing["candidate_id"]
                if existing is not None
                else self.id_allocator(STEP7_KIND_TO_NAMESPACE[request.record_kind])
            ),
            "type": STEP7_KIND_TO_TYPE[request.record_kind],
            **payload,
            "evidence_base": list(closure.evidence_base),
            "review_queue_refs": list(closure.review_queue_refs),
            **(
                {"review_background_base": [dict(item) for item in closure.review_background_base]}
                if closure.review_background_base
                else {}
            ),
            "input_snapshot": deepcopy(closure.input_snapshot),
            "not_fact": True,
            "review_status": "ai_draft",
            "automation_status": "pending",
            "created_at": existing["created_at"] if existing is not None else now,
            "updated_at": now,
        }
        if request.fixture_origin is not None:
            record["fixture_origin"] = request.fixture_origin
        elif existing is not None and "fixture_origin" in existing:
            record["fixture_origin"] = existing["fixture_origin"]
        if approval is not None:
            record["approval"] = deepcopy(approval)
        return record

    def _promote_store(
        self,
        request: MutationRequest,
        entries: list[BundleEntry],
        record: dict[str, Any],
        closure: SupportClosure,
        source_views: tuple[str, ...],
        actor: str,
    ) -> tuple[dict[str, Any], TransactionResult]:
        target = self.layout.step7_store_path(request.record_kind)
        target_before = file_sha256(target)
        existing = read_jsonl(target, record_kind=request.record_kind, id_field="candidate_id")
        proposed = (
            [*existing, record]
            if request.operation == "append"
            else [record if item["candidate_id"] == record["candidate_id"] else item for item in existing]
        )
        proposed.sort(key=lambda item: item["candidate_id"])
        initial_signature = _upstream_signature(entries, closure, source_views)

        def validate_temp(path: Path) -> None:
            temporary = read_jsonl(
                path,
                record_kind=request.record_kind,
                missing_ok=False,
                id_field="candidate_id",
            )
            current_entries = load_workspace_entries(
                self.layout,
                overrides={target: [(request.record_kind, item) for item in temporary]},
            )
            validate_workspace_entries(current_entries)
            current_record = next(item for item in temporary if item["candidate_id"] == record["candidate_id"])
            current_closure = derive_support_closure(
                current_entries,
                question_id=current_record["question_id"],
                paper_card_base=current_record["paper_card_base"],
                review_background_unit_ids=[
                    unit_id
                    for item in current_record.get("review_background_base", [])
                    for unit_id in item.get("review_unit_ids", [])
                ],
                record_kind=request.record_kind,
                record_id=current_record["candidate_id"],
            )
            current_sources: tuple[str, ...] = ()
            if request.record_kind == "step7-cross-view":
                current_sources = validate_cross_view_sources(
                    current_entries,
                    question_id=current_record["question_id"],
                    source_views=current_record["source_views"],
                    record_kind=request.record_kind,
                    record_id=current_record["candidate_id"],
                )
            if (
                list(current_closure.paper_card_base) != current_record["paper_card_base"]
                or list(current_closure.evidence_base) != current_record["evidence_base"]
                or list(current_closure.review_queue_refs) != current_record["review_queue_refs"]
                or list(current_closure.review_background_base) != current_record.get("review_background_base", [])
                or current_closure.input_snapshot != current_record["input_snapshot"]
            ):
                raise ResearchKBError(
                    Diagnostic(
                        STEP7_BOUNDARY,
                        request.record_kind,
                        current_record["candidate_id"],
                        "/input_snapshot",
                        "Research Synthesis support closure changed during promotion",
                    )
                )
            if _upstream_signature(current_entries, current_closure, current_sources) != initial_signature:
                raise ResearchKBError(
                    Diagnostic(
                        STEP7_BOUNDARY,
                        request.record_kind,
                        current_record["candidate_id"],
                        "/input_snapshot",
                        "Research Synthesis upstream records changed during promotion",
                    )
                )
            if candidate_freshness(current_record, current_entries)["state"] != "current":
                raise ResearchKBError(
                    Diagnostic(
                        STEP7_BOUNDARY,
                        request.record_kind,
                        current_record["candidate_id"],
                        "/input_snapshot",
                        "Research Synthesis candidate became stale during promotion",
                    )
                )

        input_refs = set(closure.upstream_refs)
        input_refs.update(source_views)
        if request.operation == "replace":
            input_refs.add(record["candidate_id"])
        result = self.transactions.promote_bytes(
            target=target,
            content=serialize_jsonl(proposed),
            target_store=STEP7_KIND_TO_STORE[request.record_kind],
            operation=f"record_{request.operation}",
            actor=actor,
            input_refs=sorted(input_refs),
            output_refs=[record["candidate_id"]],
            validator=validate_temp,
            expected_before_sha256=target_before,
        )
        return record, result

    @staticmethod
    def _validate_request(request: MutationRequest) -> None:
        if request.record_kind not in STEP7_RECORD_KINDS or request.operation not in {"append", "replace"}:
            raise ResearchKBError(
                Diagnostic(SCHEMA_VALIDATION_FAILED, "mutation-request", request.target_record_id, "", "unsupported Research Synthesis mutation")
            )
        if request.paper_id is not None:
            raise ResearchKBError(
                Diagnostic(SCHEMA_VALIDATION_FAILED, "mutation-request", request.target_record_id, "/context/paper_id", "Research Synthesis requires null paper_id")
            )
        if request.question_origin != "existing_question":
            raise ResearchKBError(
                Diagnostic(SCHEMA_VALIDATION_FAILED, "mutation-request", request.target_record_id, "/context/question_origin", "Research Synthesis requires existing_question origin")
            )
        if request.operation == "append" and request.target_record_id is not None:
            raise ResearchKBError(
                Diagnostic(SCHEMA_VALIDATION_FAILED, "mutation-request", request.target_record_id, "/target_record_id", "append target must be null")
            )
        if request.operation == "replace" and request.target_record_id is None:
            raise ResearchKBError(
                Diagnostic(SCHEMA_VALIDATION_FAILED, "mutation-request", None, "/target_record_id", "replace target is required")
            )

    @staticmethod
    def _validate_payload(request: MutationRequest) -> None:
        payload_fields = set(request.payload)
        forbidden = payload_fields & OWNED_FIELDS
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
        required = COMMON_FIELDS | TYPE_FIELDS[request.record_kind]
        allowed = required | OPTIONAL_FIELDS
        if payload_fields - allowed:
            raise ResearchKBError(
                Diagnostic(SCHEMA_VALIDATION_FAILED, request.record_kind, request.target_record_id, "/payload", "unsupported Research Synthesis payload field")
            )
        if not required.issubset(payload_fields):
            raise ResearchKBError(
                Diagnostic(SCHEMA_VALIDATION_FAILED, request.record_kind, request.target_record_id, "/payload", "complete Research Synthesis semantic payload is required")
            )
        status = request.payload.get("candidate_status")
        rationale = request.payload.get("rejection_rationale")
        if status == "rejected" and (not isinstance(rationale, str) or not rationale.strip()):
            raise ResearchKBError(
                Diagnostic(STEP7_BOUNDARY, request.record_kind, request.target_record_id, "/payload/rejection_rationale", "rejected candidate requires a rationale")
            )
        if status != "rejected" and rationale is not None:
            raise ResearchKBError(
                Diagnostic(STEP7_BOUNDARY, request.record_kind, request.target_record_id, "/payload/rejection_rationale", "non-rejected candidate cannot retain a rationale")
            )


def _upstream_signature(
    entries: list[BundleEntry],
    closure: SupportClosure,
    source_views: tuple[str, ...],
) -> str:
    paper_ids = {item["paper_id"] for item in closure.paper_card_base}
    evidence_ids = set(closure.evidence_base)
    queue_ids = set(closure.review_queue_refs)
    source_ids = set(source_views)
    projection: list[tuple[str, dict[str, Any]]] = []
    for kind, record in expand_active_primary_entries(entries):
        include = (
            kind == "domain-profile"
            or (kind == "question-mapping" and record.get("question_id") == closure.question_mapping["question_id"])
            or (kind == "paper-card" and record.get("paper_id") in paper_ids)
            or (kind == "evidence" and record.get("evidence_id") in evidence_ids)
            or (kind == "review-queue" and record.get("queue_id") in queue_ids)
            or (kind in STEP7_RECORD_KINDS and record.get("candidate_id") in source_ids)
        )
        if include:
            projection.append((kind, record))
    canonical = json.dumps(projection, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


__all__ = ["Step7CandidateService"]
