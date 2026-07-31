from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from research_kb.catalog.models import canonical_digest
from research_kb.errors import DUPLICATE_PAPER_CARD, GROUNDING_MISMATCH, INCOMPLETE_TRANSACTION, Diagnostic


CHILD_KINDS = ("paper-card", "evidence", "review-queue")


def active_primary_entries(bundle: Mapping[str, Any]) -> tuple[tuple[str, dict[str, Any]], ...]:
    active_id = bundle.get("active_revision_id")
    revision = next(
        (item for item in bundle.get("revisions", []) if item.get("revision_id") == active_id),
        None,
    )
    if revision is None:
        return ()
    return (
        ("paper-card", revision["paper_card"]),
        *(("evidence", item) for item in revision["evidence"]),
        *(("review-queue", item) for item in revision["review_queue"]),
    )


def expand_active_primary_entries(
    entries: Iterable[tuple[str, dict[str, Any]]],
) -> list[tuple[str, dict[str, Any]]]:
    result = list(entries)
    existing = {_entry_identity(kind, record) for kind, record in result if kind in CHILD_KINDS}
    for kind, record in tuple(result):
        if kind != "primary-semantic-bundle":
            continue
        for child_kind, child in active_primary_entries(record):
            identity = _entry_identity(child_kind, child)
            if identity not in existing:
                result.append((child_kind, child))
                existing.add(identity)
    return result


def primary_bundle_diagnostics(bundle: Mapping[str, Any]) -> list[Diagnostic]:
    paper_id = bundle.get("paper_id")
    revisions = list(bundle.get("revisions", []))
    diagnostics: list[Diagnostic] = []
    expected_numbers = list(range(1, len(revisions) + 1))
    if [item.get("revision_number") for item in revisions] != expected_numbers:
        diagnostics.append(_diagnostic(paper_id, "/revisions", "Primary revisions must be contiguous and ordered from one"))
        return diagnostics
    if revisions and bundle.get("active_revision_id") != revisions[-1].get("revision_id"):
        diagnostics.append(_diagnostic(paper_id, "/active_revision_id", "active revision must be the final revision"))
    seen_revision_ids: set[str] = set()
    seen_child_ids: set[str] = set()
    for index, revision in enumerate(revisions):
        revision_id = revision.get("revision_id")
        if revision_id in seen_revision_ids:
            diagnostics.append(_diagnostic(paper_id, f"/revisions/{index}/revision_id", "duplicate Primary revision ID"))
        if isinstance(revision_id, str):
            seen_revision_ids.add(revision_id)
        predecessor = revision.get("predecessor")
        if index == 0:
            if predecessor is not None:
                diagnostics.append(_diagnostic(paper_id, f"/revisions/{index}/predecessor", "first Primary revision cannot have a predecessor"))
        else:
            previous = revisions[index - 1]
            expected = {
                "revision_id": previous.get("revision_id"),
                "revision_digest": canonical_digest(previous),
            }
            if predecessor != expected:
                diagnostics.append(_diagnostic(paper_id, f"/revisions/{index}/predecessor", "Primary revision predecessor ID or digest is invalid"))
        children = (
            revision.get("paper_card"),
            *revision.get("evidence", []),
            *revision.get("review_queue", []),
        )
        for child in children:
            if not isinstance(child, Mapping):
                continue
            if child.get("paper_id") != paper_id:
                diagnostics.append(_diagnostic(paper_id, f"/revisions/{index}", "Primary revision child belongs to another paper"))
            child_id = child.get("evidence_id") or child.get("queue_id")
            if isinstance(child_id, str):
                if child_id in seen_child_ids:
                    diagnostics.append(_diagnostic(paper_id, f"/revisions/{index}", "canonical child ID is reused across Primary revisions"))
                seen_child_ids.add(child_id)
        card = revision.get("paper_card")
        if isinstance(card, Mapping):
            for section in card.get("sections", []):
                for unit in section.get("units", []):
                    unit_id = unit.get("unit_id")
                    if isinstance(unit_id, str):
                        if unit_id in seen_child_ids:
                            diagnostics.append(_diagnostic(paper_id, f"/revisions/{index}", "Card Unit ID is reused across Primary revisions"))
                        seen_child_ids.add(unit_id)
    return diagnostics


def mixed_primary_authority_diagnostics(
    entries: Iterable[tuple[str, dict[str, Any]]],
) -> list[Diagnostic]:
    materialized = list(entries)
    bundled_papers = {
        record.get("paper_id")
        for kind, record in materialized
        if kind == "primary-semantic-bundle"
    }
    diagnostics: list[Diagnostic] = []
    for kind, record in materialized:
        if kind not in CHILD_KINDS or record.get("paper_id") not in bundled_papers:
            continue
        diagnostics.append(
            Diagnostic(
                DUPLICATE_PAPER_CARD,
                kind,
                str(record.get("paper_id")),
                "/paper_id",
                "legacy Primary records cannot coexist with a P4-B Primary bundle",
            )
        )
    return diagnostics


def _entry_identity(kind: str, record: Mapping[str, Any]) -> tuple[str, str]:
    field = {"paper-card": "paper_id", "evidence": "evidence_id", "review-queue": "queue_id"}[kind]
    return kind, str(record.get(field, ""))


def _diagnostic(paper_id: object, path: str, message: str) -> Diagnostic:
    return Diagnostic(
        INCOMPLETE_TRANSACTION if "revision" in message.lower() else GROUNDING_MISMATCH,
        "primary-semantic-bundle",
        paper_id if isinstance(paper_id, str) else None,
        path,
        message,
    )


__all__ = [
    "CHILD_KINDS",
    "active_primary_entries",
    "expand_active_primary_entries",
    "mixed_primary_authority_diagnostics",
    "primary_bundle_diagnostics",
]
