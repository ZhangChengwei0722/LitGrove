from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from research_kb.bundle import records_of_kind
from research_kb.errors import (
    DUPLICATE_ID,
    SCHEMA_VALIDATION_FAILED,
    STEP7_BOUNDARY,
    UNRESOLVED_REFERENCE,
    Diagnostic,
    ResearchKBError,
)
from research_kb.identifiers import Namespace


STEP7_KIND_TO_TYPE = {
    "step7-synthesis": "synthesis",
    "step7-review-angle": "review_angle",
    "step7-insight": "insight",
    "step7-cross-view": "cross_view",
}
STEP7_KIND_TO_NAMESPACE = {
    "step7-synthesis": Namespace.SYNTHESIS,
    "step7-review-angle": Namespace.REVIEW_ANGLE,
    "step7-insight": Namespace.INSIGHT,
    "step7-cross-view": Namespace.CROSS_VIEW,
}
STEP7_KIND_TO_STORE = {
    "step7-synthesis": "step7_synthesis",
    "step7-review-angle": "step7_review_angles",
    "step7-insight": "step7_insights",
    "step7-cross-view": "step7_cross_views",
}
STEP7_TYPE_ORDER = ("synthesis", "review_angle", "insight", "cross_view")
STEP7_RECORD_KINDS = tuple(STEP7_KIND_TO_TYPE)
FRESHNESS_REASON_ORDER = (
    "question_mapping_newer",
    "mapping_membership_changed",
    "card_newer",
    "support_expansion_changed",
    "evidence_newer",
    "boundary_expansion_changed",
    "review_queue_newer",
    "domain_profile_changed",
    "source_view_newer",
    "source_view_stale",
)
ADMISSIBLE_UNIT_STATES = {"grounded", "revised"}
ADMISSIBLE_SOURCE_STATUSES = {"keep", "revise"}


@dataclass(frozen=True, slots=True)
class SupportClosure:
    question_mapping: dict[str, Any]
    paper_card_base: tuple[dict[str, Any], ...]
    evidence_base: tuple[str, ...]
    review_queue_refs: tuple[str, ...]
    input_snapshot: dict[str, Any]
    upstream_refs: tuple[str, ...]


def derive_support_closure(
    entries: list[tuple[str, dict[str, Any]]],
    *,
    question_id: str,
    paper_card_base: object,
    record_kind: str,
    record_id: str | None = None,
) -> SupportClosure:
    indexes = _indexes(entries)
    mapping = indexes.questions.get(question_id)
    if mapping is None:
        raise _error(
            UNRESOLVED_REFERENCE,
            record_kind,
            record_id,
            "/payload/question_id",
            "question mapping does not exist",
        )
    if mapping.get("mapping_status") == "needs_resolution":
        raise _error(
            STEP7_BOUNDARY,
            record_kind,
            record_id,
            "/payload/question_id",
            "needs-resolution question mapping cannot admit Step 7 candidates",
        )
    if _mapping_is_stale(mapping, indexes):
        raise _error(
            STEP7_BOUNDARY,
            record_kind,
            record_id,
            "/payload/question_id",
            "stale question mapping must be refreshed before Step 7 promotion",
        )
    if not isinstance(paper_card_base, list) or not paper_card_base:
        raise _error(
            SCHEMA_VALIDATION_FAILED,
            record_kind,
            record_id,
            "/payload/paper_card_base",
            "paper_card_base must be a non-empty array",
        )

    links = {link["paper_id"]: link for link in mapping["paper_links"]}
    normalized: list[dict[str, Any]] = []
    seen_papers: set[str] = set()
    seen_units: set[str] = set()
    evidence_ids: set[str] = set()
    queue_ids: set[str] = set()
    upstream_refs: set[str] = {question_id}

    for base_index, source in enumerate(paper_card_base):
        base_path = f"/payload/paper_card_base/{base_index}"
        if not isinstance(source, dict) or set(source) != {"paper_id", "card_unit_ids"}:
            raise _error(
                SCHEMA_VALIDATION_FAILED,
                record_kind,
                record_id,
                base_path,
                "paper_card_base entry must contain only paper_id and card_unit_ids",
            )
        paper_id = source.get("paper_id")
        unit_ids = source.get("card_unit_ids")
        if not isinstance(paper_id, str) or not isinstance(unit_ids, list) or not unit_ids:
            raise _error(
                SCHEMA_VALIDATION_FAILED,
                record_kind,
                record_id,
                base_path,
                "paper_card_base entry requires one paper and at least one Card Unit",
            )
        if paper_id in seen_papers:
            raise _error(DUPLICATE_ID, record_kind, record_id, "/payload/paper_card_base", "duplicate paper")
        if len(unit_ids) != len(set(unit_ids)) or any(not isinstance(value, str) for value in unit_ids):
            raise _error(DUPLICATE_ID, record_kind, record_id, base_path + "/card_unit_ids", "duplicate or invalid Card Unit ID")
        duplicate_units = seen_units.intersection(unit_ids)
        if duplicate_units:
            raise _error(DUPLICATE_ID, record_kind, record_id, base_path + "/card_unit_ids", "Card Unit appears more than once")
        link = links.get(paper_id)
        if link is None:
            raise _error(STEP7_BOUNDARY, record_kind, record_id, base_path + "/paper_id", "paper is outside the question mapping")
        selected = set(link["selected_card_unit_ids"])
        if not set(unit_ids).issubset(selected):
            raise _error(STEP7_BOUNDARY, record_kind, record_id, base_path + "/card_unit_ids", "Card Unit is outside the question mapping")

        seen_papers.add(paper_id)
        seen_units.update(unit_ids)
        upstream_refs.add(paper_id)
        for unit_id in unit_ids:
            owner_and_unit = indexes.units.get(unit_id)
            if owner_and_unit is None:
                raise _error(UNRESOLVED_REFERENCE, record_kind, record_id, base_path + "/card_unit_ids", "Card Unit does not exist")
            owner, unit = owner_and_unit
            if owner != paper_id:
                raise _error(STEP7_BOUNDARY, record_kind, record_id, base_path + "/card_unit_ids", "Card Unit belongs to another paper")
            if unit.get("grounding_status") not in ADMISSIBLE_UNIT_STATES:
                raise _error(STEP7_BOUNDARY, record_kind, record_id, base_path + "/card_unit_ids", "non-factual Card Unit cannot enter Step 7 support")
            upstream_refs.add(unit_id)
            for evidence_id in unit.get("evidence_ids", []):
                evidence = indexes.evidence.get(evidence_id)
                if evidence is None:
                    raise _error(UNRESOLVED_REFERENCE, record_kind, record_id, base_path + "/card_unit_ids", "Card Unit evidence does not exist")
                if evidence.get("paper_id") != paper_id:
                    raise _error(STEP7_BOUNDARY, record_kind, record_id, base_path + "/card_unit_ids", "Card Unit evidence belongs to another paper")
                evidence_ids.add(evidence_id)
                upstream_refs.add(evidence_id)
            for queue_id in unit.get("boundary_refs", []):
                queue = indexes.queues.get(queue_id)
                if queue is None:
                    raise _error(UNRESOLVED_REFERENCE, record_kind, record_id, base_path + "/card_unit_ids", "Card Unit boundary does not exist")
                if queue.get("paper_id") != paper_id or queue.get("not_evidence") is not True:
                    raise _error(STEP7_BOUNDARY, record_kind, record_id, base_path + "/card_unit_ids", "Card Unit boundary is invalid")
                queue_ids.add(queue_id)
                upstream_refs.add(queue_id)
        normalized.append({"paper_id": paper_id, "card_unit_ids": sorted(unit_ids)})

    if not evidence_ids:
        raise _error(STEP7_BOUNDARY, record_kind, record_id, "/payload/paper_card_base", "Step 7 support requires canonical Evidence")
    normalized.sort(key=lambda item: item["paper_id"])
    flattened_units = sorted(seen_units)
    profile = indexes.profile
    profile_version = profile.get("domain_profile", {}).get("version") if profile else None
    if not isinstance(profile_version, str):
        raise _error(UNRESOLVED_REFERENCE, record_kind, record_id, "/input_snapshot/domain_profile_version", "domain profile version is unavailable")
    evidence_base = tuple(sorted(evidence_ids))
    review_queue_refs = tuple(sorted(queue_ids))
    return SupportClosure(
        question_mapping=mapping,
        paper_card_base=tuple(normalized),
        evidence_base=evidence_base,
        review_queue_refs=review_queue_refs,
        input_snapshot={
            "domain_profile_version": profile_version,
            "card_unit_ids": flattened_units,
            "evidence_ids": list(evidence_base),
            "review_queue_ids": list(review_queue_refs),
        },
        upstream_refs=tuple(sorted(upstream_refs)),
    )


def validate_cross_view_sources(
    entries: list[tuple[str, dict[str, Any]]],
    *,
    question_id: str,
    source_views: object,
    record_kind: str,
    record_id: str | None = None,
) -> tuple[str, ...]:
    if not isinstance(source_views, list) or not source_views or any(not isinstance(value, str) for value in source_views):
        raise _error(SCHEMA_VALIDATION_FAILED, record_kind, record_id, "/payload/source_views", "source_views must be a non-empty array of candidate IDs")
    if len(source_views) != len(set(source_views)):
        raise _error(DUPLICATE_ID, record_kind, record_id, "/payload/source_views", "duplicate source candidate")
    candidates = _indexes(entries).candidates
    for source_id in source_views:
        if source_id == record_id:
            raise _error(STEP7_BOUNDARY, record_kind, record_id, "/payload/source_views", "Cross-View cannot reference itself")
        source = candidates.get(source_id)
        if source is None:
            raise _error(UNRESOLVED_REFERENCE, record_kind, record_id, "/payload/source_views", "source Step 7 candidate does not exist")
        if source.get("question_id") != question_id:
            raise _error(STEP7_BOUNDARY, record_kind, record_id, "/payload/source_views", "source Step 7 candidate belongs to another question")
        if source.get("candidate_status") not in ADMISSIBLE_SOURCE_STATUSES:
            raise _error(STEP7_BOUNDARY, record_kind, record_id, "/payload/source_views", "source Step 7 candidate is not admissible")
        if candidate_freshness(source, entries)["state"] != "current":
            raise _error(STEP7_BOUNDARY, record_kind, record_id, "/payload/source_views", "source Step 7 candidate is stale")
    return tuple(sorted(source_views))


def candidate_freshness(
    candidate: dict[str, Any],
    entries: list[tuple[str, dict[str, Any]]],
    *,
    _trail: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    indexes = _indexes(entries)
    candidate_id = candidate.get("candidate_id", "")
    if candidate_id in _trail:
        return {"state": "current", "reasons": []}
    trail = _trail | {candidate_id}
    updated_at = candidate.get("updated_at")
    reasons: set[str] = set()
    mapping = indexes.questions.get(candidate.get("question_id"))
    selected_units: list[tuple[str, str]] = []
    base_papers: set[str] = set()
    for base in candidate.get("paper_card_base", []):
        paper_id = base.get("paper_id")
        if not isinstance(paper_id, str):
            continue
        base_papers.add(paper_id)
        selected_units.extend((paper_id, unit_id) for unit_id in base.get("card_unit_ids", []) if isinstance(unit_id, str))

    if mapping is not None:
        if _is_after(mapping.get("updated_at"), updated_at):
            reasons.add("question_mapping_newer")
        mapping_units = {
            (link.get("paper_id"), unit_id)
            for link in mapping.get("paper_links", [])
            for unit_id in link.get("selected_card_unit_ids", [])
        }
        if mapping.get("mapping_status") == "needs_resolution" or not set(selected_units).issubset(mapping_units):
            reasons.add("mapping_membership_changed")

    current_evidence: set[str] = set()
    current_queues: set[str] = set()
    for paper_id, unit_id in selected_units:
        card = indexes.cards.get(paper_id)
        if card is not None and _is_after(card.get("updated_at"), updated_at):
            reasons.add("card_newer")
        owner_and_unit = indexes.units.get(unit_id)
        if owner_and_unit is None or owner_and_unit[0] != paper_id:
            continue
        unit = owner_and_unit[1]
        current_evidence.update(unit.get("evidence_ids", []))
        current_queues.update(unit.get("boundary_refs", []))

    stored_evidence = set(candidate.get("evidence_base", []))
    stored_queues = set(candidate.get("review_queue_refs", []))
    if current_evidence != stored_evidence:
        reasons.add("support_expansion_changed")
    if current_queues != stored_queues:
        reasons.add("boundary_expansion_changed")
    for evidence_id in current_evidence | stored_evidence:
        item = indexes.evidence.get(evidence_id)
        if item is not None and _is_after(item.get("updated_at"), updated_at):
            reasons.add("evidence_newer")
    for queue_id in current_queues | stored_queues:
        item = indexes.queues.get(queue_id)
        if item is not None and _is_after(item.get("updated_at"), updated_at):
            reasons.add("review_queue_newer")

    snapshot = candidate.get("input_snapshot", {})
    profile_version = indexes.profile.get("domain_profile", {}).get("version") if indexes.profile else None
    if snapshot.get("domain_profile_version") != profile_version:
        reasons.add("domain_profile_changed")

    if candidate.get("type") == "cross_view":
        for source_id in candidate.get("source_views", []):
            source = indexes.candidates.get(source_id)
            if source is None:
                continue
            if _is_after(source.get("updated_at"), updated_at):
                reasons.add("source_view_newer")
            source_freshness = candidate_freshness(source, entries, _trail=trail)
            if source_freshness["state"] != "current" or source.get("candidate_status") not in ADMISSIBLE_SOURCE_STATUSES:
                reasons.add("source_view_stale")

    ordered = [reason for reason in FRESHNESS_REASON_ORDER if reason in reasons]
    return {"state": "stale_upstream" if ordered else "current", "reasons": ordered}


@dataclass(slots=True)
class _Indexes:
    profile: dict[str, Any] | None
    cards: dict[str, dict[str, Any]]
    units: dict[str, tuple[str, dict[str, Any]]]
    evidence: dict[str, dict[str, Any]]
    queues: dict[str, dict[str, Any]]
    questions: dict[str, dict[str, Any]]
    candidates: dict[str, dict[str, Any]]


def _indexes(entries: list[tuple[str, dict[str, Any]]]) -> _Indexes:
    profile = next((record for kind, record in entries if kind == "domain-profile"), None)
    cards = {record["paper_id"]: record for record in records_of_kind(entries, "paper-card")}
    units = {
        unit["unit_id"]: (card["paper_id"], unit)
        for card in cards.values()
        for section in card.get("sections", [])
        for unit in section.get("units", [])
    }
    return _Indexes(
        profile=profile,
        cards=cards,
        units=units,
        evidence={record["evidence_id"]: record for record in records_of_kind(entries, "evidence")},
        queues={record["queue_id"]: record for record in records_of_kind(entries, "review-queue")},
        questions={record["question_id"]: record for kind, record in entries if kind == "question-mapping"},
        candidates={record["candidate_id"]: record for kind, record in entries if kind in STEP7_RECORD_KINDS},
    )


def _mapping_is_stale(mapping: dict[str, Any], indexes: _Indexes) -> bool:
    mapping_time = mapping.get("updated_at")
    for link in mapping.get("paper_links", []):
        paper_id = link.get("paper_id")
        card = indexes.cards.get(paper_id)
        if card is not None and _is_after(card.get("updated_at"), mapping_time):
            return True
        expanded_evidence: set[str] = set()
        required_boundaries: set[str] = set()
        for unit_id in link.get("selected_card_unit_ids", []):
            owner_and_unit = indexes.units.get(unit_id)
            if owner_and_unit is None or owner_and_unit[0] != paper_id:
                return True
            unit = owner_and_unit[1]
            expanded_evidence.update(unit.get("evidence_ids", []))
            required_boundaries.update(unit.get("boundary_refs", []))
        if expanded_evidence != set(link.get("evidence_ids", [])):
            return True
        if not required_boundaries.issubset(set(link.get("boundary_refs", []))):
            return True
        for evidence_id in link.get("evidence_ids", []):
            evidence = indexes.evidence.get(evidence_id)
            if evidence is not None and _is_after(evidence.get("updated_at"), mapping_time):
                return True
        for queue_id in link.get("boundary_refs", []):
            queue = indexes.queues.get(queue_id)
            if queue is not None and _is_after(queue.get("updated_at"), mapping_time):
                return True
    return False


def _is_after(candidate: object, baseline: object) -> bool:
    if not isinstance(candidate, str) or not isinstance(baseline, str):
        return False
    return _parse_timestamp(candidate) > _parse_timestamp(baseline)


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _error(
    code: str,
    record_kind: str,
    record_id: str | None,
    path: str,
    message: str,
) -> ResearchKBError:
    return ResearchKBError(Diagnostic(code, record_kind, record_id, path, message))


__all__ = [
    "FRESHNESS_REASON_ORDER",
    "STEP7_KIND_TO_NAMESPACE",
    "STEP7_KIND_TO_STORE",
    "STEP7_KIND_TO_TYPE",
    "STEP7_RECORD_KINDS",
    "STEP7_TYPE_ORDER",
    "SupportClosure",
    "candidate_freshness",
    "derive_support_closure",
    "validate_cross_view_sources",
]
