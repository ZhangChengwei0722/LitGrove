from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from research_kb.bundle import BundleEntry, records_of_kind
from research_kb.catalog.models import canonical_digest
from research_kb.primary_bundles import active_primary_entries
from research_kb.review_bundles import active_review_entries
from research_kb.step7_support import STEP7_RECORD_KINDS, candidate_freshness
from research_kb.tag_bundles import active_tag, active_tag_link_state


MANIFEST_CONTRACT = "obsidian-generated-view-manifest@1.0"
RENDERER_VERSION = "1.0"
OPTIONAL_TABLES = ("library_summary", "question_coverage")


@dataclass(frozen=True, slots=True)
class ViewDependency:
    record_kind: str
    record_id: str
    record_digest: str
    revision_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_kind": self.record_kind,
            "record_id": self.record_id,
            "record_digest": self.record_digest,
            "revision_id": self.revision_id,
        }


@dataclass(frozen=True, slots=True)
class ViewDraft:
    logical_path: str
    view_kind: str
    view_id: str
    dependencies: tuple[ViewDependency, ...]
    body: str
    render_options: tuple[str, ...] = ()
    display_title: str | None = None

    @property
    def source_watermark(self) -> str:
        return canonical_digest(
            {
                "renderer_version": RENDERER_VERSION,
                "dependencies": [item.to_dict() for item in self.dependencies],
                "render_options": list(self.render_options),
            }
        )

    def render(self, rendered_at: str) -> bytes:
        frontmatter = [
            "---",
            "managed_generated_view: true",
            "canonical: false",
            "editable_source: false",
            f"view_kind: {_yaml(self.view_kind)}",
            f"view_id: {_yaml(self.view_id)}",
            f"render_version: {_yaml(RENDERER_VERSION)}",
            f"source_watermark: {_yaml(self.source_watermark)}",
            f"rendered_at: {_yaml(rendered_at)}",
            "---",
            "",
        ]
        return ("\n".join(frontmatter) + self.body.rstrip() + "\n").encode("utf-8")


def project_obsidian_views(
    entries: list[BundleEntry],
    *,
    optional_tables: Iterable[str] = (),
) -> tuple[ViewDraft, ...]:
    selected_tables = _normalize_tables(optional_tables)
    context = _ProjectionContext(entries)
    drafts: list[ViewDraft] = []

    paper_drafts = [_paper_view(context, paper_id) for paper_id in sorted(context.primary_paper_ids)]
    review_drafts = [_review_view(context, paper_id) for paper_id in sorted(context.review_paper_ids)]
    direction_drafts = [_direction_view(context, direction_id) for direction_id in sorted(context.directions)]
    question_drafts = [_question_view(context, question_id) for question_id in sorted(context.questions)]
    synthesis_question_ids = sorted(
        question_id
        for question_id in context.questions
        if any(item["question_id"] == question_id for item in context.candidates)
    )
    synthesis_drafts = [_synthesis_view(context, question_id) for question_id in synthesis_question_ids]

    drafts.extend(
        [
            _index_view(context, "papers_index", "Papers/_index.md", "Papers", paper_drafts),
            *paper_drafts,
            _index_view(context, "reviews_index", "Reviews/_index.md", "Reviews", review_drafts),
            *review_drafts,
            _index_view(context, "directions_index", "Directions/_index.md", "Directions", direction_drafts),
            *direction_drafts,
            _index_view(context, "questions_index", "Questions/_index.md", "Questions", question_drafts),
            *question_drafts,
            _index_view(
                context,
                "research_synthesis_index",
                "Research Synthesis/_index.md",
                "Research Synthesis",
                synthesis_drafts,
            ),
            *synthesis_drafts,
        ]
    )
    if "library_summary" in selected_tables:
        drafts.append(_library_summary(context))
    if "question_coverage" in selected_tables:
        drafts.append(_question_coverage(context))
    drafts.append(_home_view(context, drafts, selected_tables))
    return tuple(sorted(drafts, key=lambda item: item.logical_path))


class _ProjectionContext:
    def __init__(self, entries: list[BundleEntry]):
        self.entries = entries
        self.profile = records_of_kind(entries, "domain-profile")[0]
        self.profile_id = self.profile["domain_profile"]["id"]
        self.papers = {item["paper_id"]: item for item in records_of_kind(entries, "registry-paper")}
        self.cards = {item["paper_id"]: item for item in records_of_kind(entries, "paper-card")}
        self.evidence = records_of_kind(entries, "evidence")
        self.evidence_by_paper = _group(self.evidence, "paper_id")
        self.reviews = {item["paper_id"]: item for item in records_of_kind(entries, "review-memory")}
        self.directions = {item["direction_id"]: item for item in records_of_kind(entries, "direction")}
        self.fields = {
            item["field_map_entry_id"]: item for item in records_of_kind(entries, "field-map-entry")
        }
        self.questions = {item["question_id"]: item for item in records_of_kind(entries, "question-mapping")}
        self.candidates = [record for kind, record in entries if kind in STEP7_RECORD_KINDS]
        self.candidate_dependency_kind = {
            record["candidate_id"]: kind
            for kind, record in entries
            if kind in STEP7_RECORD_KINDS
        }
        self.dependencies = _dependency_index(entries)
        self.primary_paper_ids = set(self.cards)
        self.review_paper_ids = set(self.reviews)
        self.unit_owner: dict[str, tuple[str, str]] = {}
        for paper_id, card in self.cards.items():
            for section in card["sections"]:
                for unit in section["units"]:
                    self.unit_owner[unit["unit_id"]] = ("primary", paper_id)
        for paper_id, review in self.reviews.items():
            for section in review["sections"]:
                for unit in section["units"]:
                    self.unit_owner[unit["review_unit_id"]] = ("review", paper_id)
        self.tags_by_target, self.tag_dependencies_by_target = _tag_projection(entries)

    def paper_title(self, paper_id: str) -> str:
        bibliography = self.papers.get(paper_id, {}).get("bibliography", {})
        title = bibliography.get("title")
        return title.strip() if isinstance(title, str) and title.strip() else "Untitled"

    def paper_dependencies(self, paper_id: str, *, review: bool = False) -> tuple[ViewDependency, ...]:
        keys = [("domain-profile", self.profile_id), ("registry-paper", paper_id)]
        if review:
            keys.append(
                ("review-semantic-bundle", paper_id)
                if ("review-semantic-bundle", paper_id) in self.dependencies
                else ("review-memory", paper_id)
            )
        elif ("primary-semantic-bundle", paper_id) in self.dependencies:
            keys.append(("primary-semantic-bundle", paper_id))
        else:
            keys.append(("paper-card", paper_id))
            keys.extend(("evidence", item["evidence_id"]) for item in self.evidence_by_paper.get(paper_id, []))
        keys.extend(self.tag_dependencies_by_target.get(("paper", paper_id), ()))
        return self.resolve_dependencies(keys)

    def direction_dependencies(self, direction_id: str) -> tuple[ViewDependency, ...]:
        direction = self.directions[direction_id]
        keys: list[tuple[str, str]] = [("direction-bundle", direction_id)]
        keys.extend(self.tag_dependencies_by_target.get(("direction", direction_id), ()))
        for field in self.fields.values():
            if direction_id in field.get("direction_refs", []):
                keys.append(("field-map-bundle", field["field_map_entry_id"]))
        for link in direction.get("links", []):
            keys.extend(self.unit_dependency_keys(str(link.get("source_unit_id", ""))))
        return self.resolve_dependencies(keys)

    def question_dependencies(self, question_id: str) -> tuple[ViewDependency, ...]:
        question = self.questions[question_id]
        key = (
            ("question-revision-bundle", question_id)
            if ("question-revision-bundle", question_id) in self.dependencies
            else ("question-mapping", question_id)
        )
        keys: list[tuple[str, str]] = [key]
        keys.extend(self.tag_dependencies_by_target.get(("question", question_id), ()))
        for link in question.get("paper_links", []):
            keys.append(("registry-paper", link["paper_id"]))
            keys.extend(("paper-card-unit", unit_id) for unit_id in link["selected_card_unit_ids"])
            keys.extend(("evidence", evidence_id) for evidence_id in link.get("evidence_ids", []))
        for item in question.get("background_links", []):
            link = item.get("link", item)
            keys.extend(self.unit_dependency_keys(str(link.get("source_unit_id", ""))))
        return self.resolve_dependencies(keys)

    def synthesis_dependencies(self, question_id: str) -> tuple[ViewDependency, ...]:
        question_key = (
            ("question-revision-bundle", question_id)
            if ("question-revision-bundle", question_id) in self.dependencies
            else ("question-mapping", question_id)
        )
        keys: list[tuple[str, str]] = [
            ("domain-profile-version", self.profile_id),
            question_key,
        ]
        for candidate in self._question_candidates(question_id):
            keys.append((candidate["_dependency_kind"], candidate["candidate_id"]))
            for base in candidate.get("paper_card_base", []):
                keys.append(("paper-card", base["paper_id"]))
                keys.extend(("paper-card-unit", unit_id) for unit_id in base["card_unit_ids"])
            keys.extend(("evidence", evidence_id) for evidence_id in candidate.get("evidence_base", []))
            keys.extend(("review-queue", queue_id) for queue_id in candidate.get("review_queue_refs", []))
            for base in candidate.get("review_background_base", []):
                keys.append(("registry-paper", base["paper_id"]))
                keys.extend(("review-unit", unit_id) for unit_id in base["review_unit_ids"])
            keys.extend(
                (self.candidate_dependency_kind[source_id], source_id)
                for source_id in candidate.get("source_views", [])
                if source_id in self.candidate_dependency_kind
            )
        return self.resolve_dependencies(keys)

    def _question_candidates(self, question_id: str) -> list[dict[str, Any]]:
        result = []
        for kind, record in self.entries:
            if kind in STEP7_RECORD_KINDS and record["question_id"] == question_id:
                result.append({**record, "_dependency_kind": kind})
        return sorted(result, key=lambda item: (item["type"], item["candidate_id"]))

    def unit_dependency_keys(self, unit_id: str) -> list[tuple[str, str]]:
        owner = self.unit_owner.get(unit_id)
        if owner is None:
            return []
        unit_kind = "review-unit" if owner[0] == "review" else "paper-card-unit"
        return [("registry-paper", owner[1]), (unit_kind, unit_id)]

    def resolve_dependencies(self, keys: Iterable[tuple[str, str]]) -> tuple[ViewDependency, ...]:
        resolved = {
            key: self.dependencies[key]
            for key in keys
            if key in self.dependencies
        }
        return tuple(resolved[key] for key in sorted(resolved))


def _dependency_index(entries: list[BundleEntry]) -> dict[tuple[str, str], ViewDependency]:
    result: dict[tuple[str, str], ViewDependency] = {}
    active_primary_ids = {
        record["paper_id"] for kind, record in entries if kind == "primary-semantic-bundle"
    }
    active_review_ids = {
        record["paper_id"] for kind, record in entries if kind == "review-semantic-bundle"
    }
    active_question_ids = {
        record["question_id"] for kind, record in entries if kind == "question-revision-bundle"
    }
    for kind, record in entries:
        if kind in {"paper-card", "evidence", "review-queue"} and record.get("paper_id") in active_primary_ids:
            continue
        if kind == "review-memory" and record.get("paper_id") in active_review_ids:
            continue
        identity = _record_identity(kind, record)
        if identity is None:
            continue
        if kind == "question-mapping" and identity[1] in active_question_ids:
            continue
        revision_id = record.get("active_revision_id") if kind.endswith("-bundle") else None
        result[identity] = ViewDependency(kind, identity[1], canonical_digest(record), revision_id)
        if kind == "domain-profile":
            result[("domain-profile-version", identity[1])] = ViewDependency(
                "domain-profile-version",
                identity[1],
                canonical_digest({"version": record["domain_profile"]["version"]}),
            )
        elif kind == "paper-card":
            _index_card_units(result, record, revision_id=None)
        elif kind == "review-memory":
            _index_review_units(result, record, revision_id=None)
    for kind, bundle in entries:
        if kind == "primary-semantic-bundle":
            revision_id = bundle["active_revision_id"]
            for child_kind, child in active_primary_entries(bundle):
                identity = _record_identity(child_kind, child)
                if identity is not None:
                    result[identity] = ViewDependency(
                        child_kind,
                        identity[1],
                        canonical_digest(child),
                        revision_id,
                    )
                if child_kind == "paper-card":
                    _index_card_units(result, child, revision_id=revision_id)
        elif kind == "review-semantic-bundle":
            revision_id = bundle["active_revision_id"]
            for child_kind, child in active_review_entries(bundle):
                identity = _record_identity(child_kind, child)
                if identity is not None:
                    result[identity] = ViewDependency(
                        child_kind,
                        identity[1],
                        canonical_digest(child),
                        revision_id,
                    )
                if child_kind == "review-memory":
                    _index_review_units(result, child, revision_id=revision_id)
    return result


def _index_card_units(
    result: dict[tuple[str, str], ViewDependency],
    card: Mapping[str, Any],
    *,
    revision_id: str | None,
) -> None:
    for section in card.get("sections", []):
        for unit in section.get("units", []):
            unit_id = unit.get("unit_id")
            if isinstance(unit_id, str) and unit_id:
                result[("paper-card-unit", unit_id)] = ViewDependency(
                    "paper-card-unit",
                    unit_id,
                    canonical_digest(unit),
                    revision_id,
                )


def _index_review_units(
    result: dict[tuple[str, str], ViewDependency],
    memory: Mapping[str, Any],
    *,
    revision_id: str | None,
) -> None:
    for section in memory.get("sections", []):
        for unit in section.get("units", []):
            unit_id = unit.get("review_unit_id")
            if isinstance(unit_id, str) and unit_id:
                result[("review-unit", unit_id)] = ViewDependency(
                    "review-unit",
                    unit_id,
                    canonical_digest(unit),
                    revision_id,
                )


def _record_identity(kind: str, record: Mapping[str, Any]) -> tuple[str, str] | None:
    fields = {
        "domain-profile": "domain_profile",
        "registry-paper": "paper_id",
        "primary-semantic-bundle": "paper_id",
        "paper-card": "paper_id",
        "evidence": "evidence_id",
        "review-queue": "queue_id",
        "review-memory": "paper_id",
        "review-semantic-bundle": "paper_id",
        "direction-bundle": "direction_id",
        "field-map-bundle": "field_map_entry_id",
        "question-revision-bundle": "question_id",
        "question-mapping": "question_id",
        "tag-bundle": "tag_id",
        "tag-link-bundle": "tag_link_id",
        "step7-synthesis": "candidate_id",
        "step7-review-angle": "candidate_id",
        "step7-insight": "candidate_id",
        "step7-cross-view": "candidate_id",
    }
    field = fields.get(kind)
    value = record.get(field) if field is not None else None
    if kind == "domain-profile" and isinstance(value, Mapping):
        value = value.get("id")
    return (kind, value) if isinstance(value, str) and value else None


def _tag_projection(
    entries: list[BundleEntry],
) -> tuple[dict[tuple[str, str], tuple[str, ...]], dict[tuple[str, str], tuple[tuple[str, str], ...]]]:
    tags: dict[str, dict[str, Any]] = {}
    for kind, bundle in entries:
        if kind != "tag-bundle":
            continue
        tag = active_tag(bundle)
        if tag is not None:
            tags[bundle["tag_id"]] = tag
    names: dict[tuple[str, str], list[str]] = {}
    dependencies: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for kind, bundle in entries:
        if kind != "tag-link-bundle":
            continue
        if active_tag_link_state(bundle) != "assigned":
            continue
        target = (bundle["target_kind"], bundle["target_id"])
        tag_id = bundle["tag_id"]
        tag = tags.get(tag_id)
        if tag is None or tag.get("status") != "active":
            continue
        names.setdefault(target, []).append(tag["name"])
        dependencies.setdefault(target, []).extend(
            [("tag-bundle", tag_id), ("tag-link-bundle", bundle["tag_link_id"])]
        )
    return (
        {key: tuple(sorted(set(value))) for key, value in names.items()},
        {key: tuple(sorted(set(value))) for key, value in dependencies.items()},
    )


def _paper_view(context: _ProjectionContext, paper_id: str) -> ViewDraft:
    paper = context.papers[paper_id]
    card = context.cards[paper_id]
    bibliography = paper.get("bibliography", {})
    lines = [
        f"# {_heading(context.paper_title(paper_id))}",
        "",
        f"- Paper ID: `{paper_id}`",
        f"- Authors: {_inline(', '.join(bibliography.get('authors', [])) or 'Unknown authors')}",
        f"- Year: {_inline(str(bibliography.get('year') or 'Unknown year'))}",
        f"- DOI: {_inline(str(bibliography.get('doi') or 'No DOI'))}",
        f"- Card status: `{_inline(str(card.get('review_status', 'unknown')))}`",
    ]
    _append_tags(lines, context.tags_by_target.get(("paper", paper_id), ()))
    labels = {
        item["section_id"]: item["label"] for item in context.profile["paper_card_sections"]
    }
    for section in card["sections"]:
        lines.extend(["", f"## {_heading(labels.get(section['section_id'], section['section_id']))}"])
        if not section["units"]:
            lines.extend(["", "None."])
            continue
        for unit in section["units"]:
            lines.extend(
                [
                    "",
                    f"### Unit `{unit['unit_id']}`",
                    "",
                    _paragraph(unit["statement"]),
                    "",
                    f"- Grounding: `{_inline(unit['grounding_status'])}`",
                    f"- Statement type: `{_inline(unit['statement_type'])}`",
                    f"- Confidence: `{_inline(unit['confidence'])}`",
                ]
            )
            evidence_ids = unit.get("evidence_ids", [])
            lines.append("- Evidence: " + (", ".join(f"`{item}`" for item in evidence_ids) or "None"))
    lines.extend(["", "## Canonical Evidence"])
    evidence = context.evidence_by_paper.get(paper_id, [])
    if not evidence:
        lines.extend(["", "None."])
    for item in sorted(evidence, key=lambda value: value["evidence_id"]):
        page = item.get("source_page") or {}
        lines.extend(
            [
                "",
                f"### Evidence `{item['evidence_id']}`",
                "",
                f"- Claim: {_inline(item['claim'])}",
                f"- Quote: {_inline(item['quote'])}",
                f"- PDF Page: {_inline(str(page.get('pdf_page') or 'Unknown'))}",
                f"- Printed Page: {_inline(str(page.get('printed_page') or 'Unknown'))}",
                f"- Section: {_inline(str(page.get('section') or 'Unknown'))}",
                f"- Figure/Table: {_inline(str(page.get('figure_or_table') or 'None'))}",
                f"- Locator: `{_code(item['locator'])}`",
                f"- Support scope: {_inline(item['support_scope'])}",
            ]
        )
    return ViewDraft(
        f"Papers/{paper_id}.md",
        "paper",
        paper_id,
        context.paper_dependencies(paper_id, review=False),
        "\n".join(lines),
        display_title=context.paper_title(paper_id),
    )


def _review_view(context: _ProjectionContext, paper_id: str) -> ViewDraft:
    review = context.reviews[paper_id]
    lines = [
        f"# {_heading(context.paper_title(paper_id))}",
        "",
        f"- Paper ID: `{paper_id}`",
        f"- Review Memory ID: `{review['review_memory_id']}`",
        f"- Review subtype: `{_inline(review['review_subtype'])}`",
        f"- Read status: `{_inline(review['read_status'])}`",
        "- background_only: true",
        "- can_enter_canonical_evidence: false",
        "- not_fact: true",
    ]
    _append_tags(lines, context.tags_by_target.get(("paper", paper_id), ()))
    for section in review["sections"]:
        lines.extend(["", f"## {_heading(section['section_id'].replace('_', ' ').title())}"])
        if not section["units"]:
            lines.extend(["", "None."])
            continue
        for unit in section["units"]:
            lines.extend(
                [
                    "",
                    f"### Unit `{unit['review_unit_id']}`",
                    "",
                    _paragraph(unit["content"]),
                    "",
                    f"- Unit type: `{_inline(unit['unit_type'])}`",
                    "- Background only: `true`",
                    "- Canonical Evidence: `forbidden`",
                ]
            )
            lines.extend(["", "#### Source Notes"])
            for note in unit.get("source_notes", []):
                lines.extend(
                    [
                        "",
                        f"- Type: `{_inline(note['note_type'])}`",
                        f"- PDF Page: {_inline(str(note.get('pdf_page') or 'Unknown'))}",
                        f"- Printed Page: {_inline(str(note.get('printed_page') or 'Unknown'))}",
                        f"- Section: {_inline(str(note.get('section') or 'Unknown'))}",
                        f"- Figure/Table: {_inline(str(note.get('figure_or_table') or 'None'))}",
                        f"- Text: {_inline(note['text'])}",
                    ]
                )
    return ViewDraft(
        f"Reviews/{paper_id}.md",
        "review",
        paper_id,
        context.paper_dependencies(paper_id, review=True),
        "\n".join(lines),
        display_title=context.paper_title(paper_id),
    )


def _direction_view(context: _ProjectionContext, direction_id: str) -> ViewDraft:
    direction = context.directions[direction_id]
    lines = [
        f"# {_heading(direction['name'])}",
        "",
        f"- Direction ID: `{direction_id}`",
        f"- Status: `{_inline(direction['status'])}`",
        f"- Scope: {_inline(direction['scope'])}",
    ]
    _append_tags(lines, context.tags_by_target.get(("direction", direction_id), ()))
    lines.extend(["", "## Known Gaps"])
    lines.extend(f"- {_inline(item)}" for item in direction.get("gap_notes", []))
    if not direction.get("gap_notes"):
        lines.append("None.")
    lines.extend(["", "## Linked Units"])
    if not direction.get("links"):
        lines.append("None.")
    for link in direction.get("links", []):
        owner = context.unit_owner.get(link["source_unit_id"])
        owner_link = ""
        if owner is not None:
            folder = "Reviews" if owner[0] == "review" else "Papers"
            owner_link = " - " + _obsidian_link(f"{folder}/{owner[1]}", context.paper_title(owner[1]))
        lines.append(
            f"- `{link['source_unit_id']}` ({_inline(link['role'])}){owner_link}"
        )
    fields = [
        item for item in context.fields.values() if direction_id in item.get("direction_refs", [])
    ]
    lines.extend(["", "## Field Map Summaries"])
    if not fields:
        lines.append("None.")
    for item in sorted(fields, key=lambda value: value["field_map_entry_id"]):
        lines.append(
            f"- **{_inline(item['title'])}** (`{item['field_map_entry_id']}`): {_inline(item['definition'])}"
        )
    return ViewDraft(
        f"Directions/{direction_id}.md",
        "direction",
        direction_id,
        context.direction_dependencies(direction_id),
        "\n".join(lines),
        display_title=direction["name"],
    )


def _question_view(context: _ProjectionContext, question_id: str) -> ViewDraft:
    question = context.questions[question_id]
    lines = [
        f"# {_heading(question['question_text'])}",
        "",
        f"- Question ID: `{question_id}`",
        f"- Status: `{_inline(question['mapping_status'])}`",
        f"- Scope: {_inline(question['scope'])}",
    ]
    _append_tags(lines, context.tags_by_target.get(("question", question_id), ()))
    lines.extend(["", "## Factual Paper Links"])
    if not question.get("paper_links"):
        lines.append("None.")
    for link in sorted(question.get("paper_links", []), key=lambda item: item["paper_id"]):
        paper_id = link["paper_id"]
        lines.append(
            f"- {_obsidian_link(f'Papers/{paper_id}', context.paper_title(paper_id))}"
            f" - Units: {', '.join(f'`{item}`' for item in link['selected_card_unit_ids']) or 'None'}"
        )
    lines.extend(["", "## Review Background"])
    backgrounds = question.get("background_links", [])
    if not backgrounds:
        lines.append("None.")
    for item in backgrounds:
        link = item.get("link", item)
        owner = context.unit_owner.get(link["source_unit_id"])
        owner_link = ""
        if owner is not None:
            owner_link = " - " + _obsidian_link(
                f"Reviews/{owner[1]}", context.paper_title(owner[1])
            )
        lines.append(f"- `{link['source_unit_id']}`{owner_link}")
    return ViewDraft(
        f"Questions/{question_id}.md",
        "question",
        question_id,
        context.question_dependencies(question_id),
        "\n".join(lines),
        display_title=question["question_text"],
    )


def _synthesis_view(context: _ProjectionContext, question_id: str) -> ViewDraft:
    question = context.questions[question_id]
    candidates = context._question_candidates(question_id)
    lines = [
        f"# {_heading(question['question_text'])}",
        "",
        f"- Question ID: `{question_id}`",
        "- Candidate only: `not_fact: true`",
    ]
    labels = {
        "synthesis": "Synthesis",
        "review_angle": "Review Angles",
        "insight": "Insights",
        "cross_view": "Cross-Views",
    }
    for candidate_type in ("synthesis", "review_angle", "insight", "cross_view"):
        lines.extend(["", f"## {labels[candidate_type]}"])
        selected = [item for item in candidates if item["type"] == candidate_type]
        if not selected:
            lines.extend(["", "None."])
            continue
        for candidate in selected:
            freshness = candidate_freshness(candidate, context.entries)
            lines.extend(
                [
                    "",
                    f"### {_heading(candidate['title'])} (`{candidate['candidate_id']}`)",
                    "",
                    f"- Status: `{_inline(candidate['candidate_status'])}`",
                    f"- Freshness: `{_inline(freshness['state'])}`",
                    f"- Analysis operator: `{_inline(candidate['analysis_operator'])}`",
                ]
            )
            for key, value in candidate.items():
                if key in _SYNTHESIS_CONTENT_FIELDS:
                    _append_value(lines, key.replace("_", " ").title(), value)
            lines.extend(["", "#### Canonical Evidence"])
            lines.extend(f"- `{item}`" for item in candidate.get("evidence_base", []))
            if not candidate.get("evidence_base"):
                lines.append("None.")
            lines.extend(["", "#### Review Boundaries (Not Evidence)"])
            lines.extend(f"- `{item}`" for item in candidate.get("review_queue_refs", []))
            if not candidate.get("review_queue_refs"):
                lines.append("None.")
            lines.extend(["", "#### Review Background (Not Evidence)"])
            backgrounds = candidate.get("review_background_base", [])
            if not backgrounds:
                lines.append("None.")
            for base in backgrounds:
                paper_id = base["paper_id"]
                lines.append(
                    f"- {_obsidian_link(f'Reviews/{paper_id}', context.paper_title(paper_id))}: "
                    + ", ".join(f"`{item}`" for item in base["review_unit_ids"])
                )
    return ViewDraft(
        f"Research Synthesis/{question_id}.md",
        "research_synthesis",
        question_id,
        context.synthesis_dependencies(question_id),
        "\n".join(lines),
        display_title=question["question_text"],
    )


_SYNTHESIS_CONTENT_FIELDS = {
    "claim",
    "scope",
    "agreement_pattern",
    "conflict_pattern",
    "boundary_statement",
    "thesis",
    "organizing_axes",
    "included_clusters",
    "excluded_scope",
    "why_this_angle_adds_value",
    "insight_type",
    "hypothesis_or_idea",
    "rationale",
    "falsification_condition",
    "minimum_test",
    "source_views",
    "relation_type",
    "why_interesting",
    "shared_dimension",
    "non_equivalence_warning",
    "missing_evidence",
    "assumptions",
    "risk",
    "testability",
    "next_action",
}


def _index_view(
    context: _ProjectionContext,
    view_id: str,
    logical_path: str,
    title: str,
    children: list[ViewDraft],
) -> ViewDraft:
    del context
    lines = [f"# {title}", ""]
    if not children:
        lines.append("None.")
    for child in children:
        target = child.logical_path[: -len(".md")]
        lines.append(f"- {_obsidian_link(target, _view_display_title(child))}")
    dependencies = _merge_dependencies(item.dependencies for item in children)
    return ViewDraft(logical_path, "index", view_id, dependencies, "\n".join(lines))


def _home_view(
    context: _ProjectionContext,
    drafts: list[ViewDraft],
    optional_tables: tuple[str, ...],
) -> ViewDraft:
    counts = {
        "Papers": len(context.primary_paper_ids),
        "Reviews": len(context.review_paper_ids),
        "Directions": len(context.directions),
        "Questions": len(context.questions),
        "Research Synthesis": len(
            {item["question_id"] for item in context.candidates}
        ),
    }
    lines = ["# Research KB", ""]
    for label, target in (
        ("Papers", "Papers/_index"),
        ("Reviews", "Reviews/_index"),
        ("Directions", "Directions/_index"),
        ("Questions", "Questions/_index"),
        ("Research Synthesis", "Research Synthesis/_index"),
    ):
        lines.append(f"- {_obsidian_link(target, label)}: {counts[label]}")
    tables = [item for item in drafts if item.view_kind == "table"]
    if tables:
        lines.extend(["", "## Tables"])
        for table in tables:
            lines.append(
                f"- {_obsidian_link(table.logical_path[:-3], _view_display_title(table))}"
            )
    return ViewDraft(
        "Home.md",
        "home",
        "home",
        _merge_dependencies(item.dependencies for item in drafts),
        "\n".join(lines),
        optional_tables,
    )


def _library_summary(context: _ProjectionContext) -> ViewDraft:
    rows = ["# Library Summary", "", "| Paper | Route | Evidence |", "| --- | --- | ---: |"]
    for paper_id in sorted(context.primary_paper_ids | context.review_paper_ids):
        route = "Review" if paper_id in context.review_paper_ids else "Primary"
        folder = "Reviews" if route == "Review" else "Papers"
        rows.append(
            f"| {_obsidian_link(f'{folder}/{paper_id}', context.paper_title(paper_id))} | {route} | "
            f"{len(context.evidence_by_paper.get(paper_id, []))} |"
        )
    dependencies = _merge_dependencies(
        context.paper_dependencies(paper_id, review=paper_id in context.review_paper_ids)
        for paper_id in sorted(context.primary_paper_ids | context.review_paper_ids)
    )
    return ViewDraft(
        "Tables/library_summary.md",
        "table",
        "library_summary",
        dependencies,
        "\n".join(rows),
        display_title="Library Summary",
    )


def _question_coverage(context: _ProjectionContext) -> ViewDraft:
    rows = [
        "# Question Coverage",
        "",
        "| Question | Factual Papers | Review Background | Synthesis Candidates |",
        "| --- | ---: | ---: | ---: |",
    ]
    for question_id, question in sorted(context.questions.items()):
        candidate_count = sum(item["question_id"] == question_id for item in context.candidates)
        rows.append(
            f"| {_obsidian_link(f'Questions/{question_id}', question['question_text'])} | "
            f"{len(question.get('paper_links', []))} | {len(question.get('background_links', []))} | "
            f"{candidate_count} |"
        )
    dependencies = _merge_dependencies(
        context.synthesis_dependencies(question_id)
        if any(item["question_id"] == question_id for item in context.candidates)
        else context.question_dependencies(question_id)
        for question_id in sorted(context.questions)
    )
    return ViewDraft(
        "Tables/question_coverage.md",
        "table",
        "question_coverage",
        dependencies,
        "\n".join(rows),
        display_title="Question Coverage",
    )


def _normalize_tables(values: Iterable[str]) -> tuple[str, ...]:
    selected = tuple(sorted(set(values)))
    if any(value not in OPTIONAL_TABLES for value in selected):
        raise ValueError("unsupported optional Obsidian table")
    return selected


def _merge_dependencies(groups: Iterable[Iterable[ViewDependency]]) -> tuple[ViewDependency, ...]:
    merged = {
        (item.record_kind, item.record_id): item
        for group in groups
        for item in group
    }
    return tuple(merged[key] for key in sorted(merged))


def _group(records: Iterable[dict[str, Any]], key: str) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        result.setdefault(record[key], []).append(record)
    return result


def _view_display_title(view: ViewDraft) -> str:
    return view.display_title or "Untitled"


def _append_tags(lines: list[str], tags: Iterable[str]) -> None:
    values = tuple(tags)
    if values:
        lines.append("- Tags: " + ", ".join(_inline(item) for item in values))


def _append_value(lines: list[str], label: str, value: Any) -> None:
    if isinstance(value, list):
        lines.extend(["", f"#### {label}"])
        lines.extend(f"- {_inline(str(item))}" for item in value)
    else:
        lines.append(f"- {label}: {_inline(str(value))}")


def _obsidian_link(target: str, label: str) -> str:
    safe_target = target.replace("\\", "/")
    if safe_target.startswith("/") or ".." in safe_target.split("/"):
        raise ValueError("unsafe generated Obsidian link target")
    return f"[[{safe_target}|{_link_label(label)}]]"


def _yaml(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _heading(value: str) -> str:
    return _inline(value).replace("#", "\\#")


def _paragraph(value: str) -> str:
    return _inline(value)


def _inline(value: str) -> str:
    normalized = value.replace("\r", " ").replace("\n", " ")
    normalized = re.sub(
        r"(?i)\b(file|https?|ftp|javascript|data|obsidian):",
        lambda match: match.group(1) + r"\:",
        normalized,
    )
    for character in ("\\", "`", "*", "_", "[", "]", "<", ">", "#", "|", "!", "(", ")"):
        normalized = normalized.replace(character, "\\" + character)
    return normalized


def _link_label(value: str) -> str:
    return _inline(value)


def _code(value: str) -> str:
    return value.replace("`", "\\`").replace("\r", " ").replace("\n", " ")


__all__ = [
    "MANIFEST_CONTRACT",
    "OPTIONAL_TABLES",
    "RENDERER_VERSION",
    "ViewDependency",
    "ViewDraft",
    "project_obsidian_views",
]
