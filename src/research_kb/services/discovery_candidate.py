from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from research_kb.bundle import load_workspace_entries, records_of_kind, validate_workspace_entries
from research_kb.contracts.validator import validate_record
from research_kb.errors import (
    DISCOVERY_METADATA_CONFLICT,
    DUPLICATE_ID,
    INVALID_AUTHORITY,
    SCHEMA_VALIDATION_FAILED,
    UNRESOLVED_REFERENCE,
    Diagnostic,
    ResearchKBError,
)
from research_kb.identifiers import Namespace, allocate_id, validate_id
from research_kb.process_events import timestamp
from research_kb.services.discovery import discovery_report_sha256, validate_discovery_report
from research_kb.storage.json_io import file_sha256, read_jsonl, serialize_json, serialize_jsonl
from research_kb.storage.transactions import TransactionManager, TransactionResult
from research_kb.workspace import WorkspaceLayout


IdAllocator = Callable[[Namespace], str]
REQUEST_FIELDS = {"request_version", "report", "selections", "fixture_origin"}
SELECTION_FIELDS = {"result_key", "target_question_ids"}
CANDIDATE_METADATA_FIELDS = (
    "result_key",
    "title",
    "authors",
    "first_publication_date",
    "journal_or_server",
    "doi",
    "paper_type",
    "publication_types",
    "abstract",
    "matched_keywords",
    "match_location",
    "discovery_sources",
    "full_text_status",
    "version_relationship",
    "possible_duplicate_result_keys",
)


@dataclass(frozen=True, slots=True)
class DiscoverySelectionResult:
    selected_candidate_ids: tuple[str, ...]
    created_candidate_ids: tuple[str, ...]
    updated_candidate_ids: tuple[str, ...]
    unchanged_candidate_ids: tuple[str, ...]
    transaction: TransactionResult | None

    def to_dict(self, layout: WorkspaceLayout) -> dict[str, Any]:
        return {
            "status": "success",
            "result": "updated" if self.transaction is not None else "no_change",
            "selected_candidate_ids": list(self.selected_candidate_ids),
            "created_candidate_ids": list(self.created_candidate_ids),
            "updated_candidate_ids": list(self.updated_candidate_ids),
            "unchanged_candidate_ids": list(self.unchanged_candidate_ids),
            "persistent_writes": 1 if self.transaction is not None else 0,
            "event_id": self.transaction.event_id if self.transaction is not None else None,
            "target": (
                layout.target_relative_path(self.transaction.target)
                if self.transaction is not None
                else None
            ),
        }


class DiscoveryCandidateService:
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

    def select(
        self,
        request_mapping: Mapping[str, Any],
        *,
        actor: str,
    ) -> DiscoverySelectionResult:
        if actor != "user":
            raise ResearchKBError(
                Diagnostic(
                    INVALID_AUTHORITY,
                    "discovery-selection-request",
                    None,
                    "/actor",
                    "discovery candidate selection requires explicit user authority",
                )
            )
        request = _parse_selection_request(request_mapping)
        report = validate_discovery_report(request["report"])
        report_sha256 = discovery_report_sha256(report)
        results_by_key = {item["result_key"]: item for item in report["results"]}
        for selection in request["selections"]:
            if selection["result_key"] not in results_by_key:
                raise ResearchKBError(
                    Diagnostic(
                        UNRESOLVED_REFERENCE,
                        "discovery-selection-request",
                        None,
                        "/selections/result_key",
                        "selected result key does not exist in the supplied discovery report",
                    )
                )

        entries = load_workspace_entries(self.layout)
        validate_workspace_entries(entries)
        question_ids = {
            item["question_id"] for item in records_of_kind(entries, "question-mapping")
        }
        for selection in request["selections"]:
            for question_id in selection["target_question_ids"]:
                if question_id not in question_ids:
                    raise ResearchKBError(
                        Diagnostic(
                            UNRESOLVED_REFERENCE,
                            "discovery-selection-request",
                            None,
                            "/selections/target_question_ids",
                            "target question does not exist in the workspace",
                        )
                    )

        profiles = records_of_kind(entries, "domain-profile")
        profile_id = profiles[0]["domain_profile"]["id"]
        existing_records = records_of_kind(entries, "discovery-candidate")
        existing_by_key = {item["result_key"]: item for item in existing_records}
        for selection in request["selections"]:
            result = results_by_key[selection["result_key"]]
            existing = existing_by_key.get(selection["result_key"])
            if existing is not None and _metadata_projection(existing) != _metadata_projection(result):
                raise ResearchKBError(
                    Diagnostic(
                        DISCOVERY_METADATA_CONFLICT,
                        "discovery-candidate",
                        existing["candidate_id"],
                        "/result_key",
                        "stored discovery metadata differs from the selected report result",
                    )
                )

        now = timestamp(self.transactions.clock)
        created_ids: list[str] = []
        updated_ids: list[str] = []
        unchanged_ids: list[str] = []
        selected_ids: list[str] = []
        changed_records: dict[str, dict[str, Any]] = {}
        for selection in request["selections"]:
            result = results_by_key[selection["result_key"]]
            existing = existing_by_key.get(selection["result_key"])
            context = _selection_context(
                report,
                result_key=result["result_key"],
                target_question_ids=selection["target_question_ids"],
                report_sha256=report_sha256,
                selected_at=now,
            )
            if existing is None:
                candidate_id = self.id_allocator(Namespace.DISCOVERY)
                if not isinstance(candidate_id, str):
                    raise _request_error("discovery candidate ID allocation failed")
                validate_id(candidate_id, Namespace.DISCOVERY)
                if candidate_id in {item["candidate_id"] for item in existing_records} | set(created_ids):
                    raise ResearchKBError(
                        Diagnostic(
                            DUPLICATE_ID,
                            "discovery-candidate",
                            candidate_id,
                            "/candidate_id",
                            "allocated discovery candidate ID is already in use",
                        )
                    )
                candidate = {
                    "schema_version": "1.0",
                    "candidate_id": candidate_id,
                    "workspace_id": self.layout.workspace_id,
                    "domain_profile_id": profile_id,
                    **deepcopy(result),
                    "selection_contexts": [context],
                    "target_question_ids": list(selection["target_question_ids"]),
                    "selection_status": "user_selected",
                    "source_status": "metadata_only",
                    "acquisition_status": "not_started",
                    "not_evidence": True,
                    "automation_status": "passed_auto_checks",
                    "created_at": now,
                    "updated_at": now,
                }
                if request.get("fixture_origin") is not None:
                    candidate["fixture_origin"] = request["fixture_origin"]
                created_ids.append(candidate_id)
                changed_records[candidate_id] = candidate
                existing_by_key[result["result_key"]] = candidate
            else:
                candidate_id = existing["candidate_id"]
                context_ids = {
                    item["selection_context_id"] for item in existing["selection_contexts"]
                }
                if context["selection_context_id"] in context_ids:
                    unchanged_ids.append(candidate_id)
                else:
                    candidate = deepcopy(existing)
                    candidate["selection_contexts"].append(context)
                    candidate["selection_contexts"].sort(
                        key=lambda item: item["selection_context_id"]
                    )
                    candidate["target_question_ids"] = sorted(
                        {
                            question_id
                            for item in candidate["selection_contexts"]
                            for question_id in item["target_question_ids"]
                        }
                    )
                    candidate["updated_at"] = now
                    updated_ids.append(candidate_id)
                    changed_records[candidate_id] = candidate
                    existing_by_key[result["result_key"]] = candidate
            selected_ids.append(candidate_id)

        for record in changed_records.values():
            diagnostics = validate_record("discovery-candidate", record, actor="stored")
            if diagnostics:
                raise ResearchKBError(diagnostics[0])
        if not changed_records:
            return DiscoverySelectionResult(
                tuple(sorted(selected_ids)),
                (),
                (),
                tuple(sorted(unchanged_ids)),
                None,
            )

        proposed = [
            changed_records.get(item["candidate_id"], item)
            for item in existing_records
        ]
        proposed.extend(
            record
            for candidate_id, record in changed_records.items()
            if candidate_id in created_ids
        )
        proposed.sort(key=lambda item: item["candidate_id"])
        target = self.layout.discovery_candidates_path
        before_sha256 = file_sha256(target)

        def validate_temp(path: Path) -> None:
            temporary = read_jsonl(
                path,
                record_kind="discovery-candidate",
                missing_ok=False,
                id_field="candidate_id",
            )
            current_entries = load_workspace_entries(
                self.layout,
                overrides={target: [("discovery-candidate", item) for item in temporary]},
            )
            validate_workspace_entries(current_entries)

        input_refs = set(updated_ids)
        for selection in request["selections"]:
            if existing_by_key[selection["result_key"]]["candidate_id"] in changed_records:
                input_refs.update(selection["target_question_ids"])
        transaction = self.transactions.promote_bytes(
            target=target,
            content=serialize_jsonl(proposed),
            target_store="discovery_candidates",
            operation="discovery_select",
            actor="user",
            input_refs=sorted(input_refs),
            output_refs=sorted(changed_records),
            validator=validate_temp,
            expected_before_sha256=before_sha256,
        )
        return DiscoverySelectionResult(
            tuple(sorted(selected_ids)),
            tuple(sorted(created_ids)),
            tuple(sorted(updated_ids)),
            tuple(sorted(unchanged_ids)),
            transaction,
        )

    def list(self) -> dict[str, Any]:
        entries = load_workspace_entries(self.layout)
        validate_workspace_entries(entries)
        candidates = sorted(
            records_of_kind(entries, "discovery-candidate"),
            key=lambda item: item["candidate_id"],
        )
        return {
            "status": "success",
            "interface_version": "1.0",
            "candidate_count": len(candidates),
            "candidates": [
                {
                    "candidate_id": item["candidate_id"],
                    "result_key": item["result_key"],
                    "title": item["title"],
                    "doi": item["doi"],
                    "first_publication_date": item["first_publication_date"],
                    "paper_type": item["paper_type"],
                    "full_text_status": item["full_text_status"],
                    "target_question_ids": item["target_question_ids"],
                    "selection_context_count": len(item["selection_contexts"]),
                }
                for item in candidates
            ],
        }

    def show(self, candidate_id: str) -> dict[str, Any]:
        candidate_id = validate_id(candidate_id, Namespace.DISCOVERY)
        entries = load_workspace_entries(self.layout)
        validate_workspace_entries(entries)
        candidate = next(
            (
                item
                for item in records_of_kind(entries, "discovery-candidate")
                if item["candidate_id"] == candidate_id
            ),
            None,
        )
        if candidate is None:
            raise ResearchKBError(
                Diagnostic(
                    UNRESOLVED_REFERENCE,
                    "discovery-candidate",
                    candidate_id,
                    "/candidate_id",
                    "discovery candidate does not exist",
                )
            )
        return {
            "status": "success",
            "interface_version": "1.0",
            "candidate": candidate,
        }


def _parse_selection_request(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise _request_error("selection request fields do not match the interface contract")
    fields = set(value)
    if fields != REQUEST_FIELDS and fields != REQUEST_FIELDS - {"fixture_origin"}:
        raise _request_error("selection request fields do not match the interface contract")
    request = dict(value)
    if request.get("request_version") != "1.0":
        raise _request_error("unsupported selection request version", "/request_version")
    if "fixture_origin" in request and request["fixture_origin"] != "synthetic_from_scratch":
        raise _request_error("fixture origin is invalid", "/fixture_origin")
    if not isinstance(request.get("report"), Mapping):
        raise _request_error("selection request report must be an object", "/report")
    selections_value = request.get("selections")
    if not isinstance(selections_value, list) or not 1 <= len(selections_value) <= 15:
        raise _request_error("selection request must contain 1 through 15 selections", "/selections")
    selections: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for index, item in enumerate(selections_value):
        if not isinstance(item, Mapping) or set(item) != SELECTION_FIELDS:
            raise _request_error("selection fields do not match the interface contract", f"/selections/{index}")
        result_key = item["result_key"]
        if not isinstance(result_key, str) or not result_key or len(result_key) > 1024:
            raise _request_error("selection result key is invalid", f"/selections/{index}/result_key")
        if result_key in seen_keys:
            raise ResearchKBError(
                Diagnostic(
                    DUPLICATE_ID,
                    "discovery-selection-request",
                    None,
                    f"/selections/{index}/result_key",
                    "selection result key is duplicated",
                )
            )
        seen_keys.add(result_key)
        question_values = item["target_question_ids"]
        if not isinstance(question_values, list) or not all(
            isinstance(item, str) for item in question_values
        ):
            raise _request_error("target question IDs must be a unique array", f"/selections/{index}/target_question_ids")
        if len(question_values) != len(set(question_values)):
            raise _request_error("target question IDs must be a unique array", f"/selections/{index}/target_question_ids")
        questions: list[str] = []
        for question_index, question_id in enumerate(question_values):
            try:
                questions.append(validate_id(question_id, Namespace.QUESTION))
            except ResearchKBError as error:
                raise _request_error(
                    "target question ID is invalid",
                    f"/selections/{index}/target_question_ids/{question_index}",
                ) from error
        selections.append(
            {"result_key": result_key, "target_question_ids": sorted(questions)}
        )
    request["report"] = dict(request["report"])
    request["selections"] = sorted(selections, key=lambda item: item["result_key"])
    return request


def _selection_context(
    report: Mapping[str, Any],
    *,
    result_key: str,
    target_question_ids: list[str],
    report_sha256: str,
    selected_at: str,
) -> dict[str, Any]:
    identity = {
        "provider": report["provider"],
        "result_key": result_key,
        "query": report["query"],
        "target_question_ids": target_question_ids,
    }
    digest = hashlib.sha256(serialize_json(identity)).hexdigest()
    return {
        "selection_context_id": f"selection_sha256_{digest}",
        "provider": report["provider"],
        "provider_api_version": report["provider_api_version"],
        "query": deepcopy(report["query"]),
        "report_sha256": report_sha256,
        "target_question_ids": list(target_question_ids),
        "selected_at": selected_at,
    }


def _metadata_projection(value: Mapping[str, Any]) -> dict[str, Any]:
    return {field: value.get(field) for field in CANDIDATE_METADATA_FIELDS}


def _request_error(message: str, path: str = "") -> ResearchKBError:
    return ResearchKBError(
        Diagnostic(
            SCHEMA_VALIDATION_FAILED,
            "discovery-selection-request",
            None,
            path,
            message,
        )
    )


__all__ = ["DiscoveryCandidateService", "DiscoverySelectionResult"]
