from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from research_kb.catalog.models import canonical_digest
from research_kb.errors import DUPLICATE_ID, GROUNDING_MISMATCH, INCOMPLETE_TRANSACTION, Diagnostic
from research_kb.primary_bundles import active_primary_entries
from research_kb.review_bundles import active_review_entries


BundleEntry = tuple[str, dict[str, Any]]

ORGANIZATION_BUNDLE_SPECS = {
    "direction-bundle": ("direction_id", "direction", "direction"),
    "field-map-bundle": ("field_map_entry_id", "field_map_entry", "field-map-entry"),
    "question-revision-bundle": ("question_id", "question_mapping", "question-mapping"),
}


def active_organization_revision(bundle: Mapping[str, Any]) -> dict[str, Any] | None:
    active_id = bundle.get("active_revision_id")
    return next(
        (
            dict(revision)
            for revision in bundle.get("revisions", [])
            if revision.get("revision_id") == active_id
        ),
        None,
    )


def active_organization_record(
    bundle: Mapping[str, Any],
    *,
    child_field: str,
) -> dict[str, Any] | None:
    revision = active_organization_revision(bundle)
    if revision is None or not isinstance(revision.get(child_field), Mapping):
        return None
    return dict(revision[child_field])


def expand_active_organization_entries(
    entries: Iterable[BundleEntry],
) -> list[BundleEntry]:
    materialized = list(entries)
    question_successors = {
        record.get("question_id")
        for kind, record in materialized
        if kind == "question-revision-bundle"
    }
    result = [
        (kind, record)
        for kind, record in materialized
        if not (kind == "question-mapping" and record.get("question_id") in question_successors)
    ]
    for kind, bundle in materialized:
        spec = ORGANIZATION_BUNDLE_SPECS.get(kind)
        if spec is None:
            continue
        _, child_field, child_kind = spec
        child = active_organization_record(bundle, child_field=child_field)
        if child is not None:
            result.append((child_kind, child))
    return result


def organization_entries_diagnostics(entries: Iterable[BundleEntry]) -> list[Diagnostic]:
    materialized = list(entries)
    diagnostics: list[Diagnostic] = []
    owners: dict[tuple[str, str], int] = {}
    stable_links: dict[str, tuple[str, str, str, str, str]] = {}
    stable_backgrounds: dict[str, tuple[str, str, str]] = {}
    for kind, bundle in materialized:
        spec = ORGANIZATION_BUNDLE_SPECS.get(kind)
        if spec is None:
            continue
        target_field, child_field, _ = spec
        target_id = bundle.get(target_field)
        diagnostics.extend(
            organization_bundle_diagnostics(
                bundle,
                bundle_kind=kind,
                target_id_field=target_field,
                child_field=child_field,
            )
        )
        if isinstance(target_id, str):
            owners[(kind, target_id)] = owners.get((kind, target_id), 0) + 1
        for revision in bundle.get("revisions", []):
            child = revision.get(child_field, {})
            links = list(child.get("links", []))
            backgrounds = revision.get("background_links", [])
            links.extend(item.get("link", {}) for item in backgrounds)
            for link in links:
                link_id = link.get("organization_link_id")
                identity = (
                    kind,
                    str(target_id),
                    str(link.get("source_kind")),
                    str(link.get("source_unit_id")),
                    str(link.get("role")),
                )
                if isinstance(link_id, str) and link_id in stable_links and stable_links[link_id] != identity:
                    diagnostics.append(
                        Diagnostic(DUPLICATE_ID, kind, link_id, "", "organization link ID is reused for a different target or source identity")
                    )
                elif isinstance(link_id, str):
                    stable_links[link_id] = identity
            for item in backgrounds:
                background_id = item.get("question_background_id")
                identity = (str(target_id), str(item.get("link", {}).get("source_unit_id")), str(item.get("link", {}).get("role")))
                if isinstance(background_id, str) and background_id in stable_backgrounds and stable_backgrounds[background_id] != identity:
                    diagnostics.append(
                        Diagnostic(DUPLICATE_ID, kind, background_id, "", "Question background ID is reused for a different source identity")
                    )
                elif isinstance(background_id, str):
                    stable_backgrounds[background_id] = identity
    for (kind, target_id), count in owners.items():
        if count > 1:
            diagnostics.append(
                Diagnostic(
                    DUPLICATE_ID,
                    kind,
                    target_id,
                    "",
                    "multiple organization bundles own the same target",
                )
            )
    legacy_questions = {
        record["question_id"]: record
        for kind, record in materialized
        if kind == "question-mapping"
    }
    for kind, bundle in materialized:
        if kind != "question-revision-bundle" or not bundle.get("revisions"):
            continue
        question_id = bundle.get("question_id")
        predecessor = bundle["revisions"][0].get("predecessor")
        legacy = legacy_questions.get(question_id)
        expected = None if legacy is None else {
            "basis_kind": "legacy_question_mapping",
            "basis_id": question_id,
            "basis_digest": canonical_digest(legacy),
        }
        if predecessor != expected:
            diagnostics.append(
                _bundle_diagnostic(
                    kind,
                    question_id,
                    "/revisions/0/predecessor",
                    "first Question revision does not close over the exact legacy base",
                )
            )
    return diagnostics


def organization_bundle_diagnostics(
    bundle: Mapping[str, Any],
    *,
    bundle_kind: str,
    target_id_field: str,
    child_field: str,
) -> list[Diagnostic]:
    target_id = bundle.get(target_id_field)
    revisions = list(bundle.get("revisions", []))
    diagnostics: list[Diagnostic] = []
    if [item.get("revision_number") for item in revisions] != list(range(1, len(revisions) + 1)):
        return [_bundle_diagnostic(bundle_kind, target_id, "/revisions", "revisions must be contiguous and ordered from one")]
    if revisions and bundle.get("active_revision_id") != revisions[-1].get("revision_id"):
        diagnostics.append(
            _bundle_diagnostic(bundle_kind, target_id, "/active_revision_id", "active revision must be the final revision")
        )

    seen_revision_ids: set[str] = set()
    for index, revision in enumerate(revisions):
        base = f"/revisions/{index}"
        revision_id = revision.get("revision_id")
        if isinstance(revision_id, str):
            if revision_id in seen_revision_ids:
                diagnostics.append(_bundle_diagnostic(bundle_kind, target_id, base + "/revision_id", "duplicate revision ID"))
            seen_revision_ids.add(revision_id)
        predecessor = revision.get("predecessor")
        if index == 0:
            if predecessor is not None and not _legacy_question_predecessor(predecessor, target_id):
                diagnostics.append(_bundle_diagnostic(bundle_kind, target_id, base + "/predecessor", "first revision has an invalid predecessor"))
        else:
            previous = revisions[index - 1]
            expected = (
                {
                    "basis_kind": "question_revision",
                    "basis_id": previous.get("revision_id"),
                    "basis_digest": canonical_digest(previous),
                }
                if bundle_kind == "question-revision-bundle"
                else {
                    "revision_id": previous.get("revision_id"),
                    "revision_digest": canonical_digest(previous),
                }
            )
            if predecessor != expected:
                diagnostics.append(_bundle_diagnostic(bundle_kind, target_id, base + "/predecessor", "revision predecessor ID or digest is invalid"))

        child = revision.get(child_field)
        if not isinstance(child, Mapping):
            continue
        if child.get(target_id_field) != target_id:
            diagnostics.append(_bundle_diagnostic(bundle_kind, target_id, base + f"/{child_field}/{target_id_field}", "revision child belongs to another target"))
        if revision.get("content_digest") != _revision_content_digest(revision, child_field):
            diagnostics.append(_bundle_diagnostic(bundle_kind, target_id, base + "/content_digest", "revision content digest is invalid"))
        links = list(child.get("links", []))
        links.extend(
            item.get("link")
            for item in revision.get("background_links", [])
            if isinstance(item, Mapping)
        )
        seen_link_ids: set[str] = set()
        for link_index, link in enumerate(links):
            if not isinstance(link, Mapping):
                continue
            link_id = link.get("organization_link_id")
            if isinstance(link_id, str):
                if link_id in seen_link_ids:
                    diagnostics.append(_bundle_diagnostic(bundle_kind, target_id, f"{base}/links/{link_index}/link_id", "organization link ID is reused across revisions"))
                seen_link_ids.add(link_id)
    return diagnostics


def organization_link_freshness(
    link: Mapping[str, Any],
    entries: Iterable[BundleEntry],
) -> dict[str, Any]:
    materialized = list(entries)
    source_kind = link.get("source_kind")
    reasons: list[str] = []
    if source_kind == "primary_unit":
        _primary_link_reasons(link, materialized, reasons)
    elif source_kind == "review_unit":
        _review_link_reasons(link, materialized, reasons)
    else:
        reasons.append("source_unit_unavailable")
    return {
        "status": "current" if not reasons else "stale_upstream",
        "reasons": sorted(set(reasons)),
    }


def project_links_with_freshness(
    links: Iterable[Mapping[str, Any]],
    entries: Iterable[BundleEntry],
) -> list[dict[str, Any]]:
    materialized = list(entries)
    return [
        {**dict(link), "freshness": organization_link_freshness(link, materialized)}
        for link in links
    ]


def _primary_link_reasons(
    link: Mapping[str, Any],
    entries: list[BundleEntry],
    reasons: list[str],
) -> None:
    cards = {item["paper_id"]: item for item in _records_of_kind(entries, "paper-card")}
    evidence = {item["evidence_id"]: item for item in _records_of_kind(entries, "evidence")}
    paper_id = link.get("paper_id")
    card = cards.get(paper_id)
    units = {
        unit.get("unit_id"): unit
        for section in (card or {}).get("sections", [])
        for unit in section.get("units", [])
    }
    unit = units.get(link.get("source_unit_id"))
    if unit is None:
        reasons.append("source_unit_superseded")
        return
    expected_revision = _active_source_revision(entries, "primary-semantic-bundle", paper_id)
    if link.get("source_revision_id") != expected_revision:
        reasons.append("source_unit_superseded")
    role = link.get("role")
    if role == "factual_example":
        if unit.get("grounding_status") not in {"grounded", "revised"}:
            reasons.append("source_unit_inadmissible")
        expected_evidence = sorted(unit.get("evidence_ids", []))
        if sorted(link.get("evidence_ids", [])) != expected_evidence:
            reasons.append("evidence_closure_changed")
        for evidence_id in expected_evidence:
            item = evidence.get(evidence_id)
            if (
                item is None
                or item.get("paper_id") != paper_id
                or item.get("canonical") is not True
            ):
                reasons.append("evidence_missing_or_inadmissible")
                break
    elif role in {"background_context", "question_background"}:
        if unit.get("grounding_status") not in {"interpretive", "background_only"}:
            reasons.append("source_unit_inadmissible")
        if link.get("evidence_ids"):
            reasons.append("context_link_has_evidence")
    else:
        reasons.append("source_unit_inadmissible")


def _review_link_reasons(
    link: Mapping[str, Any],
    entries: list[BundleEntry],
    reasons: list[str],
) -> None:
    memories = {item["paper_id"]: item for item in _records_of_kind(entries, "review-memory")}
    paper_id = link.get("paper_id")
    memory = memories.get(paper_id)
    units = {
        unit.get("review_unit_id"): unit
        for section in (memory or {}).get("sections", [])
        for unit in section.get("units", [])
    }
    unit = units.get(link.get("source_unit_id"))
    expected_revision = _active_source_revision(entries, "review-semantic-bundle", paper_id)
    if (
        memory is None
        or unit is None
        or link.get("review_memory_id") != memory.get("review_memory_id")
        or link.get("source_revision_id") != expected_revision
    ):
        reasons.append("review_source_revision_superseded")
        return
    if (
        unit.get("background_only") is not True
        or unit.get("can_enter_canonical_evidence") is not False
        or unit.get("not_fact") is not True
        or not unit.get("source_notes")
        or link.get("role") not in {"background_context", "question_background"}
        or bool(link.get("evidence_ids"))
    ):
        reasons.append("review_provenance_or_boundary_invalid")


def _active_source_revision(entries: list[BundleEntry], kind: str, paper_id: object) -> str | None:
    owners = [record for entry_kind, record in entries if entry_kind == kind and record.get("paper_id") == paper_id]
    if len(owners) != 1:
        return None
    active = owners[0].get("active_revision_id")
    return active if isinstance(active, str) else None


def _records_of_kind(entries: list[BundleEntry], kind: str) -> list[dict[str, Any]]:
    records = [record for entry_kind, record in entries if entry_kind == kind]
    if kind in {"paper-card", "evidence", "review-queue"}:
        records.extend(
            child
            for entry_kind, bundle in entries
            if entry_kind == "primary-semantic-bundle"
            for child_kind, child in active_primary_entries(bundle)
            if child_kind == kind
        )
    if kind == "review-memory":
        records.extend(
            child
            for entry_kind, bundle in entries
            if entry_kind == "review-semantic-bundle"
            for child_kind, child in active_review_entries(bundle)
            if child_kind == kind
        )
    return records


def _revision_content_digest(revision: Mapping[str, Any], child_field: str) -> str:
    if child_field == "question_mapping":
        mapping = {
            key: value
            for key, value in revision.get("question_mapping", {}).items()
            if key not in {"created_at", "updated_at", "fixture_origin"}
        }
        return canonical_digest(
            {
                "question_mapping": mapping,
                "background_links": revision.get("background_links", []),
            }
        )
    return canonical_digest(revision.get(child_field))


def _legacy_question_predecessor(value: object, target_id: object) -> bool:
    return (
        isinstance(value, Mapping)
        and value.get("basis_kind") == "legacy_question_mapping"
        and value.get("basis_id") == target_id
        and isinstance(value.get("basis_digest"), str)
        and len(value["basis_digest"]) == 64
    )


def _bundle_diagnostic(
    bundle_kind: str,
    target_id: object,
    path: str,
    message: str,
) -> Diagnostic:
    code = INCOMPLETE_TRANSACTION if "revision" in message.lower() else GROUNDING_MISMATCH
    return Diagnostic(
        code,
        bundle_kind,
        target_id if isinstance(target_id, str) else None,
        path,
        message,
    )


__all__ = [
    "ORGANIZATION_BUNDLE_SPECS",
    "active_organization_record",
    "active_organization_revision",
    "expand_active_organization_entries",
    "organization_bundle_diagnostics",
    "organization_entries_diagnostics",
    "organization_link_freshness",
    "project_links_with_freshness",
]
