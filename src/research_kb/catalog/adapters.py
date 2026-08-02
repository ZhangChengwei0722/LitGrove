from __future__ import annotations

import hashlib
import unicodedata
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from research_kb.catalog.models import (
    CatalogDocument,
    CatalogSnapshot,
    CatalogSourceRecord,
    canonical_digest,
)
from research_kb.errors import DUPLICATE_ID, UNKNOWN_SCHEMA_KIND, Diagnostic, ResearchKBError
from research_kb.identity_corrections import project_registry_identity
from research_kb.pipeline_jobs import current_pipeline_states
from research_kb.primary_bundles import expand_active_primary_entries
from research_kb.review_bundles import expand_active_review_entries
from research_kb.organization_bundles import expand_active_organization_entries
from research_kb.source_assets import current_source_asset_heads


MAX_TITLE_CHARACTERS = 1_000
MAX_SUMMARY_CHARACTERS = 4_000
MAX_SEARCH_CHARACTERS = 32_000

Record = Mapping[str, Any]
Projector = Callable[[Record, str, str], tuple[CatalogDocument, ...]]
DetailProjector = Callable[[Record, str | None], dict[str, Any]]
IdGetter = Callable[[Record], str]


@dataclass(frozen=True, slots=True)
class CatalogRecordAdapter:
    record_kind: str
    adapter_version: str
    supported_contract_versions: tuple[str, ...]
    id_getter: IdGetter
    projector: Projector
    detail_projector: DetailProjector

    def record_id(self, record: Record) -> str:
        return self.id_getter(record)

    def project(self, record: Record, workspace_id: str, digest: str) -> tuple[CatalogDocument, ...]:
        version = record.get("schema_version")
        if version not in self.supported_contract_versions:
            raise ResearchKBError(
                Diagnostic(
                    UNKNOWN_SCHEMA_KIND,
                    self.record_kind,
                    self.record_id(record),
                    "/schema_version",
                    "catalog adapter does not support this record contract version",
                )
            )
        return self.projector(record, workspace_id, digest)

    def detail(self, record: Record, child_id: str | None) -> dict[str, Any]:
        return self.detail_projector(record, child_id)


class CatalogAdapterRegistry:
    def __init__(
        self,
        adapters: Iterable[CatalogRecordAdapter] | None = None,
        *,
        registry_version: str = "1.1",
        ignored_record_kinds: Iterable[str] = (),
    ):
        selected = tuple(_default_adapters() if adapters is None else adapters)
        by_kind: dict[str, CatalogRecordAdapter] = {}
        for adapter in selected:
            if adapter.record_kind in by_kind:
                raise ResearchKBError(
                    Diagnostic(
                        DUPLICATE_ID,
                        "catalog-adapter",
                        adapter.record_kind,
                        "/record_kind",
                        "duplicate catalog adapter record kind",
                    )
                )
            by_kind[adapter.record_kind] = adapter
        self.registry_version = registry_version
        self.adapters = by_kind
        self.ignored_record_kinds = frozenset(
            {
                "workspace",
                "domain-profile",
                "parsed-page",
                "review-queue",
                "discovery-candidate",
                "guardian-finding-disposition",
                "registry-identity-correction",
                "primary-semantic-bundle",
                "review-semantic-bundle",
                "direction-bundle",
                "field-map-bundle",
                "question-revision-bundle",
                *ignored_record_kinds,
            }
        )

    def capability(self, record_kinds: Iterable[str] = ()) -> dict[str, Any]:
        observed = set(record_kinds)
        return {
            "registry_version": self.registry_version,
            "adapters": [
                {
                    "record_kind": adapter.record_kind,
                    "adapter_version": adapter.adapter_version,
                    "supported_contract_versions": list(adapter.supported_contract_versions),
                }
                for adapter in sorted(self.adapters.values(), key=lambda item: item.record_kind)
            ],
            "ignored_record_kinds": sorted(self.ignored_record_kinds),
            "unregistered_record_kinds": sorted(
                observed - set(self.adapters) - self.ignored_record_kinds
            ),
        }

    def project_entries(
        self,
        entries: Iterable[tuple[str, dict[str, Any]]],
        *,
        workspace_id: str,
    ) -> CatalogSnapshot:
        selected_entries = _select_catalog_entries(
            tuple(
                expand_active_organization_entries(
                    expand_active_review_entries(expand_active_primary_entries(entries))
                )
            )
        )
        source_records: list[CatalogSourceRecord] = []
        documents: list[CatalogDocument] = []
        unknown: list[tuple[str, str]] = []
        seen_sources: set[str] = set()
        seen_items: set[str] = set()

        for record_kind, record in selected_entries:
            adapter = self.adapters.get(record_kind)
            if adapter is None:
                if record_kind not in self.ignored_record_kinds:
                    unknown.append((record_kind, canonical_digest(record)))
                continue
            record_id = adapter.record_id(record)
            source_key = f"{record_kind}:{record_id}"
            if source_key in seen_sources:
                raise ResearchKBError(
                    Diagnostic(
                        DUPLICATE_ID,
                        record_kind,
                        record_id,
                        "",
                        "duplicate catalog source record",
                    )
                )
            seen_sources.add(source_key)
            digest = canonical_digest(record)
            source_records.append(
                CatalogSourceRecord(
                    source_key,
                    record_kind,
                    record_id,
                    digest,
                    adapter.adapter_version,
                )
            )
            for document in adapter.project(record, workspace_id, digest):
                if document.source_key != source_key:
                    raise ValueError("catalog adapter returned a mismatched source key")
                if document.item_id in seen_items:
                    raise ResearchKBError(
                        Diagnostic(
                            DUPLICATE_ID,
                            "catalog-item",
                            document.item_id,
                            "/item_id",
                            "duplicate catalog item",
                        )
                    )
                seen_items.add(document.item_id)
                documents.append(document)

        source_records.sort(key=lambda item: item.source_key)
        documents.sort(key=lambda item: (item.sort_key, item.item_kind, item.item_id))
        unknown.sort()
        watermark = canonical_digest(
            {
                "registry_version": self.registry_version,
                "indexed": [
                    [
                        item.source_key,
                        item.source_record_digest,
                        item.adapter_version,
                    ]
                    for item in source_records
                ],
                "unknown": unknown,
            }
        )
        return CatalogSnapshot(
            workspace_id,
            self.registry_version,
            watermark,
            tuple(source_records),
            tuple(documents),
            tuple(sorted({kind for kind, _ in unknown})),
        )

    def find_adapter(self, record_kind: str) -> CatalogRecordAdapter:
        try:
            return self.adapters[record_kind]
        except KeyError as error:
            raise ResearchKBError(
                Diagnostic(
                    UNKNOWN_SCHEMA_KIND,
                    "catalog-adapter",
                    record_kind,
                    "/record_kind",
                    "catalog record kind has no registered adapter",
                )
            ) from error

    def select_entries(
        self,
        entries: Iterable[tuple[str, dict[str, Any]]],
    ) -> tuple[tuple[str, dict[str, Any]], ...]:
        return _select_catalog_entries(
            tuple(
                expand_active_organization_entries(
                    expand_active_review_entries(expand_active_primary_entries(entries))
                )
            )
        )


def _default_adapters() -> tuple[CatalogRecordAdapter, ...]:
    return (
        _adapter("registry-paper", "paper_id", _project_paper, _paper_detail),
        _adapter("paper-card", "paper_id", _project_card, _card_detail),
        _adapter("evidence", "evidence_id", _project_evidence, _evidence_detail),
        _adapter("review-memory", "review_memory_id", _project_review, _review_detail),
        _adapter("question-mapping", "question_id", _project_question, _question_detail),
        _adapter("direction", "direction_id", _project_direction, _direction_detail),
        _adapter("field-map-entry", "field_map_entry_id", _project_field_map, _field_map_detail),
        _adapter("step7-synthesis", "candidate_id", _project_step7, _step7_detail),
        _adapter("step7-review-angle", "candidate_id", _project_step7, _step7_detail),
        _adapter("step7-insight", "candidate_id", _project_step7, _step7_detail),
        _adapter("step7-cross-view", "candidate_id", _project_step7, _step7_detail),
        _adapter("process-event", "event_id", _project_event, _event_detail),
        _adapter("pipeline-job-state", "state_id", _project_job, _job_detail),
        _adapter(
            "source-adequacy-profile",
            "profile_id",
            _project_source_adequacy,
            _source_adequacy_detail,
        ),
        _adapter("source-asset-state", "source_asset_id", _project_source_asset, _source_asset_detail),
        _adapter("registry-identity-projection", "paper_id", _project_identity, _identity_detail),
        _adapter("guardian-report", "guardian_report_id", _project_guardian, _guardian_detail),
    )


def _adapter(
    record_kind: str,
    id_field: str,
    projector: Projector,
    detail: DetailProjector,
) -> CatalogRecordAdapter:
    return CatalogRecordAdapter(
        record_kind,
        "1.0",
        ("1.0",),
        lambda record, field=id_field: str(record[field]),
        projector,
        detail,
    )


def _project_paper(record: Record, workspace_id: str, digest: str) -> tuple[CatalogDocument, ...]:
    bibliography = record["bibliography"]
    title = bibliography.get("title") or "Untitled paper"
    summary = _join(
        [
            ", ".join(bibliography.get("authors", [])),
            str(bibliography.get("year") or ""),
            bibliography.get("doi") or "",
        ]
    )
    return (
        _document(
            record_kind="registry-paper",
            record_id=str(record["paper_id"]),
            child_id=None,
            item_kind="paper",
            authority_layer="canonical",
            paper_id=str(record["paper_id"]),
            question_id=None,
            title=title,
            summary=summary,
            statuses=(
                f"screening:{record['screening_status']}",
                f"review:{record['review_status']}",
                f"automation:{record['automation_status']}",
            ),
            search_text=_join([title, summary]),
            digest=digest,
        ),
    )


def _project_card(record: Record, workspace_id: str, digest: str) -> tuple[CatalogDocument, ...]:
    documents = []
    for section in record["sections"]:
        for unit in section["units"]:
            documents.append(
                _document(
                    record_kind="paper-card",
                    record_id=str(record["paper_id"]),
                    child_id=str(unit["unit_id"]),
                    item_kind="paper_card_unit",
                    authority_layer="canonical",
                    paper_id=str(record["paper_id"]),
                    question_id=None,
                    title=unit["statement"],
                    summary=section["section_id"].replace("_", " "),
                    statuses=(
                        f"grounding:{unit['grounding_status']}",
                        f"review:{record['review_status']}",
                    ),
                    search_text=_join(
                        [unit["statement"], unit["statement_type"], section["section_id"]]
                    ),
                    digest=digest,
                )
            )
    return tuple(documents)


def _project_evidence(record: Record, workspace_id: str, digest: str) -> tuple[CatalogDocument, ...]:
    return (
        _document(
            record_kind="evidence",
            record_id=str(record["evidence_id"]),
            child_id=None,
            item_kind="evidence",
            authority_layer="canonical",
            paper_id=str(record["paper_id"]),
            question_id=None,
            title=record["claim"],
            summary=record["support_scope"],
            statuses=(
                f"type:{record['evidence_type']}",
                f"review:{record['review_status']}",
            ),
            search_text=_join(
                [
                    record["claim"],
                    record["quote"],
                    record["support_scope"],
                    *record["what_it_does_not_support"],
                ]
            ),
            digest=digest,
        ),
    )


def _project_review(record: Record, workspace_id: str, digest: str) -> tuple[CatalogDocument, ...]:
    documents = [
        _document(
            record_kind="review-memory",
            record_id=str(record["review_memory_id"]),
            child_id=None,
            item_kind="review_memory",
            authority_layer="canonical",
            paper_id=str(record["paper_id"]),
            question_id=None,
            title=record["one_sentence_reuse_value"],
            summary=record["memory_value"]["reason"],
            statuses=(
                f"memory:{record['memory_value']['status']}",
                f"read:{record['read_status']}",
                "background_only",
            ),
            search_text=_join(
                [
                    record["one_sentence_reuse_value"],
                    record["memory_value"]["reason"],
                    *record["scope_tags"],
                ]
            ),
            digest=digest,
        )
    ]
    for section in record["sections"]:
        for unit in section["units"]:
            source_text = [note["text"] for note in unit["source_notes"]]
            impacts = [impact["action"] for impact in unit["workflow_impacts"]]
            documents.append(
                _document(
                    record_kind="review-memory",
                    record_id=str(record["review_memory_id"]),
                    child_id=str(unit["review_unit_id"]),
                    item_kind="review_unit",
                    authority_layer="canonical",
                    paper_id=str(record["paper_id"]),
                    question_id=None,
                    title=unit["content"],
                    summary=section["section_id"].replace("_", " "),
                    statuses=(
                        f"unit_type:{unit['unit_type']}",
                        "background_only",
                    ),
                    search_text=_join([unit["content"], *source_text, *impacts]),
                    digest=digest,
                )
            )
    return tuple(documents)


def _project_question(record: Record, workspace_id: str, digest: str) -> tuple[CatalogDocument, ...]:
    rationales = [link["relevance_rationale"] for link in record["paper_links"]]
    return (
        _document(
            record_kind="question-mapping",
            record_id=str(record["question_id"]),
            child_id=None,
            item_kind="question",
            authority_layer="canonical",
            paper_id=None,
            question_id=str(record["question_id"]),
            title=record["question_text"],
            summary=record["scope"],
            statuses=(f"mapping:{record['mapping_status']}",),
            search_text=_join([record["question_text"], record["scope"], *rationales]),
            digest=digest,
        ),
    )


def _project_direction(record: Record, workspace_id: str, digest: str) -> tuple[CatalogDocument, ...]:
    del workspace_id
    return (
        _document(
            record_kind="direction",
            record_id=str(record["direction_id"]),
            child_id=None,
            item_kind="research_direction",
            authority_layer="canonical",
            paper_id=None,
            question_id=None,
            title=record["name"],
            summary=record["scope"],
            statuses=(f"direction:{record['status']}",),
            search_text=_join([record["name"], record["scope"], *record["gap_notes"]]),
            digest=digest,
        ),
    )


def _project_field_map(record: Record, workspace_id: str, digest: str) -> tuple[CatalogDocument, ...]:
    del workspace_id
    return (
        _document(
            record_kind="field-map-entry",
            record_id=str(record["field_map_entry_id"]),
            child_id=None,
            item_kind="field_map_entry",
            authority_layer="canonical",
            paper_id=None,
            question_id=None,
            title=record["title"],
            summary=record["definition"],
            statuses=(
                f"field_map:{record['status']}",
                f"consensus:{record['consensus_level']}",
            ),
            search_text=_join(
                [record["title"], record["definition"], record["entry_type"], *record["aspect_notes"]]
            ),
            digest=digest,
        ),
    )


def _project_step7(record: Record, workspace_id: str, digest: str) -> tuple[CatalogDocument, ...]:
    content_fields = {
        "step7-synthesis": ("claim", "scope", "agreement_pattern", "conflict_pattern", "boundary_statement"),
        "step7-review-angle": ("thesis", "why_this_angle_adds_value"),
        "step7-insight": ("hypothesis_or_idea", "rationale", "falsification_condition", "minimum_test"),
        "step7-cross-view": ("why_interesting", "shared_dimension", "non_equivalence_warning"),
    }
    values: list[str] = [record["title"], *record["missing_evidence"], *record["assumptions"], *record["risk"]]
    for field in content_fields[record_kind_from_step7(record)]:
        value = record.get(field)
        if isinstance(value, str):
            values.append(value)
    for field in ("organizing_axes", "included_clusters", "excluded_scope"):
        values.extend(str(item) for item in record.get(field, []))
    kind = record_kind_from_step7(record)
    return (
        _document(
            record_kind=kind,
            record_id=str(record["candidate_id"]),
            child_id=None,
            item_kind=record["type"],
            authority_layer="canonical",
            paper_id=None,
            question_id=str(record["question_id"]),
            title=record["title"],
            summary=next((value for value in values[1:] if value), record["next_action"]),
            statuses=(
                f"candidate:{record['candidate_status']}",
                f"trace:{record['trace_status']}",
                "not_fact",
            ),
            search_text=_join(values),
            digest=digest,
        ),
    )


def _project_event(record: Record, workspace_id: str, digest: str) -> tuple[CatalogDocument, ...]:
    return (
        _document(
            record_kind="process-event",
            record_id=str(record["event_id"]),
            child_id=None,
            item_kind="process_event",
            authority_layer="operational",
            paper_id=None,
            question_id=None,
            title=record["operation"].replace("_", " "),
            summary=f"{record['actor']} / {record['result']}",
            statuses=(f"result:{record['result']}", f"actor:{record['actor']}"),
            search_text=_join([record["operation"], record["actor"], record["result"]]),
            digest=digest,
        ),
    )


def _project_job(record: Record, workspace_id: str, digest: str) -> tuple[CatalogDocument, ...]:
    wait_reason = record.get("wait_reason")
    return (
        _document(
            record_kind="pipeline-job-state",
            record_id=str(record["state_id"]),
            child_id=None,
            item_kind="pipeline_job",
            authority_layer="operational",
            paper_id=None,
            question_id=None,
            title=f"Pipeline Job {record['job_id'][-12:]}",
            summary=f"{record['requested_route']} / {record['current_node']} / {record['status']}",
            statuses=tuple(
                value
                for value in (
                    f"job:{record['status']}",
                    f"route:{record['requested_route']}",
                    f"wait:{wait_reason}" if wait_reason is not None else None,
                )
                if value is not None
            ),
            search_text=_join(
                [
                    record["job_id"],
                    record["requested_route"],
                    record["requested_depth"],
                    record["current_node"],
                    record["status"],
                    wait_reason or "",
                ]
            ),
            digest=digest,
        ),
    )


def _project_guardian(record: Record, workspace_id: str, digest: str) -> tuple[CatalogDocument, ...]:
    codes = [finding["code"] for finding in record["findings"]]
    return (
        _document(
            record_kind="guardian-report",
            record_id=str(record["guardian_report_id"]),
            child_id=None,
            item_kind="guardian_report",
            authority_layer="operational",
            paper_id=None,
            question_id=None,
            title="Guardian report",
            summary=f"{record['status']} / {len(codes)} findings",
            statuses=(f"guardian:{record['status']}",),
            search_text=_join([record["status"], *codes]),
            digest=digest,
        ),
    )


def _project_source_adequacy(record: Record, workspace_id: str, digest: str) -> tuple[CatalogDocument, ...]:
    del workspace_id
    operation = str(record["requested_operation"])
    capability_statuses = tuple(
        f"capability:{name}:{value['status']}"
        for name, value in sorted(record["capabilities"].items())
    )
    return (
        _document(
            record_kind="source-adequacy-profile",
            record_id=str(record["profile_id"]),
            child_id=None,
            item_kind="source_adequacy",
            authority_layer="operational",
            paper_id=str(record["paper_id"]),
            question_id=None,
            title=operation.replace("_", " "),
            summary=f"{record['assessed_by']} / {record['assessment_rule_version']}",
            statuses=(f"operation:{operation}", *capability_statuses),
            search_text=_join(
                [
                    operation,
                    record["assessed_by"],
                    *record["known_limitations"],
                    *record["recommended_actions"],
                ]
            ),
            digest=digest,
        ),
    )


def _project_source_asset(record: Record, workspace_id: str, digest: str) -> tuple[CatalogDocument, ...]:
    del workspace_id
    if record["availability"] != "available":
        currentness = "unavailable"
    elif record["manifestation_status"] == "change_candidate":
        currentness = "stale_source"
    else:
        currentness = "current"
    role = str(record["asset_role"])
    paper_id = record.get("paper_id")
    return (
        _document(
            record_kind="source-asset-state",
            record_id=str(record["source_asset_id"]),
            child_id=None,
            item_kind="source_asset",
            authority_layer="canonical",
            paper_id=str(paper_id) if paper_id is not None else None,
            question_id=None,
            title=role.replace("_", " "),
            summary=f"{currentness} / {record['availability']}",
            statuses=(
                f"source:{currentness}",
                f"availability:{record['availability']}",
                f"role:{role}",
            ),
            search_text=_join([role, currentness, str(record["availability"])]),
            digest=digest,
        ),
    )


def _project_identity(record: Record, workspace_id: str, digest: str) -> tuple[CatalogDocument, ...]:
    del workspace_id
    return (
        _document(
            record_kind="registry-identity-projection",
            record_id=str(record["paper_id"]),
            child_id=None,
            item_kind="paper_identity",
            authority_layer="canonical_projection",
            paper_id=str(record["paper_id"]),
            question_id=None,
            title="Paper identity status",
            summary=f"{record['library_status']} / {record['canonical_paper_id']}",
            statuses=(
                f"library:{record['library_status']}",
                "identity:redirected" if record["canonical_paper_id"] != record["paper_id"] else "identity:self",
            ),
            search_text=_join([str(record["library_status"]), str(record["canonical_paper_id"])]),
            digest=digest,
        ),
    )


def _document(
    *,
    record_kind: str,
    record_id: str,
    child_id: str | None,
    item_kind: str,
    authority_layer: str,
    paper_id: str | None,
    question_id: str | None,
    title: str,
    summary: str,
    statuses: tuple[str, ...],
    search_text: str,
    digest: str,
) -> CatalogDocument:
    stable_source = f"{record_kind}:{record_id}:{child_id or ''}"
    item_id = "catalog_" + hashlib.sha256(stable_source.encode("utf-8")).hexdigest()[:32]
    bounded_title, title_truncated = _bounded(title, MAX_TITLE_CHARACTERS)
    bounded_summary, summary_truncated = _bounded(summary, MAX_SUMMARY_CHARACTERS)
    bounded_search, search_truncated = _bounded(search_text, MAX_SEARCH_CHARACTERS)
    labels = tuple(sorted({*statuses, *({"projection_truncated"} if any((title_truncated, summary_truncated, search_truncated)) else set())}))
    return CatalogDocument(
        item_id,
        item_kind,
        authority_layer,
        f"{record_kind}:{record_id}",
        record_kind,
        record_id,
        child_id,
        paper_id,
        question_id,
        bounded_title,
        bounded_summary,
        labels,
        bounded_search,
        _sort_key(bounded_title),
        digest,
        "1.0",
    )


def _paper_detail(record: Record, child_id: str | None) -> dict[str, Any]:
    return {
        "bibliography": dict(record["bibliography"]),
        "screening_status": record["screening_status"],
        "review_status": record["review_status"],
        "automation_status": record["automation_status"],
        "created_at": record["created_at"],
        "updated_at": record["updated_at"],
    }


def _card_detail(record: Record, child_id: str | None) -> dict[str, Any]:
    unit = _find_nested_unit(record, child_id, "unit_id")
    return {
        "paper_id": record["paper_id"],
        "card_status": record["card_status"],
        "review_status": record["review_status"],
        "unit": unit,
    }


def _evidence_detail(record: Record, child_id: str | None) -> dict[str, Any]:
    return {
        key: record[key]
        for key in (
            "evidence_id",
            "paper_id",
            "claim",
            "evidence_type",
            "quote",
            "source_page",
            "locator",
            "support_scope",
            "what_it_does_not_support",
            "review_status",
            "automation_status",
            "created_at",
            "updated_at",
        )
    }


def _review_detail(record: Record, child_id: str | None) -> dict[str, Any]:
    common = {
        "review_memory_id": record["review_memory_id"],
        "paper_id": record["paper_id"],
        "review_subtype": record["review_subtype"],
        "read_status": record["read_status"],
        "scope_tags": record["scope_tags"],
        "one_sentence_reuse_value": record["one_sentence_reuse_value"],
        "memory_value": record["memory_value"],
        "coverage_limits": record["coverage_limits"],
        "source_type": record["source_type"],
        "source_fingerprint": record["source_fingerprint"],
        "parse_snapshot": record["parse_snapshot"],
        "background_only": record["background_only"],
        "can_enter_canonical_evidence": record["can_enter_canonical_evidence"],
        "not_fact": record["not_fact"],
        "review_status": record["review_status"],
        "automation_status": record["automation_status"],
        "created_at": record["created_at"],
        "updated_at": record["updated_at"],
    }
    if child_id is not None:
        common["unit"] = _find_nested_unit(record, child_id, "review_unit_id")
    return common


def _question_detail(record: Record, child_id: str | None) -> dict[str, Any]:
    return {
        key: record[key]
        for key in (
            "question_id",
            "question_text",
            "scope",
            "domain_profile_id",
            "paper_links",
            "mapping_status",
            "created_at",
            "updated_at",
        )
    }


def _direction_detail(record: Record, child_id: str | None) -> dict[str, Any]:
    del child_id
    return {
        "direction_id": record["direction_id"],
        "name": record["name"],
        "scope": record["scope"],
        "status": record["status"],
        "links": record["links"],
        "gap_notes": record["gap_notes"],
    }


def _field_map_detail(record: Record, child_id: str | None) -> dict[str, Any]:
    del child_id
    return {
        "field_map_entry_id": record["field_map_entry_id"],
        "title": record["title"],
        "entry_type": record["entry_type"],
        "definition": record["definition"],
        "status": record["status"],
        "consensus_level": record["consensus_level"],
        "direction_refs": record["direction_refs"],
        "links": record["links"],
        "aspect_notes": record["aspect_notes"],
    }


def _step7_detail(record: Record, child_id: str | None) -> dict[str, Any]:
    return {
        key: value
        for key, value in record.items()
        if key not in {"fixture_origin"}
    }


def _event_detail(record: Record, child_id: str | None) -> dict[str, Any]:
    detail = {
        key: record[key]
        for key in (
            "event_id",
            "operation",
            "actor",
            "result",
            "input_refs",
            "output_refs",
            "created_at",
        )
    }
    if record.get("job_id") is not None:
        detail["job_id"] = record["job_id"]
    return detail


def _job_detail(record: Record, child_id: str | None) -> dict[str, Any]:
    del child_id
    return {
        key: record[key]
        for key in (
            "job_id",
            "state_id",
            "revision",
            "requested_route",
            "requested_depth",
            "current_node",
            "status",
            "wait_reason",
            "retry_count",
            "terminal_receipt",
            "updated_at",
        )
    } | {
        "input_ref_count": len(record["input_refs"]),
        "output_ref_count": len(record["output_refs"]),
    }


def _guardian_detail(record: Record, child_id: str | None) -> dict[str, Any]:
    return {
        "guardian_report_id": record["guardian_report_id"],
        "status": record["status"],
        "finding_count": len(record["findings"]),
        "finding_codes": sorted({item["code"] for item in record["findings"]}),
        "created_at": record["created_at"],
    }


def _source_asset_detail(record: Record, child_id: str | None) -> dict[str, Any]:
    del child_id
    if record["availability"] != "available":
        currentness = "unavailable"
    elif record["manifestation_status"] == "change_candidate":
        currentness = "stale_source"
    else:
        currentness = "current"
    return {
        "source_asset_id": record["source_asset_id"],
        "source_asset_state_id": record["source_asset_state_id"],
        "paper_id": record["paper_id"],
        "asset_role": record["asset_role"],
        "source_availability": record["availability"],
        "source_currentness": currentness,
        "manifestation_status": record["manifestation_status"],
        "updated_at": record["updated_at"],
    }


def _source_adequacy_detail(record: Record, child_id: str | None) -> dict[str, Any]:
    del child_id
    return {
        "profile_id": record["profile_id"],
        "basis_profile_id": (
            None if record["basis_profile"] is None else record["basis_profile"]["profile_id"]
        ),
        "paper_id": record["paper_id"],
        "job_id": record["job_id"],
        "requested_operation": record["requested_operation"],
        "assessment_rule_version": record["assessment_rule_version"],
        "assessed_by": record["assessed_by"],
        "assessed_at": record["assessed_at"],
        "capabilities": record["capabilities"],
        "known_limitations": record["known_limitations"],
        "recommended_actions": record["recommended_actions"],
    }


def _identity_detail(record: Record, child_id: str | None) -> dict[str, Any]:
    del child_id
    return {
        "paper_id": record["paper_id"],
        "canonical_paper_id": record["canonical_paper_id"],
        "library_status": record["library_status"],
    }


def _find_nested_unit(record: Record, child_id: str | None, id_field: str) -> dict[str, Any]:
    if child_id is None:
        raise ValueError("catalog unit detail requires a child ID")
    for section in record["sections"]:
        for unit in section["units"]:
            if unit[id_field] == child_id:
                return dict(unit)
    raise KeyError(child_id)


def record_kind_from_step7(record: Record) -> str:
    return {
        "synthesis": "step7-synthesis",
        "review_angle": "step7-review-angle",
        "insight": "step7-insight",
        "cross_view": "step7-cross-view",
    }[record["type"]]


def _join(values: Iterable[str]) -> str:
    return "\n".join(value.strip() for value in values if isinstance(value, str) and value.strip())


def _bounded(value: str, limit: int) -> tuple[str, bool]:
    if len(value) <= limit:
        return value, False
    return value[:limit], True


def _sort_key(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def _select_catalog_entries(
    entries: tuple[tuple[str, dict[str, Any]], ...],
) -> tuple[tuple[str, dict[str, Any]], ...]:
    job_states = [record for kind, record in entries if kind == "pipeline-job-state"]
    current_state_ids = {
        item["state_id"] for item in current_pipeline_states(job_states)
    }
    selected = [
        (kind, record)
        for kind, record in entries
        if (
            (kind != "pipeline-job-state" or record["state_id"] in current_state_ids)
            and kind not in {"source-asset-state", "registry-identity-correction"}
        )
    ]
    source_states = [record for kind, record in entries if kind == "source-asset-state"]
    selected.extend(("source-asset-state", item) for item in current_source_asset_heads(source_states))
    adequacy_profiles: dict[tuple[str, str], dict[str, Any]] = {}
    for kind, record in entries:
        if kind != "source-adequacy-profile":
            continue
        key = (record["paper_id"], record["requested_operation"])
        existing = adequacy_profiles.get(key)
        if existing is None or (record["assessed_at"], record["profile_id"]) > (
            existing["assessed_at"],
            existing["profile_id"],
        ):
            adequacy_profiles[key] = record
    selected = [
        (kind, record)
        for kind, record in selected
        if kind != "source-adequacy-profile"
    ]
    selected.extend(
        ("source-adequacy-profile", record)
        for _, record in sorted(adequacy_profiles.items())
    )
    corrections = _ordered_identity_corrections(
        [record for kind, record in entries if kind == "registry-identity-correction"]
    )
    if corrections:
        papers = [record for kind, record in entries if kind == "registry-paper"]
        projection = project_registry_identity(papers, corrections)
        selected.extend(
            (
                "registry-identity-projection",
                {
                    "schema_version": "1.0",
                    **value,
                },
            )
            for value in projection.values()
            if value["canonical_paper_id"] != value["paper_id"] or value["library_status"] != "active"
        )
    return tuple(selected)


def _ordered_identity_corrections(corrections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not corrections:
        return []
    by_previous = {item.get("previous_correction_id"): item for item in corrections}
    ordered: list[dict[str, Any]] = []
    current = by_previous.get(None)
    while current is not None and len(ordered) < len(corrections):
        ordered.append(current)
        current = by_previous.get(current.get("correction_id"))
    return ordered if len(ordered) == len(corrections) else corrections


__all__ = ["CatalogAdapterRegistry", "CatalogRecordAdapter"]
