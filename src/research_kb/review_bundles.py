from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from research_kb.catalog.models import canonical_digest
from research_kb.errors import DUPLICATE_REVIEW_MEMORY, GROUNDING_MISMATCH, INCOMPLETE_TRANSACTION, Diagnostic


def active_review_entries(bundle: Mapping[str, Any]) -> tuple[tuple[str, dict[str, Any]], ...]:
    active_id = bundle.get("active_revision_id")
    revision = next(
        (item for item in bundle.get("revisions", []) if item.get("revision_id") == active_id),
        None,
    )
    return () if revision is None else (("review-memory", revision["review_memory"]),)


def expand_active_review_entries(
    entries: Iterable[tuple[str, dict[str, Any]]],
) -> list[tuple[str, dict[str, Any]]]:
    result = list(entries)
    existing = {
        record.get("review_memory_id")
        for kind, record in result
        if kind == "review-memory"
    }
    for kind, record in tuple(result):
        if kind != "review-semantic-bundle":
            continue
        for child_kind, child in active_review_entries(record):
            if child.get("review_memory_id") not in existing:
                result.append((child_kind, child))
                existing.add(child.get("review_memory_id"))
    return result


def review_bundle_diagnostics(bundle: Mapping[str, Any]) -> list[Diagnostic]:
    paper_id = bundle.get("paper_id")
    revisions = list(bundle.get("revisions", []))
    diagnostics: list[Diagnostic] = []
    if [item.get("revision_number") for item in revisions] != list(range(1, len(revisions) + 1)):
        return [_diagnostic(paper_id, "/revisions", "Review revisions must be contiguous and ordered from one")]
    if revisions and bundle.get("active_revision_id") != revisions[-1].get("revision_id"):
        diagnostics.append(_diagnostic(paper_id, "/active_revision_id", "active Review revision must be the final revision"))
    seen_revision_ids: set[str] = set()
    seen_child_ids: set[str] = set()
    for index, revision in enumerate(revisions):
        revision_id = revision.get("revision_id")
        if isinstance(revision_id, str):
            if revision_id in seen_revision_ids:
                diagnostics.append(_diagnostic(paper_id, f"/revisions/{index}/revision_id", "duplicate Review revision ID"))
            seen_revision_ids.add(revision_id)
        predecessor = revision.get("predecessor")
        if index == 0:
            if predecessor is not None:
                diagnostics.append(_diagnostic(paper_id, f"/revisions/{index}/predecessor", "first Review revision cannot have a predecessor"))
        else:
            previous = revisions[index - 1]
            expected = {
                "revision_id": previous.get("revision_id"),
                "revision_digest": canonical_digest(previous),
            }
            if predecessor != expected:
                diagnostics.append(_diagnostic(paper_id, f"/revisions/{index}/predecessor", "Review revision predecessor ID or digest is invalid"))
        memory = revision.get("review_memory")
        if not isinstance(memory, Mapping):
            continue
        if memory.get("paper_id") != paper_id:
            diagnostics.append(_diagnostic(paper_id, f"/revisions/{index}/review_memory/paper_id", "Review revision child belongs to another paper"))
        child_ids = [memory.get("review_memory_id")]
        note_keys: set[tuple[str, int]] = set()
        for section in memory.get("sections", []):
            for unit in section.get("units", []):
                unit_id = unit.get("review_unit_id")
                child_ids.append(unit_id)
                if isinstance(unit_id, str):
                    note_keys.update((unit_id, note_index) for note_index, _ in enumerate(unit.get("source_notes", [])))
        for child_id in child_ids:
            if not isinstance(child_id, str):
                continue
            if child_id in seen_child_ids:
                diagnostics.append(_diagnostic(paper_id, f"/revisions/{index}", "Review Memory or Unit ID is reused across revisions"))
            seen_child_ids.add(child_id)
        bindings = revision.get("provenance_bindings", [])
        binding_keys = {
            (item.get("review_unit_id"), item.get("source_note_index"))
            for item in bindings
            if isinstance(item, Mapping)
        }
        if binding_keys != note_keys or len(binding_keys) != len(bindings):
            diagnostics.append(_diagnostic(paper_id, f"/revisions/{index}/provenance_bindings", "Review provenance bindings must close exactly over every retained source note"))
    return diagnostics


def mixed_review_authority_diagnostics(
    entries: Iterable[tuple[str, dict[str, Any]]],
) -> list[Diagnostic]:
    materialized = list(entries)
    bundled_papers = {
        record.get("paper_id")
        for kind, record in materialized
        if kind == "review-semantic-bundle"
    }
    return [
        Diagnostic(
            DUPLICATE_REVIEW_MEMORY,
            "review-memory",
            str(record.get("review_memory_id")),
            "/paper_id",
            "legacy Review Memory cannot coexist with a P4-C Review bundle",
        )
        for kind, record in materialized
        if kind == "review-memory" and record.get("paper_id") in bundled_papers
    ]


def _diagnostic(paper_id: object, path: str, message: str) -> Diagnostic:
    return Diagnostic(
        INCOMPLETE_TRANSACTION if "revision" in message.lower() else GROUNDING_MISMATCH,
        "review-semantic-bundle",
        paper_id if isinstance(paper_id, str) else None,
        path,
        message,
    )


__all__ = [
    "active_review_entries",
    "expand_active_review_entries",
    "mixed_review_authority_diagnostics",
    "review_bundle_diagnostics",
]
