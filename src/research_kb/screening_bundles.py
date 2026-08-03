from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from research_kb.catalog.models import canonical_digest
from research_kb.errors import DUPLICATE_ID, GROUNDING_MISMATCH, INCOMPLETE_TRANSACTION, Diagnostic, ResearchKBError
from research_kb.identity_corrections import project_registry_identity
from research_kb.organization_bundles import expand_active_organization_entries


BundleEntry = tuple[str, dict[str, Any]]


def active_screening_criteria(bundle: Mapping[str, Any]) -> dict[str, Any] | None:
    revision = _active_revision(bundle)
    return None if revision is None else dict(revision.get("criteria", {}))


def active_screening_decision(bundle: Mapping[str, Any]) -> dict[str, Any] | None:
    revision = _active_revision(bundle)
    return None if revision is None else dict(revision.get("decision", {}))


def screening_criteria_bundle_diagnostics(bundle: Mapping[str, Any]) -> list[Diagnostic]:
    diagnostics = _revision_diagnostics(bundle, "screening-criteria-bundle", "criteria_id", "criteria")
    criteria_id = bundle.get("criteria_id")
    question_id = bundle.get("question_id")
    seen_items: dict[str, str] = {}
    for revision in bundle.get("revisions", []):
        criteria = revision.get("criteria", {})
        if criteria.get("criteria_id") != criteria_id or criteria.get("question_id") != question_id:
            diagnostics.append(_diag("screening-criteria-bundle", criteria_id, "/revisions", "criteria revision belongs to another stable identity"))
        revision_items: set[str] = set()
        for item_kind in ("inclusion_criteria", "exclusion_criteria"):
            for item in criteria.get(item_kind, []):
                item_id = item.get("criterion_id")
                identity = item_kind
                if item_id in revision_items:
                    diagnostics.append(_diag("screening-criteria-bundle", criteria_id, "/revisions", "criterion ID is duplicated within a revision"))
                revision_items.add(item_id)
                if item_id in seen_items and seen_items[item_id] != identity:
                    diagnostics.append(_diag("screening-criteria-bundle", criteria_id, "/revisions", "criterion ID is reused for different content"))
                seen_items[item_id] = identity
    return diagnostics


def screening_decision_bundle_diagnostics(bundle: Mapping[str, Any]) -> list[Diagnostic]:
    diagnostics = _revision_diagnostics(bundle, "screening-decision-bundle", "decision_id", "decision")
    decision_id = bundle.get("decision_id")
    for revision in bundle.get("revisions", []):
        decision = revision.get("decision", {})
        if (
            decision.get("decision_id") != decision_id
            or decision.get("question_id") != bundle.get("question_id")
            or decision.get("paper_id") != bundle.get("paper_id")
        ):
            diagnostics.append(_diag("screening-decision-bundle", decision_id, "/revisions", "decision revision belongs to another stable identity"))
        disposition_ids = [item.get("criterion_id") for item in decision.get("criterion_dispositions", [])]
        if len(disposition_ids) != len(set(disposition_ids)):
            diagnostics.append(_diag("screening-decision-bundle", decision_id, "/revisions", "criterion dispositions must be unique"))
    return diagnostics


def screening_entries_diagnostics(entries: Iterable[BundleEntry]) -> list[Diagnostic]:
    materialized = list(entries)
    diagnostics: list[Diagnostic] = []
    questions = {
        record.get("question_id")
        for kind, record in expand_active_organization_entries(materialized)
        if kind == "question-mapping"
    }
    papers = [record for kind, record in materialized if kind == "registry-paper"]
    corrections = [record for kind, record in materialized if kind == "registry-identity-correction"]
    paper_projection = project_registry_identity(papers, corrections)
    criteria_by_revision: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    active_by_question: dict[str, str] = {}
    pairs: dict[tuple[str, str], str] = {}
    for kind, bundle in materialized:
        if kind == "screening-criteria-bundle":
            diagnostics.extend(screening_criteria_bundle_diagnostics(bundle))
            if bundle.get("question_id") not in questions:
                diagnostics.append(_diag(kind, bundle.get("criteria_id"), "/question_id", "screening criteria references an unavailable Question", GROUNDING_MISMATCH))
            for revision in bundle.get("revisions", []):
                criteria_by_revision[str(revision.get("revision_id"))] = (bundle, revision)
            active = active_screening_criteria(bundle)
            if active is not None and active.get("status") == "active":
                owner = active_by_question.get(str(bundle.get("question_id")))
                if owner is not None and owner != bundle.get("criteria_id"):
                    diagnostics.append(_diag(kind, bundle.get("criteria_id"), "/question_id", "multiple active criteria sets govern one Question", DUPLICATE_ID))
                active_by_question[str(bundle.get("question_id"))] = str(bundle.get("criteria_id"))
        elif kind == "screening-decision-bundle":
            diagnostics.extend(screening_decision_bundle_diagnostics(bundle))
            pair = (str(bundle.get("question_id")), str(bundle.get("paper_id")))
            owner = pairs.get(pair)
            if owner is not None and owner != bundle.get("decision_id"):
                diagnostics.append(_diag(kind, bundle.get("decision_id"), "", "multiple decisions own one Question-Paper pair", DUPLICATE_ID))
            pairs[pair] = str(bundle.get("decision_id"))
            decision = active_screening_decision(bundle)
            if decision is None:
                continue
            if bundle.get("question_id") not in questions:
                diagnostics.append(_diag(kind, bundle.get("decision_id"), "/question_id", "screening decision references an unavailable Question", GROUNDING_MISMATCH))
            paper = paper_projection.get(str(bundle.get("paper_id")))
            if paper is None or paper.get("canonical_paper_id") != bundle.get("paper_id") or paper.get("library_status") != "active":
                diagnostics.append(_diag(kind, bundle.get("decision_id"), "/paper_id", "screening decision references an unavailable canonical Paper", GROUNDING_MISMATCH))
            basis = criteria_by_revision.get(str(decision.get("criteria_revision_id")))
            if basis is None or basis[0].get("question_id") != bundle.get("question_id"):
                diagnostics.append(_diag(kind, bundle.get("decision_id"), "/criteria_revision_id", "screening decision criteria revision is unavailable", GROUNDING_MISMATCH))
                continue
            revision = basis[1]
            if decision.get("criteria_digest") != revision.get("content_digest"):
                diagnostics.append(_diag(kind, bundle.get("decision_id"), "/criteria_digest", "screening decision criteria digest does not match its revision", GROUNDING_MISMATCH))
            expected = {
                item.get("criterion_id")
                for field in ("inclusion_criteria", "exclusion_criteria")
                for item in revision.get("criteria", {}).get(field, [])
            }
            actual = {item.get("criterion_id") for item in decision.get("criterion_dispositions", [])}
            if actual != expected:
                diagnostics.append(_diag(kind, bundle.get("decision_id"), "/criterion_dispositions", "screening decision does not close over every criterion", GROUNDING_MISMATCH))
    return diagnostics


def decision_freshness(bundle: Mapping[str, Any], entries: Iterable[BundleEntry]) -> dict[str, Any]:
    materialized = list(entries)
    decision = active_screening_decision(bundle)
    if decision is None:
        return {"state": "invalid", "reasons": ["active_revision_unavailable"]}
    questions = {
        record.get("question_id")
        for kind, record in expand_active_organization_entries(materialized)
        if kind == "question-mapping"
    }
    if bundle.get("question_id") not in questions:
        return {"state": "question_unavailable", "reasons": ["question_unavailable"]}
    papers = [record for kind, record in materialized if kind == "registry-paper"]
    corrections = [record for kind, record in materialized if kind == "registry-identity-correction"]
    paper = project_registry_identity(papers, corrections).get(str(bundle.get("paper_id")))
    if paper is None or paper.get("canonical_paper_id") != bundle.get("paper_id") or paper.get("library_status") != "active":
        return {"state": "paper_unavailable", "reasons": ["paper_unavailable"]}
    active_criteria = [
        item
        for kind, item in materialized
        if kind == "screening-criteria-bundle"
        and item.get("question_id") == bundle.get("question_id")
        and (active_screening_criteria(item) or {}).get("status") == "active"
    ]
    if len(active_criteria) != 1:
        return {"state": "stale_criteria", "reasons": ["active_criteria_unavailable"]}
    criteria_bundle = active_criteria[0]
    if (
        decision.get("criteria_revision_id") != criteria_bundle.get("active_revision_id")
        or decision.get("criteria_digest") != criteria_bundle.get("revisions", [])[-1].get("content_digest")
    ):
        return {"state": "stale_criteria", "reasons": ["criteria_superseded"]}
    return {"state": "current", "reasons": []}


def current_included_decision(question_id: str, paper_id: str, entries: Iterable[BundleEntry]) -> dict[str, Any] | None:
    materialized = list(entries)
    matches = [
        bundle
        for kind, bundle in materialized
        if kind == "screening-decision-bundle"
        and bundle.get("question_id") == question_id
        and bundle.get("paper_id") == paper_id
    ]
    if len(matches) != 1 or decision_freshness(matches[0], materialized)["state"] != "current":
        return None
    decision = active_screening_decision(matches[0])
    return decision if decision is not None and decision.get("outcome") == "included" else None


def active_criteria_for_question(question_id: str, entries: Iterable[BundleEntry]) -> dict[str, Any] | None:
    matches = [
        bundle
        for kind, bundle in entries
        if kind == "screening-criteria-bundle"
        and bundle.get("question_id") == question_id
        and (active_screening_criteria(bundle) or {}).get("status") == "active"
    ]
    return matches[0] if len(matches) == 1 else None


def require_screening_eligible_links(question_id: str, paper_ids: Iterable[str], entries: Iterable[BundleEntry]) -> None:
    materialized = list(entries)
    criteria = active_criteria_for_question(question_id, materialized)
    if criteria is None:
        return
    for paper_id in paper_ids:
        if current_included_decision(question_id, paper_id, materialized) is None:
            raise ResearchKBError(
                Diagnostic(
                    GROUNDING_MISMATCH,
                    "question-mapping",
                    question_id,
                    "/paper_links",
                    f"Paper {paper_id} lacks a current included Question-specific screening decision",
                )
            )


def _active_revision(bundle: Mapping[str, Any]) -> dict[str, Any] | None:
    active_id = bundle.get("active_revision_id")
    matches = [dict(item) for item in bundle.get("revisions", []) if item.get("revision_id") == active_id]
    return matches[0] if len(matches) == 1 else None


def _revision_diagnostics(bundle: Mapping[str, Any], kind: str, stable_field: str, child_field: str) -> list[Diagnostic]:
    stable_id = bundle.get(stable_field)
    revisions = list(bundle.get("revisions", []))
    diagnostics: list[Diagnostic] = []
    ids = [item.get("revision_id") for item in revisions]
    if len(ids) != len(set(ids)):
        diagnostics.append(_diag(kind, stable_id, "/revisions", "revision IDs must be unique"))
    if ids.count(bundle.get("active_revision_id")) != 1 or (revisions and bundle.get("active_revision_id") != ids[-1]):
        diagnostics.append(_diag(kind, stable_id, "/active_revision_id", "active revision must match the final revision exactly once"))
    if [item.get("revision_number") for item in revisions] != list(range(1, len(revisions) + 1)):
        diagnostics.append(_diag(kind, stable_id, "/revisions", "revisions must be contiguous and ordered from one"))
    for index, revision in enumerate(revisions):
        predecessor = revision.get("predecessor")
        if index == 0 and predecessor is not None:
            diagnostics.append(_diag(kind, stable_id, f"/revisions/{index}/predecessor", "first revision must not have a predecessor"))
        elif index:
            previous = revisions[index - 1]
            if predecessor != {"revision_id": previous.get("revision_id"), "revision_digest": canonical_digest(previous)}:
                diagnostics.append(_diag(kind, stable_id, f"/revisions/{index}/predecessor", "revision predecessor ID or digest is invalid"))
        if revision.get("content_digest") != canonical_digest(revision.get(child_field)):
            diagnostics.append(_diag(kind, stable_id, f"/revisions/{index}/content_digest", "revision content digest is invalid"))
    return diagnostics


def _diag(kind: str, record_id: object, path: str, message: str, code: str = INCOMPLETE_TRANSACTION) -> Diagnostic:
    return Diagnostic(code, kind, record_id if isinstance(record_id, str) else None, path, message)


__all__ = [
    "active_criteria_for_question",
    "active_screening_criteria",
    "active_screening_decision",
    "current_included_decision",
    "decision_freshness",
    "require_screening_eligible_links",
    "screening_criteria_bundle_diagnostics",
    "screening_decision_bundle_diagnostics",
    "screening_entries_diagnostics",
]
