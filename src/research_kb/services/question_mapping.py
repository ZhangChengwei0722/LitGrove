from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from research_kb.bundle import BundleEntry, load_workspace_entries, records_of_kind, validate_workspace_entries
from research_kb.contracts.validator import validate_record
from research_kb.errors import (
    DUPLICATE_ID,
    GROUNDING_MISMATCH,
    INVALID_AUTHORITY,
    SCHEMA_VALIDATION_FAILED,
    SNAPSHOT_MISMATCH,
    UNRESOLVED_REFERENCE,
    Diagnostic,
    ResearchKBError,
)
from research_kb.identifiers import Namespace, allocate_id, validate_id
from research_kb.mutation import MutationRequest
from research_kb.process_events import timestamp
from research_kb.screening_bundles import require_screening_eligible_links
from research_kb.storage.json_io import file_sha256, read_jsonl, serialize_jsonl
from research_kb.storage.transactions import TransactionManager, TransactionResult
from research_kb.workspace import WorkspaceLayout


IdAllocator = Callable[[Namespace], str]
APPEND_ORIGINS = {"user_supplied", "user_approved_candidate"}
ALLOWED_FIELDS = {"question_text", "scope", "mapping_status", "paper_links"}
OWNED_FIELDS = {
    "schema_version",
    "question_id",
    "domain_profile_id",
    "created_at",
    "updated_at",
    "fixture_origin",
}
ALLOWED_LINK_FIELDS = {
    "paper_id",
    "selected_card_unit_ids",
    "role_in_question",
    "relevance_rationale",
    "boundary_refs",
}
OWNED_LINK_FIELDS = {"question_link_id", "evidence_ids"}


class QuestionMappingService:
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
        self._validate_request(request)
        entries = load_workspace_entries(self.layout)
        validate_workspace_entries(entries)
        self._validate_payload(request)
        if (
            request.operation == "replace"
            and any(
                kind == "question-revision-bundle"
                and record.get("question_id") == request.target_record_id
                for kind, record in entries
            )
        ):
            raise ResearchKBError(
                Diagnostic(
                    INVALID_AUTHORITY,
                    "question-mapping",
                    request.target_record_id,
                    "/question_id",
                    "legacy Question writer is disabled after a P7 successor exists",
                )
            )
        if request.operation == "append":
            record = self._append_record(request, entries)
        else:
            record = self._replace_record(request, entries, actor)
        diagnostics = validate_record("question-mapping", record, actor=actor)
        if diagnostics:
            raise ResearchKBError(diagnostics[0])
        require_screening_eligible_links(
            record["question_id"],
            (link["paper_id"] for link in record["paper_links"]),
            entries,
        )
        return self._promote_store(request, entries, record, actor)

    def _append_record(
        self,
        request: MutationRequest,
        entries: list[BundleEntry],
    ) -> dict[str, Any]:
        now = timestamp(self.transactions.clock)
        profile = records_of_kind(entries, "domain-profile")[0]
        question_id = self.id_allocator(Namespace.QUESTION)
        links = self._normalize_links(
            request.payload["paper_links"],
            entries,
            mapping_status=request.payload["mapping_status"],
            existing_link_ids={},
        )
        record = {
            "schema_version": "1.0",
            "question_id": question_id,
            "question_text": request.payload["question_text"],
            "scope": request.payload["scope"],
            "domain_profile_id": profile["domain_profile"]["id"],
            "paper_links": links,
            "mapping_status": request.payload["mapping_status"],
            "created_at": now,
            "updated_at": now,
        }
        if request.fixture_origin is not None:
            record["fixture_origin"] = request.fixture_origin
        return record

    def _replace_record(
        self,
        request: MutationRequest,
        entries: list[BundleEntry],
        actor: str,
    ) -> dict[str, Any]:
        assert request.target_record_id is not None
        validate_id(request.target_record_id, Namespace.QUESTION)
        existing = next(
            (
                item
                for item in records_of_kind(entries, "question-mapping")
                if item["question_id"] == request.target_record_id
            ),
            None,
        )
        if existing is None:
            raise ResearchKBError(
                Diagnostic(
                    UNRESOLVED_REFERENCE,
                    "question-mapping",
                    request.target_record_id,
                    "/question_id",
                    "target question does not exist",
                )
            )
        if actor != "user":
            for field in ("question_text", "scope"):
                if field in request.payload and request.payload[field] != existing[field]:
                    raise ResearchKBError(
                        Diagnostic(
                            INVALID_AUTHORITY,
                            "question-mapping",
                            request.target_record_id,
                            f"/payload/{field}",
                            f"changing {field} is user-only",
                        )
                    )
        mapping_status = request.payload.get("mapping_status", existing["mapping_status"])
        raw_links = request.payload.get("paper_links")
        if raw_links is None:
            raw_links = [
                {field: link[field] for field in ALLOWED_LINK_FIELDS}
                for link in existing["paper_links"]
            ]
        existing_link_ids = {
            link["paper_id"]: link["question_link_id"]
            for link in existing["paper_links"]
        }
        links = self._normalize_links(
            raw_links,
            entries,
            mapping_status=mapping_status,
            existing_link_ids=existing_link_ids,
        )
        missing = set(existing_link_ids) - {link["paper_id"] for link in links}
        if missing:
            raise ResearchKBError(
                Diagnostic(
                    INVALID_AUTHORITY,
                    "question-mapping",
                    request.target_record_id,
                    "/payload/paper_links",
                    "M2B-1 cannot remove an existing paper link",
                )
            )
        return {
            **existing,
            **request.payload,
            "paper_links": links,
            "updated_at": timestamp(self.transactions.clock),
        }

    def _normalize_links(
        self,
        value: object,
        entries: list[BundleEntry],
        *,
        mapping_status: str,
        existing_link_ids: dict[str, str],
    ) -> list[dict[str, Any]]:
        if not isinstance(value, list) or not value:
            raise ResearchKBError(
                Diagnostic(
                    SCHEMA_VALIDATION_FAILED,
                    "question-mapping",
                    None,
                    "/payload/paper_links",
                    "paper_links must be a non-empty array",
                )
            )
        papers = {item["paper_id"]: item for item in records_of_kind(entries, "registry-paper")}
        cards = {item["paper_id"]: item for item in records_of_kind(entries, "paper-card")}
        evidence = {item["evidence_id"]: item for item in records_of_kind(entries, "evidence")}
        queues = {item["queue_id"]: item for item in records_of_kind(entries, "review-queue")}
        units: dict[str, tuple[str, dict[str, Any]]] = {}
        for paper_id, card in cards.items():
            for section in card["sections"]:
                for unit in section["units"]:
                    units[unit["unit_id"]] = (paper_id, unit)

        normalized: list[dict[str, Any]] = []
        seen_papers: set[str] = set()
        for index, source in enumerate(value):
            base = f"/payload/paper_links/{index}"
            if not isinstance(source, dict):
                raise ResearchKBError(
                    Diagnostic(SCHEMA_VALIDATION_FAILED, "question-mapping", None, base, "paper link must be an object")
                )
            forbidden = set(source) & OWNED_LINK_FIELDS
            if forbidden:
                raise ResearchKBError(
                    Diagnostic(
                        INVALID_AUTHORITY,
                        "question-mapping",
                        None,
                        base,
                        f"CLI-owned link fields cannot be submitted: {', '.join(sorted(forbidden))}",
                    )
                )
            unknown = set(source) - ALLOWED_LINK_FIELDS
            if unknown:
                raise ResearchKBError(
                    Diagnostic(SCHEMA_VALIDATION_FAILED, "question-mapping", None, base, "unsupported paper-link field")
                )
            required = {"paper_id", "selected_card_unit_ids", "role_in_question", "relevance_rationale"}
            if not required.issubset(source):
                raise ResearchKBError(
                    Diagnostic(SCHEMA_VALIDATION_FAILED, "question-mapping", None, base, "paper link lacks required fields")
                )
            paper_id = source["paper_id"]
            if not isinstance(paper_id, str) or paper_id not in papers:
                raise ResearchKBError(
                    Diagnostic(UNRESOLVED_REFERENCE, "question-mapping", None, base + "/paper_id", "paper is not registered")
                )
            if paper_id in seen_papers:
                raise ResearchKBError(
                    Diagnostic(DUPLICATE_ID, "question-mapping", None, "/payload/paper_links", "duplicate paper link")
                )
            seen_papers.add(paper_id)
            if paper_id not in cards:
                raise ResearchKBError(
                    Diagnostic(UNRESOLVED_REFERENCE, "question-mapping", None, base, "paper has no Paper Card")
                )
            unit_ids = self._unique_string_ids(source["selected_card_unit_ids"], base + "/selected_card_unit_ids")
            if not unit_ids:
                raise ResearchKBError(
                    Diagnostic(SCHEMA_VALIDATION_FAILED, "question-mapping", None, base + "/selected_card_unit_ids", "at least one Card Unit is required")
                )
            boundary_ids = self._unique_string_ids(source.get("boundary_refs", []), base + "/boundary_refs")
            expanded_evidence: set[str] = set()
            expanded_boundaries = set(boundary_ids)
            for unit_id in unit_ids:
                owner_and_unit = units.get(unit_id)
                if owner_and_unit is None:
                    raise ResearchKBError(
                        Diagnostic(UNRESOLVED_REFERENCE, "question-mapping", None, base + "/selected_card_unit_ids", "Card Unit does not exist")
                    )
                owner, unit = owner_and_unit
                if owner != paper_id:
                    raise ResearchKBError(
                        Diagnostic(GROUNDING_MISMATCH, "question-mapping", None, base + "/selected_card_unit_ids", "selected Card Unit belongs to another paper")
                    )
                if unit["grounding_status"] not in {"grounded", "revised"}:
                    raise ResearchKBError(
                        Diagnostic(
                            GROUNDING_MISMATCH,
                            "question-mapping",
                            None,
                            base + "/selected_card_unit_ids",
                            "new factual mappings require grounded or revised Card Units",
                        )
                    )
                expanded_evidence.update(unit["evidence_ids"])
                expanded_boundaries.update(unit["boundary_refs"])
            for evidence_id in expanded_evidence:
                item = evidence.get(evidence_id)
                if item is None:
                    raise ResearchKBError(
                        Diagnostic(UNRESOLVED_REFERENCE, "question-mapping", None, base + "/evidence_ids", "expanded evidence does not exist")
                    )
                if item["paper_id"] != paper_id:
                    raise ResearchKBError(
                        Diagnostic(GROUNDING_MISMATCH, "question-mapping", None, base + "/evidence_ids", "expanded evidence belongs to another paper")
                    )
            for queue_id in expanded_boundaries:
                item = queues.get(queue_id)
                if item is None:
                    raise ResearchKBError(
                        Diagnostic(UNRESOLVED_REFERENCE, "question-mapping", None, base + "/boundary_refs", "review queue boundary does not exist")
                    )
                if item["paper_id"] != paper_id:
                    raise ResearchKBError(
                        Diagnostic(GROUNDING_MISMATCH, "question-mapping", None, base + "/boundary_refs", "review queue boundary belongs to another paper")
                    )
                if item.get("not_evidence") is not True:
                    raise ResearchKBError(
                        Diagnostic(GROUNDING_MISMATCH, "question-mapping", None, base + "/boundary_refs", "boundary must remain not_evidence")
                    )
            normalized.append(
                {
                    "question_link_id": existing_link_ids.get(paper_id, ""),
                    "paper_id": paper_id,
                    "selected_card_unit_ids": unit_ids,
                    "role_in_question": source["role_in_question"],
                    "relevance_rationale": source["relevance_rationale"],
                    "evidence_ids": sorted(expanded_evidence),
                    "boundary_refs": sorted(expanded_boundaries),
                }
            )
        normalized.sort(key=lambda item: item["paper_id"])
        for link in normalized:
            if not link["question_link_id"]:
                link["question_link_id"] = self.id_allocator(Namespace.QUESTION_LINK)
        return normalized

    def _promote_store(
        self,
        request: MutationRequest,
        entries: list[BundleEntry],
        record: dict[str, Any],
        actor: str,
    ) -> tuple[dict[str, Any], TransactionResult]:
        target = self.layout.question_mappings_path
        target_before = file_sha256(target)
        existing = read_jsonl(target, record_kind="question-mapping", id_field="question_id")
        proposed = (
            [*existing, record]
            if request.operation == "append"
            else [record if item["question_id"] == record["question_id"] else item for item in existing]
        )
        proposed.sort(key=lambda item: item["question_id"])

        def validate_temp(path: Path) -> None:
            temporary = read_jsonl(
                path,
                record_kind="question-mapping",
                missing_ok=False,
                id_field="question_id",
            )
            override = [("question-mapping", item) for item in temporary]
            current_entries = load_workspace_entries(self.layout, overrides={target: override})
            validate_workspace_entries(current_entries)
            current = next(item for item in temporary if item["question_id"] == record["question_id"])
            freshness = mapping_freshness_diagnostics(current, current_entries)
            if freshness:
                raise ResearchKBError(freshness[0])

        upstream_refs = {
            value
            for link in record["paper_links"]
            for value in (
                link["paper_id"],
                *link["selected_card_unit_ids"],
                *link["evidence_ids"],
                *link["boundary_refs"],
            )
        }
        if request.operation == "replace":
            upstream_refs.add(record["question_id"])
            previous = next(
                item
                for item in records_of_kind(entries, "question-mapping")
                if item["question_id"] == record["question_id"]
            )
            upstream_refs.update(link["question_link_id"] for link in previous["paper_links"])
        output_refs = [record["question_id"], *(link["question_link_id"] for link in record["paper_links"])]
        result = self.transactions.promote_bytes(
            target=target,
            content=serialize_jsonl(proposed),
            target_store="question_mappings",
            operation=f"record_{request.operation}",
            actor=actor,
            input_refs=sorted(upstream_refs),
            output_refs=sorted(output_refs),
            validator=validate_temp,
            expected_before_sha256=target_before,
        )
        return record, result

    @staticmethod
    def _validate_request(request: MutationRequest) -> None:
        if request.record_kind != "question-mapping" or request.operation not in {"append", "replace"}:
            raise ResearchKBError(
                Diagnostic(SCHEMA_VALIDATION_FAILED, "mutation-request", request.target_record_id, "", "unsupported question mapping request")
            )
        if request.paper_id is not None:
            raise ResearchKBError(
                Diagnostic(SCHEMA_VALIDATION_FAILED, "mutation-request", request.target_record_id, "/context/paper_id", "question mappings require null paper_id")
            )
        if request.operation == "append":
            if request.target_record_id is not None or request.question_origin not in APPEND_ORIGINS:
                raise ResearchKBError(
                    Diagnostic(SCHEMA_VALIDATION_FAILED, "mutation-request", request.target_record_id, "/context/question_origin", "append requires an approved question origin")
                )
        elif request.target_record_id is None or request.question_origin != "existing_question":
            raise ResearchKBError(
                Diagnostic(SCHEMA_VALIDATION_FAILED, "mutation-request", request.target_record_id, "/context/question_origin", "replace requires existing_question origin")
            )

    @staticmethod
    def _validate_payload(request: MutationRequest) -> None:
        forbidden = set(request.payload) & OWNED_FIELDS
        if forbidden:
            raise ResearchKBError(
                Diagnostic(INVALID_AUTHORITY, "question-mapping", request.target_record_id, "/payload", "CLI-owned fields cannot be submitted")
            )
        unknown = set(request.payload) - ALLOWED_FIELDS
        if unknown:
            raise ResearchKBError(
                Diagnostic(SCHEMA_VALIDATION_FAILED, "question-mapping", request.target_record_id, "/payload", "unsupported question mapping field")
            )
        if request.operation == "append" and set(request.payload) != ALLOWED_FIELDS:
            raise ResearchKBError(
                Diagnostic(SCHEMA_VALIDATION_FAILED, "question-mapping", None, "/payload", "append requires all question mapping fields")
            )
        if request.operation == "replace" and not request.payload:
            raise ResearchKBError(
                Diagnostic(SCHEMA_VALIDATION_FAILED, "question-mapping", request.target_record_id, "/payload", "replace payload must not be empty")
            )

    @staticmethod
    def _unique_string_ids(value: object, path: str) -> list[str]:
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ResearchKBError(
                Diagnostic(SCHEMA_VALIDATION_FAILED, "question-mapping", None, path, "value must be an array of IDs")
            )
        if len(value) != len(set(value)):
            raise ResearchKBError(
                Diagnostic(DUPLICATE_ID, "question-mapping", None, path, "duplicate ID")
            )
        return sorted(value)


def mapping_freshness_diagnostics(
    mapping: dict[str, Any],
    entries: list[BundleEntry],
) -> list[Diagnostic]:
    cards = {item["paper_id"]: item for item in records_of_kind(entries, "paper-card")}
    evidence = {item["evidence_id"]: item for item in records_of_kind(entries, "evidence")}
    queues = {item["queue_id"]: item for item in records_of_kind(entries, "review-queue")}
    updated_at = mapping["updated_at"]
    diagnostics: list[Diagnostic] = []
    for link in mapping["paper_links"]:
        card = cards.get(link["paper_id"])
        active_unit_ids = {
            unit["unit_id"]
            for section in (card or {}).get("sections", [])
            for unit in section.get("units", [])
        }
        missing_active_refs = (
            card is None
            or any(value not in active_unit_ids for value in link["selected_card_unit_ids"])
            or any(value not in evidence for value in link["evidence_ids"])
            or any(value not in queues for value in link["boundary_refs"])
        )
        upstream = [card]
        upstream.extend(evidence.get(value) for value in link["evidence_ids"])
        upstream.extend(queues.get(value) for value in link["boundary_refs"])
        mapping_time = _parse_timestamp(updated_at)
        if missing_active_refs or any(
            item is not None and _parse_timestamp(item["updated_at"]) > mapping_time
            for item in upstream
        ):
            diagnostics.append(
                Diagnostic(
                    SNAPSHOT_MISMATCH,
                    "question-mapping",
                    mapping["question_id"],
                    "/updated_at",
                    "question mapping is older than or no longer active in its linked Paper Card, evidence, or review queue records",
                    severity="warning",
                )
            )
            break
    try:
        require_screening_eligible_links(
            mapping["question_id"],
            (link["paper_id"] for link in mapping["paper_links"]),
            entries,
        )
    except ResearchKBError:
        diagnostics.append(
            Diagnostic(
                SNAPSHOT_MISMATCH,
                "question-mapping",
                mapping["question_id"],
                "/paper_links",
                "question mapping is stale relative to its active Question-specific screening criteria or decisions",
                severity="warning",
            )
        )
    return diagnostics


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


__all__ = ["QuestionMappingService", "mapping_freshness_diagnostics"]
