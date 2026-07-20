from __future__ import annotations

from typing import Any

from research_kb.bundle import BundleEntry, validate_workspace_entries
from research_kb.services.step7_context import project_step7_context
from research_kb.step7_support import STEP7_TYPE_ORDER


TYPE_LABELS = {
    "synthesis": "Synthesis",
    "review_angle": "Review Angles",
    "insight": "Insights",
    "cross_view": "Cross-Views",
}
TYPE_FIELDS = {
    "synthesis": (
        ("Claim", "claim"),
        ("Scope", "scope"),
        ("Agreement Pattern", "agreement_pattern"),
        ("Conflict Pattern", "conflict_pattern"),
        ("Boundary Statement", "boundary_statement"),
    ),
    "review_angle": (
        ("Thesis", "thesis"),
        ("Organizing Axes", "organizing_axes"),
        ("Included Clusters", "included_clusters"),
        ("Excluded Scope", "excluded_scope"),
        ("Why This Angle Adds Value", "why_this_angle_adds_value"),
    ),
    "insight": (
        ("Insight Type", "insight_type"),
        ("Hypothesis Or Idea", "hypothesis_or_idea"),
        ("Rationale", "rationale"),
        ("Falsification Condition", "falsification_condition"),
        ("Minimum Test", "minimum_test"),
    ),
    "cross_view": (
        ("Source Views", "source_views"),
        ("Relation Type", "relation_type"),
        ("Why Interesting", "why_interesting"),
        ("Shared Dimension", "shared_dimension"),
        ("Non-Equivalence Warning", "non_equivalence_warning"),
    ),
}


class Step7ReadingViewService:
    def __init__(self, entries: list[BundleEntry]):
        self.entries = entries

    def render(self, question_id: str) -> bytes:
        validate_workspace_entries(self.entries)
        context = project_step7_context(self.entries, question_id)
        return _render(context).encode("utf-8")


def _render(context: dict[str, Any]) -> str:
    mapping = context["question_mapping"]
    summary = context["summary"]
    lines = [
        "---",
        'view_type: "step7_reading_view"',
        'interface_version: "1.0"',
        f'question_id: "{context["question_id"]}"',
        "canonical: false",
        "generated_view: true",
        "editable_source: false",
        f'candidate_count: {summary["total"]}',
        f'stale_count: {summary["stale_count"]}',
        "---",
        "",
        f'# {_escape_heading(mapping["question_text"])}',
        "",
        f'- Scope: {_escape_inline(mapping["scope"])}',
        f'- Mapping Status: `{mapping["mapping_status"]}`',
        f'- Candidates: {summary["total"]}',
        f'- Stale Candidates: {summary["stale_count"]}',
    ]
    by_type = {
        candidate_type: [
            item for item in context["candidates"] if item["candidate"]["type"] == candidate_type
        ]
        for candidate_type in STEP7_TYPE_ORDER
    }
    for candidate_type in STEP7_TYPE_ORDER:
        lines.extend(["", f'## {TYPE_LABELS[candidate_type]}'])
        if not by_type[candidate_type]:
            lines.extend(["", "None."])
            continue
        for item in by_type[candidate_type]:
            _render_candidate(lines, item)
    return "\n".join(lines).rstrip() + "\n"


def _render_candidate(lines: list[str], item: dict[str, Any]) -> None:
    candidate = item["candidate"]
    freshness = item["freshness"]
    lines.extend(
        [
            "",
            f'### {_escape_heading(candidate["title"])} (`{candidate["candidate_id"]}`)',
            "",
            f'- Candidate Status: `{candidate["candidate_status"]}`',
            f'- Freshness: `{freshness["state"]}`',
            f'- Analysis Operator: `{candidate["analysis_operator"]}`',
            f'- Trace Status: `{candidate["trace_status"]}`',
            "- Candidate Only: `not_fact: true`",
        ]
    )
    if freshness["reasons"]:
        lines.append("- Stale Reasons: " + ", ".join(f'`{value}`' for value in freshness["reasons"]))
    if candidate.get("rejection_rationale") is not None:
        lines.append(f'- Rejection Rationale: {_escape_inline(candidate["rejection_rationale"])}')

    lines.extend(["", "#### Scientific Content"])
    for label, field in TYPE_FIELDS[candidate["type"]]:
        _render_value(lines, label, candidate[field])

    lines.extend(["", "#### Paper Card Base"])
    for base in candidate["paper_card_base"]:
        units = ", ".join(f'`{value}`' for value in base["card_unit_ids"])
        lines.append(f'- `{base["paper_id"]}`: {units}')

    lines.extend(["", "#### Canonical Evidence Base"])
    lines.extend(f'- `{value}`' for value in candidate["evidence_base"])
    lines.extend(["", "#### Review Queue Boundaries (Not Evidence)"])
    if candidate["review_queue_refs"]:
        lines.extend(f'- `{value}`' for value in candidate["review_queue_refs"])
    else:
        lines.append("None.")

    for label, field in (
        ("Missing Evidence", "missing_evidence"),
        ("Assumptions", "assumptions"),
        ("Risk", "risk"),
    ):
        lines.extend(["", f"#### {label}"])
        lines.extend(f'- {_escape_inline(value)}' for value in candidate[field])
    lines.extend(
        [
            "",
            "#### Testability",
            _escape_paragraph(candidate["testability"]),
            "",
            "#### Next Action",
            _escape_paragraph(candidate["next_action"]),
        ]
    )


def _render_value(lines: list[str], label: str, value: object) -> None:
    if isinstance(value, list):
        lines.append(f"- {label}:")
        lines.extend(f'  - {_escape_inline(str(item))}' for item in value)
    else:
        lines.append(f'- {label}: {_escape_inline(str(value))}')


def _escape_heading(value: str) -> str:
    return _escape_inline(value).replace("#", "\\#")


def _escape_inline(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace("`", "\\`")
        .replace("*", "\\*")
        .replace("_", "\\_")
        .replace("[", "\\[")
        .replace("]", "\\]")
        .replace("<", "\\<")
        .replace(">", "\\>")
        .replace("\r", " ")
        .replace("\n", " ")
    )


def _escape_paragraph(value: str) -> str:
    return _escape_inline(value)


__all__ = ["Step7ReadingViewService"]
