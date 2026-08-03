from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping
from typing import Any

from research_kb.catalog.models import canonical_digest
from research_kb.errors import DUPLICATE_ID, GROUNDING_MISMATCH, INCOMPLETE_TRANSACTION, UNRESOLVED_REFERENCE, Diagnostic
from research_kb.identity_corrections import project_registry_identity


BundleEntry = tuple[str, dict[str, Any]]
_SPACE = re.compile(r"\s+")


def clean_tag_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return _SPACE.sub(" ", unicodedata.normalize("NFKC", value).strip())


def normalize_tag_name(value: object) -> str:
    return clean_tag_text(value).casefold()


def active_tag(bundle: Mapping[str, Any]) -> dict[str, Any] | None:
    active_id = bundle.get("active_revision_id")
    matches = [item for item in bundle.get("revisions", []) if item.get("revision_id") == active_id]
    return None if len(matches) != 1 else dict(matches[0].get("tag", {}))


def active_tag_link_state(bundle: Mapping[str, Any]) -> str | None:
    active_id = bundle.get("active_revision_id")
    matches = [item for item in bundle.get("revisions", []) if item.get("revision_id") == active_id]
    return None if len(matches) != 1 else matches[0].get("state")


def tag_link_content(bundle: Mapping[str, Any], state: object) -> dict[str, Any]:
    return {
        "tag_id": bundle.get("tag_id"),
        "target_kind": bundle.get("target_kind"),
        "target_id": bundle.get("target_id"),
        "state": state,
    }


def tag_bundle_diagnostics(bundle: Mapping[str, Any]) -> list[Diagnostic]:
    return _revision_diagnostics(
        bundle,
        kind="tag-bundle",
        stable_id_field="tag_id",
        revision_child="tag",
    )


def tag_link_bundle_diagnostics(bundle: Mapping[str, Any]) -> list[Diagnostic]:
    return _revision_diagnostics(
        bundle,
        kind="tag-link-bundle",
        stable_id_field="tag_link_id",
        revision_child="state",
    )


def tag_entries_diagnostics(entries: Iterable[BundleEntry]) -> list[Diagnostic]:
    materialized = list(entries)
    diagnostics: list[Diagnostic] = []
    vocabulary: dict[str, str] = {}
    assignments: dict[tuple[str, str, str], str] = {}
    tag_ids = {str(record.get("tag_id")) for kind, record in materialized if kind == "tag-bundle"}
    papers = [record for kind, record in materialized if kind == "registry-paper"]
    corrections = [record for kind, record in materialized if kind == "registry-identity-correction"]
    paper_projection = project_registry_identity(papers, corrections)
    target_ids = {
        "paper": {
            paper_id
            for paper_id, item in paper_projection.items()
            if item.get("canonical_paper_id") == paper_id and item.get("library_status") == "active"
        },
        "direction": {str(record.get("direction_id")) for kind, record in materialized if kind == "direction-bundle"},
        "field_map_entry": {str(record.get("field_map_entry_id")) for kind, record in materialized if kind == "field-map-bundle"},
        "question": {str(record.get("question_id")) for kind, record in materialized if kind in {"question-mapping", "question-revision-bundle"}},
    }
    for kind, bundle in materialized:
        if kind == "tag-bundle":
            diagnostics.extend(tag_bundle_diagnostics(bundle))
            tag = active_tag(bundle)
            if tag is None:
                continue
            for key in [tag.get("normalized_name"), *map(normalize_tag_name, tag.get("aliases", []))]:
                if not isinstance(key, str) or not key:
                    continue
                owner = vocabulary.get(key)
                if owner is not None and owner != bundle.get("tag_id"):
                    diagnostics.append(
                        Diagnostic(DUPLICATE_ID, kind, bundle.get("tag_id"), "/revisions", "normalized Tag name or alias belongs to another Tag")
                    )
                vocabulary[key] = str(bundle.get("tag_id"))
        elif kind == "tag-link-bundle":
            diagnostics.extend(tag_link_bundle_diagnostics(bundle))
            identity = (
                str(bundle.get("tag_id")),
                str(bundle.get("target_kind")),
                str(bundle.get("target_id")),
            )
            owner = assignments.get(identity)
            if owner is not None and owner != bundle.get("tag_link_id"):
                diagnostics.append(
                    Diagnostic(DUPLICATE_ID, kind, bundle.get("tag_link_id"), "", "multiple Tag links own the same Tag and target identity")
                )
            assignments[identity] = str(bundle.get("tag_link_id"))
            if str(bundle.get("tag_id")) not in tag_ids:
                diagnostics.append(Diagnostic(UNRESOLVED_REFERENCE, kind, bundle.get("tag_link_id"), "/tag_id", "Tag link references an unavailable Tag"))
            target_kind = str(bundle.get("target_kind"))
            if str(bundle.get("target_id")) not in target_ids.get(target_kind, set()):
                diagnostics.append(Diagnostic(UNRESOLVED_REFERENCE, kind, bundle.get("tag_link_id"), "/target_id", "Tag link target is unavailable"))
    return diagnostics


def _revision_diagnostics(
    bundle: Mapping[str, Any],
    *,
    kind: str,
    stable_id_field: str,
    revision_child: str,
) -> list[Diagnostic]:
    stable_id = bundle.get(stable_id_field)
    revisions = list(bundle.get("revisions", []))
    diagnostics: list[Diagnostic] = []
    revision_ids = [item.get("revision_id") for item in revisions]
    if len(revision_ids) != len(set(revision_ids)):
        diagnostics.append(_diagnostic(kind, stable_id, "/revisions", "revision IDs must be unique within the bundle"))
    if revision_ids.count(bundle.get("active_revision_id")) != 1:
        diagnostics.append(_diagnostic(kind, stable_id, "/active_revision_id", "active revision must match exactly once"))
    if [item.get("revision_number") for item in revisions] != list(range(1, len(revisions) + 1)):
        diagnostics.append(_diagnostic(kind, stable_id, "/revisions", "revisions must be contiguous and ordered from one"))
    if revisions and bundle.get("active_revision_id") != revisions[-1].get("revision_id"):
        diagnostics.append(_diagnostic(kind, stable_id, "/active_revision_id", "active revision must be the final revision"))
    for index, revision in enumerate(revisions):
        base = f"/revisions/{index}"
        predecessor = revision.get("predecessor")
        if index == 0 and predecessor is not None:
            diagnostics.append(_diagnostic(kind, stable_id, base + "/predecessor", "first revision must not have a predecessor"))
        elif index:
            previous = revisions[index - 1]
            expected = {
                "revision_id": previous.get("revision_id"),
                "revision_digest": canonical_digest(previous),
            }
            if predecessor != expected:
                diagnostics.append(_diagnostic(kind, stable_id, base + "/predecessor", "revision predecessor ID or digest is invalid"))
        content = revision.get(revision_child)
        digest_content = tag_link_content(bundle, content) if kind == "tag-link-bundle" else content
        if revision.get("content_digest") != canonical_digest(digest_content):
            diagnostics.append(_diagnostic(kind, stable_id, base + "/content_digest", "revision content digest is invalid"))
        if kind == "tag-bundle" and isinstance(content, Mapping):
            if content.get("tag_id") != stable_id:
                diagnostics.append(_diagnostic(kind, stable_id, base + "/tag/tag_id", "Tag revision belongs to another Tag"))
            if content.get("normalized_name") != normalize_tag_name(content.get("name")):
                diagnostics.append(_diagnostic(kind, stable_id, base + "/tag/normalized_name", "normalized Tag name is invalid"))
            aliases = list(content.get("aliases", []))
            normalized = [normalize_tag_name(item) for item in aliases]
            if len(normalized) != len(set(normalized)) or content.get("normalized_name") in normalized:
                diagnostics.append(_diagnostic(kind, stable_id, base + "/tag/aliases", "Tag aliases are not unique after normalization"))
    return diagnostics


def _diagnostic(kind: str, record_id: object, path: str, message: str) -> Diagnostic:
    code = INCOMPLETE_TRANSACTION if "revision" in message.lower() else GROUNDING_MISMATCH
    return Diagnostic(code, kind, record_id if isinstance(record_id, str) else None, path, message)


__all__ = [
    "active_tag",
    "active_tag_link_state",
    "clean_tag_text",
    "normalize_tag_name",
    "tag_bundle_diagnostics",
    "tag_entries_diagnostics",
    "tag_link_content",
    "tag_link_bundle_diagnostics",
]
