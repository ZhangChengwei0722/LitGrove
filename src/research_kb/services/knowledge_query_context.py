from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from research_kb.bundle import load_workspace_entries, records_of_kind, validate_workspace_entries
from research_kb.catalog.models import canonical_digest
from research_kb.errors import (
    DUPLICATE_ID,
    SCHEMA_VALIDATION_FAILED,
    UNRESOLVED_REFERENCE,
    Diagnostic,
    ResearchKBError,
)
from research_kb.identifiers import Namespace, validate_id
from research_kb.identity_corrections import project_registry_identity
from research_kb.review_memory_provenance import build_active_parse_index, review_memory_freshness
from research_kb.services.question_mapping import mapping_freshness_diagnostics
from research_kb.source_resolution import observe_paper_source
from research_kb.workspace import WorkspaceLayout


QUERY_TYPES = frozenset(
    {
        "single_paper_explanation",
        "seven_section_overview",
        "methods",
        "selected_paper_comparison",
        "trend_problem_discussion",
        "evidence_find",
    }
)
_SINGLE_PAPER_QUERY_TYPES = frozenset(
    {"single_paper_explanation", "seven_section_overview", "methods"}
)
_MULTI_PAPER_QUERY_TYPES = frozenset(
    {"selected_paper_comparison", "trend_problem_discussion"}
)
_ADMISSIBLE_UNIT_STATUSES = frozenset({"grounded", "revised"})


@dataclass(frozen=True, slots=True)
class KnowledgeQueryContext:
    basis: dict[str, Any]
    payload: dict[str, Any]


class KnowledgeQueryContextService:
    def __init__(self, layout: WorkspaceLayout):
        self.layout = layout

    def build(
        self,
        *,
        query_type: str,
        query_text: str,
        paper_ids: Iterable[str],
        include_review_background: bool,
        include_routing_context: bool,
        effective_content_classes: Iterable[str],
    ) -> KnowledgeQueryContext:
        normalized_type, normalized_text, normalized_ids = _normalize_query(
            query_type,
            query_text,
            paper_ids,
        )
        classes = frozenset(effective_content_classes)
        if include_review_background and "review_background" not in classes:
            raise _query_error(
                "/include_review_background",
                "Review background was requested outside the effective privacy scope",
            )
        if include_routing_context and "research_routing_context" not in classes:
            raise _query_error(
                "/include_routing_context",
                "routing context was requested outside the effective privacy scope",
            )

        entries = load_workspace_entries(self.layout)
        validate_workspace_entries(entries)
        papers = {item["paper_id"]: item for item in records_of_kind(entries, "registry-paper")}
        missing = [paper_id for paper_id in normalized_ids if paper_id not in papers]
        if missing:
            raise ResearchKBError(
                Diagnostic(
                    UNRESOLVED_REFERENCE,
                    "registry-paper",
                    missing[0],
                    "/paper_ids",
                    "Knowledge Query paper is not registered",
                )
            )
        identity = project_registry_identity(
            papers.values(),
            records_of_kind(entries, "registry-identity-correction"),
        )

        primary_bundles = {
            item["paper_id"]: item for item in records_of_kind(entries, "primary-semantic-bundle")
        }
        review_bundles = {
            item["paper_id"]: item for item in records_of_kind(entries, "review-semantic-bundle")
        }
        cards = {item["paper_id"]: item for item in records_of_kind(entries, "paper-card")}
        evidence_by_paper = _group_by(records_of_kind(entries, "evidence"), "paper_id")
        memories = {item["paper_id"]: item for item in records_of_kind(entries, "review-memory")}
        active_parse, parse_failures = build_active_parse_index(records_of_kind(entries, "parsed-page"))
        parse_failure_papers = {item.record_id for item in parse_failures if item.record_id is not None}

        primary_payload: list[dict[str, Any]] = []
        review_payload: list[dict[str, Any]] = []
        paper_snapshots: list[dict[str, Any]] = []
        excluded: list[dict[str, str]] = []
        for paper_id in normalized_ids:
            paper = papers[paper_id]
            paper_identity = identity[paper_id]
            source = observe_paper_source(self.layout, entries, paper)
            primary_bundle = primary_bundles.get(paper_id)
            review_bundle = review_bundles.get(paper_id)
            primary_revision = _active_revision(primary_bundle)
            review_revision = _active_revision(review_bundle)
            card = (
                primary_revision.get("paper_card")
                if primary_revision is not None
                else cards.get(paper_id)
            )
            evidence = (
                list(primary_revision.get("evidence", []))
                if primary_revision is not None
                else list(evidence_by_paper.get(paper_id, []))
            )
            memory = (
                review_revision.get("review_memory")
                if review_revision is not None
                else memories.get(paper_id)
            )
            authority_mode = (
                "revisioned_bundle"
                if primary_revision is not None or review_revision is not None
                else "legacy_unversioned"
                if card is not None or memory is not None
                else "none"
            )
            document_route = "review" if memory is not None else "primary" if card is not None else "unprocessed"
            active_revision = review_revision if document_route == "review" else primary_revision
            paper_snapshots.append(
                {
                    "paper_id": paper_id,
                    "paper_record_digest": canonical_digest(paper),
                    "canonical_paper_id": paper_identity["canonical_paper_id"],
                    "library_status": paper_identity["library_status"],
                    "identity_projection_digest": canonical_digest(paper_identity),
                    "source_state": source.state,
                    "source_digest": source.live_sha256,
                    "document_route": document_route,
                    "authority_mode": authority_mode,
                    "revision_id": None if active_revision is None else active_revision["revision_id"],
                    "revision_digest": None if active_revision is None else canonical_digest(active_revision),
                    "card_digest": None if card is None else canonical_digest(card),
                    "evidence_digests": [
                        {
                            "evidence_id": item["evidence_id"],
                            "evidence_digest": canonical_digest(item),
                        }
                        for item in sorted(evidence, key=lambda item: item["evidence_id"])
                    ],
                    "review_memory_digest": None if memory is None else canonical_digest(memory),
                }
            )

            primary_item, primary_excluded = _primary_context(
                paper,
                card,
                evidence,
                source_state=source.state,
                expected_source_digest=source.expected_sha256,
                include_metadata="metadata" in classes,
                authority_mode=authority_mode,
                revision_id=None if primary_revision is None else primary_revision["revision_id"],
                identity_is_active=(
                    paper_identity["library_status"] == "active"
                    and paper_identity["canonical_paper_id"] == paper_id
                ),
            )
            primary_payload.append(primary_item)
            excluded.extend(primary_excluded)

            if include_review_background and memory is not None:
                freshness = (
                    "unavailable"
                    if paper_id in parse_failure_papers
                    else review_memory_freshness(memory, active_parse)
                )
                if (
                    source.state == "current"
                    and freshness == "current"
                    and paper_identity["library_status"] == "active"
                    and paper_identity["canonical_paper_id"] == paper_id
                ):
                    review_payload.append(_review_context(paper, memory, "metadata" in classes))
                else:
                    excluded.append(
                        {
                            "paper_id": paper_id,
                            "record_type": "review_memory",
                            "record_id": memory["review_memory_id"],
                            "reason": "review_background_not_current",
                        }
                    )

        routing_payload, mapping_snapshots = self._routing_context(
            entries,
            normalized_ids,
            enabled=include_routing_context,
        )
        payload = {
            "query": {
                "query_type": normalized_type,
                "query_text": normalized_text,
                "paper_ids": normalized_ids,
            },
            "primary_papers": primary_payload,
            "review_background": review_payload,
            "routing_context": routing_payload,
            "excluded_context": sorted(
                excluded,
                key=lambda item: (
                    item["paper_id"],
                    item["record_type"],
                    item["record_id"],
                    item["reason"],
                ),
            ),
            "operational_context": {
                "task_kind": "knowledge_query_report",
                "factual_support_policy": "current_primary_card_unit_plus_canonical_evidence",
                "review_content_policy": "background_only",
                "excluded_context_can_support_claims": False,
                "canonical_scientific_write": False,
            },
        }
        basis = {
            "query_type": normalized_type,
            "query_text": normalized_text,
            "paper_ids": normalized_ids,
            "include_review_background": include_review_background,
            "include_routing_context": include_routing_context,
            "paper_snapshots": paper_snapshots,
            "mapping_snapshots": mapping_snapshots,
            "payload_digest": canonical_digest(payload),
        }
        return KnowledgeQueryContext(basis=basis, payload=payload)

    @staticmethod
    def validate_result(
        result: dict[str, Any],
        payload: dict[str, Any],
    ) -> None:
        query_type = payload["query"]["query_type"]
        if result.get("query_type") != query_type:
            raise _query_error("/staged_result/query_type", "query result type does not match the Task")
        support_allowlist: dict[tuple[str, str], frozenset[str]] = {}
        for paper in payload["primary_papers"]:
            for unit in paper["card_units"]:
                support_allowlist[(paper["paper_id"], unit["unit_id"])] = frozenset(
                    unit["evidence_ids"]
                )
        background_allowlist = {
            (item["paper_id"], item["review_memory_id"], unit["review_unit_id"])
            for item in payload["review_background"]
            for unit in item["review_units"]
        }
        for block_index, block in enumerate(result.get("answer_blocks", [])):
            support_papers: set[str] = set()
            for ref_index, support in enumerate(block.get("support_refs", [])):
                key = (support["paper_id"], support["card_unit_id"])
                allowed = support_allowlist.get(key)
                submitted = set(support["evidence_ids"])
                if allowed is None or not submitted.issubset(allowed):
                    raise _query_error(
                        f"/staged_result/answer_blocks/{block_index}/support_refs/{ref_index}",
                        "query support reference is outside the exact payload allowlist",
                    )
                support_papers.add(support["paper_id"])
            if block.get("block_role") == "cross_paper_synthesis" and len(support_papers) < 2:
                raise _query_error(
                    f"/staged_result/answer_blocks/{block_index}/support_refs",
                    "cross-paper synthesis requires support from at least two selected papers",
                )
            for ref_index, background in enumerate(block.get("background_refs", [])):
                key = (
                    background["paper_id"],
                    background["review_memory_id"],
                    background["review_unit_id"],
                )
                if key not in background_allowlist:
                    raise _query_error(
                        f"/staged_result/answer_blocks/{block_index}/background_refs/{ref_index}",
                        "query background reference is outside the exact payload allowlist",
                    )

    def _routing_context(
        self,
        entries: list[tuple[str, dict[str, Any]]],
        paper_ids: list[str],
        *,
        enabled: bool,
    ) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
        if not enabled:
            return [], []
        selected = set(paper_ids)
        payload: list[dict[str, Any]] = []
        snapshots: list[dict[str, str]] = []
        for mapping in sorted(records_of_kind(entries, "question-mapping"), key=lambda item: item["question_id"]):
            links = [item for item in mapping["paper_links"] if item["paper_id"] in selected]
            if not links:
                continue
            freshness = "stale" if mapping_freshness_diagnostics(mapping, entries) else "current"
            payload.append(
                {
                    "question_id": mapping["question_id"],
                    "question_text": mapping["question_text"],
                    "scope": mapping["scope"],
                    "mapping_status": mapping["mapping_status"],
                    "freshness": freshness,
                    "paper_links": [
                        {
                            "paper_id": item["paper_id"],
                            "selected_card_unit_ids": list(item["selected_card_unit_ids"]),
                            "role_in_question": item["role_in_question"],
                        }
                        for item in links
                    ],
                }
            )
            snapshots.append(
                {
                    "question_id": mapping["question_id"],
                    "mapping_digest": canonical_digest(mapping),
                    "freshness": freshness,
                }
            )
        return payload, snapshots


def _normalize_query(
    query_type: object,
    query_text: object,
    paper_ids: Iterable[str],
) -> tuple[str, str, list[str]]:
    if not isinstance(query_type, str) or query_type not in QUERY_TYPES:
        raise _query_error("/query_type", "Knowledge Query type is invalid")
    if not isinstance(query_text, str) or not query_text.strip():
        raise _query_error("/query_text", "Knowledge Query text must be non-empty")
    normalized_text = query_text.strip()
    if len(normalized_text.encode("utf-8")) > 2000:
        raise _query_error("/query_text", "Knowledge Query text exceeds 2000 UTF-8 bytes")
    if isinstance(paper_ids, (str, bytes)):
        raise _query_error("/paper_ids", "Knowledge Query paper IDs must be an array")
    normalized_ids = [validate_id(item, Namespace.PAPER) for item in paper_ids]
    if len(normalized_ids) != len(set(normalized_ids)):
        raise ResearchKBError(
            Diagnostic(DUPLICATE_ID, "agent-task-state", None, "/paper_ids", "Knowledge Query paper IDs must be unique")
        )
    valid_cardinality = (
        len(normalized_ids) == 1
        if query_type in _SINGLE_PAPER_QUERY_TYPES
        else 2 <= len(normalized_ids) <= 4
        if query_type in _MULTI_PAPER_QUERY_TYPES
        else 1 <= len(normalized_ids) <= 4
    )
    if not valid_cardinality:
        raise _query_error("/paper_ids", "Knowledge Query paper cardinality does not match its type")
    return query_type, normalized_text, normalized_ids


def _primary_context(
    paper: dict[str, Any],
    card: dict[str, Any] | None,
    evidence: list[dict[str, Any]],
    *,
    source_state: str,
    expected_source_digest: str,
    include_metadata: bool,
    authority_mode: str,
    revision_id: str | None,
    identity_is_active: bool,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    result: dict[str, Any] = {
        "paper_id": paper["paper_id"],
        "authority_mode": authority_mode,
        "revision_id": revision_id,
        "card_units": [],
        "evidence": [],
    }
    if include_metadata:
        result["bibliography"] = paper["bibliography"]
    excluded: list[dict[str, str]] = []
    if card is None:
        excluded.append(_excluded(paper["paper_id"], "paper_card", paper["paper_id"], "paper_card_absent"))
        return result, excluded
    if not identity_is_active:
        excluded.append(_excluded(paper["paper_id"], "registry_identity", paper["paper_id"], "library_record_not_active"))
        return result, excluded
    if source_state != "current":
        excluded.append(_excluded(paper["paper_id"], "source", paper["paper_id"], "source_not_current"))
        return result, excluded
    eligible_evidence = {
        item["evidence_id"]: item
        for item in evidence
        if item.get("canonical") is True
        and item.get("source_type") == "primary"
        and item.get("paper_id") == paper["paper_id"]
        and item.get("source_fingerprint", {}).get("value") == expected_source_digest
    }
    retained_ids: set[str] = set()
    for section in card["sections"]:
        for unit in section["units"]:
            evidence_ids = list(unit["evidence_ids"])
            if unit["grounding_status"] not in _ADMISSIBLE_UNIT_STATUSES:
                excluded.append(_excluded(paper["paper_id"], "card_unit", unit["unit_id"], "unit_status_not_admissible"))
                continue
            if not evidence_ids or any(evidence_id not in eligible_evidence for evidence_id in evidence_ids):
                excluded.append(_excluded(paper["paper_id"], "card_unit", unit["unit_id"], "evidence_not_admissible"))
                continue
            result["card_units"].append(
                {
                    "unit_id": unit["unit_id"],
                    "section_id": unit["section_id"],
                    "statement": unit["statement"],
                    "statement_type": unit["statement_type"],
                    "grounding_status": unit["grounding_status"],
                    "evidence_ids": evidence_ids,
                }
            )
            retained_ids.update(evidence_ids)
    result["evidence"] = [
        {
            "evidence_id": item["evidence_id"],
            "claim": item["claim"],
            "quote": item["quote"],
            "source_page": item["source_page"],
            "locator": item["locator"],
            "support_scope": item["support_scope"],
            "what_it_does_not_support": item["what_it_does_not_support"],
        }
        for item in sorted(eligible_evidence.values(), key=lambda item: item["evidence_id"])
        if item["evidence_id"] in retained_ids
    ]
    return result, excluded


def _review_context(
    paper: dict[str, Any],
    memory: dict[str, Any],
    include_metadata: bool,
) -> dict[str, Any]:
    result = {
        "paper_id": paper["paper_id"],
        "review_memory_id": memory["review_memory_id"],
        "background_only": True,
        "review_units": [
            {
                "review_unit_id": unit["review_unit_id"],
                "section_id": unit["section_id"],
                "unit_type": unit["unit_type"],
                "content": unit["content"],
                "source_notes": unit["source_notes"],
                "background_only": True,
            }
            for section in memory["sections"]
            for unit in section["units"]
        ],
    }
    if include_metadata:
        result["bibliography"] = paper["bibliography"]
    return result


def _active_revision(bundle: dict[str, Any] | None) -> dict[str, Any] | None:
    if bundle is None:
        return None
    return next(
        (
            item
            for item in bundle["revisions"]
            if item["revision_id"] == bundle["active_revision_id"]
        ),
        None,
    )


def _group_by(records: Iterable[dict[str, Any]], field: str) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        result.setdefault(record[field], []).append(record)
    return result


def _excluded(paper_id: str, record_type: str, record_id: str, reason: str) -> dict[str, str]:
    return {
        "paper_id": paper_id,
        "record_type": record_type,
        "record_id": record_id,
        "reason": reason,
    }


def _query_error(path: str, message: str) -> ResearchKBError:
    return ResearchKBError(
        Diagnostic(SCHEMA_VALIDATION_FAILED, "agent-task-state", None, path, message)
    )


__all__ = [
    "QUERY_TYPES",
    "KnowledgeQueryContext",
    "KnowledgeQueryContextService",
]
