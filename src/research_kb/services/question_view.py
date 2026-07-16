from __future__ import annotations

import json
import re
from typing import Any

from research_kb.bundle import BundleEntry, records_of_kind, validate_workspace_entries
from research_kb.errors import UNRESOLVED_REFERENCE, Diagnostic, ResearchKBError
from research_kb.identifiers import Namespace, validate_id
from research_kb.services.question_mapping import mapping_freshness_diagnostics
from research_kb.storage.json_io import serialize_json, sha256_bytes


VIEW_CONTRACT_VERSION = "1.0"
_INLINE_MARKDOWN = frozenset("`*_{}[]()<>#+-.!|>")
_SNAPSHOT_KIND_ORDER = (
    "question-mapping",
    "domain-profile",
    "registry-paper",
    "paper-card",
    "evidence",
    "review-queue",
)


class QuestionReadingViewService:
    def __init__(self, entries: list[BundleEntry]):
        self.entries = list(entries)

    def render(self, question_id: str) -> bytes:
        question_id = validate_id(question_id, Namespace.QUESTION)
        validate_workspace_entries(self.entries)

        mapping = next(
            (
                item
                for item in records_of_kind(self.entries, "question-mapping")
                if item["question_id"] == question_id
            ),
            None,
        )
        if mapping is None:
            raise ResearchKBError(
                Diagnostic(
                    UNRESOLVED_REFERENCE,
                    "question-mapping",
                    question_id,
                    "/question_id",
                    "question mapping does not exist",
                )
            )

        projection = self._project(mapping)
        diagnostics = mapping_freshness_diagnostics(mapping, self.entries)
        freshness_status = "stale" if diagnostics else "current"
        snapshot_digest = self._snapshot_digest(mapping, projection)
        return self._render_markdown(
            mapping,
            projection,
            diagnostics,
            freshness_status,
            snapshot_digest,
        )

    def _project(self, mapping: dict[str, Any]) -> dict[str, Any]:
        profiles = {
            item["domain_profile"]["id"]: item
            for item in records_of_kind(self.entries, "domain-profile")
        }
        papers = {
            item["paper_id"]: item
            for item in records_of_kind(self.entries, "registry-paper")
        }
        cards = {
            item["paper_id"]: item
            for item in records_of_kind(self.entries, "paper-card")
        }
        evidence = {
            item["evidence_id"]: item
            for item in records_of_kind(self.entries, "evidence")
        }
        queues = {
            item["queue_id"]: item
            for item in records_of_kind(self.entries, "review-queue")
        }

        profile = self._require(
            profiles,
            mapping["domain_profile_id"],
            "domain-profile",
            "/domain_profile_id",
        )
        profile_sections = profile["paper_card_sections"]
        linked_papers: list[dict[str, Any]] = []
        projected_evidence: dict[str, dict[str, Any]] = {}
        projected_queues: dict[str, dict[str, Any]] = {}

        for link in sorted(mapping["paper_links"], key=lambda item: item["paper_id"]):
            paper_id = link["paper_id"]
            paper = self._require(papers, paper_id, "registry-paper", "/paper_links")
            card = self._require(cards, paper_id, "paper-card", "/paper_links")
            selected_ids = set(link["selected_card_unit_ids"])
            card_sections = {item["section_id"]: item for item in card["sections"]}
            selected_sections: list[dict[str, Any]] = []

            for section_definition in profile_sections:
                section_id = section_definition["section_id"]
                section = self._require(
                    card_sections,
                    section_id,
                    "paper-card",
                    "/sections",
                    record_id=paper_id,
                )
                units = [
                    unit
                    for unit in section["units"]
                    if unit["unit_id"] in selected_ids
                ]
                if units:
                    selected_sections.append(
                        {
                            "section_id": section_id,
                            "label": section_definition["label"],
                            "units": units,
                        }
                    )

            found_ids = {
                unit["unit_id"]
                for section in selected_sections
                for unit in section["units"]
            }
            if found_ids != selected_ids:
                missing = sorted(selected_ids - found_ids)[0]
                raise ResearchKBError(
                    Diagnostic(
                        UNRESOLVED_REFERENCE,
                        "paper-card",
                        paper_id,
                        "/sections/units",
                        f"selected Card Unit does not exist: {missing}",
                    )
                )

            link_evidence = [
                self._require(evidence, evidence_id, "evidence", "/paper_links/evidence_ids")
                for evidence_id in sorted(link["evidence_ids"])
            ]
            link_queues = [
                self._require(queues, queue_id, "review-queue", "/paper_links/boundary_refs")
                for queue_id in sorted(link["boundary_refs"])
            ]
            projected_evidence.update((item["evidence_id"], item) for item in link_evidence)
            projected_queues.update((item["queue_id"], item) for item in link_queues)
            linked_papers.append(
                {
                    "link": link,
                    "paper": paper,
                    "card": card,
                    "sections": selected_sections,
                    "evidence": link_evidence,
                    "queues": link_queues,
                }
            )

        return {
            "profile": profile,
            "linked_papers": linked_papers,
            "evidence": [projected_evidence[key] for key in sorted(projected_evidence)],
            "queues": [projected_queues[key] for key in sorted(projected_queues)],
        }

    @staticmethod
    def _require(
        index: dict[str, dict[str, Any]],
        key: str,
        record_kind: str,
        json_path: str,
        *,
        record_id: str | None = None,
    ) -> dict[str, Any]:
        item = index.get(key)
        if item is None:
            raise ResearchKBError(
                Diagnostic(
                    UNRESOLVED_REFERENCE,
                    record_kind,
                    record_id or key,
                    json_path,
                    f"required {record_kind} record does not exist",
                )
            )
        return item

    @staticmethod
    def _snapshot_digest(mapping: dict[str, Any], projection: dict[str, Any]) -> str:
        records = {
            "question-mapping": [(mapping["question_id"], mapping)],
            "domain-profile": [
                (projection["profile"]["domain_profile"]["id"], projection["profile"])
            ],
            "registry-paper": [
                (item["paper"]["paper_id"], item["paper"])
                for item in projection["linked_papers"]
            ],
            "paper-card": [
                (item["card"]["paper_id"], item["card"])
                for item in projection["linked_papers"]
            ],
            "evidence": [
                (item["evidence_id"], item)
                for item in projection["evidence"]
            ],
            "review-queue": [
                (item["queue_id"], item)
                for item in projection["queues"]
            ],
        }
        inputs = [
            {
                "record_kind": kind,
                "record_id": record_id,
                "record": record,
            }
            for kind in _SNAPSHOT_KIND_ORDER
            for record_id, record in sorted(records[kind], key=lambda item: item[0])
        ]
        return sha256_bytes(
            serialize_json(
                {
                    "view_contract_version": VIEW_CONTRACT_VERSION,
                    "inputs": inputs,
                }
            )
        )

    @staticmethod
    def _render_markdown(
        mapping: dict[str, Any],
        projection: dict[str, Any],
        diagnostics: list[Diagnostic],
        freshness_status: str,
        snapshot_digest: str,
    ) -> bytes:
        profile_id = projection["profile"]["domain_profile"]["id"]
        evidence_count = len(projection["evidence"])
        queue_count = len(projection["queues"])
        unit_count = sum(
            len(section["units"])
            for paper in projection["linked_papers"]
            for section in paper["sections"]
        )
        lines = [
            "---",
            f'view_type: {_yaml_string("question_reading_view")}',
            f'view_contract_version: {_yaml_string(VIEW_CONTRACT_VERSION)}',
            f'question_id: {_yaml_string(mapping["question_id"])}',
            f'domain_profile_id: {_yaml_string(profile_id)}',
            f'mapping_status: {_yaml_string(mapping["mapping_status"])}',
            f'freshness_status: {_yaml_string(freshness_status)}',
            f'mapping_updated_at: {_yaml_string(mapping["updated_at"])}',
            f'source_snapshot_sha256: {_yaml_string(snapshot_digest)}',
            "canonical: false",
            "generated_view: true",
            "editable_source: false",
            "---",
            "",
            f'# {_escape_inline(mapping["question_text"])}',
            "",
            "## Question Scope",
            "",
            _escape_inline(mapping["scope"]),
            "",
            "## Mapping State",
            "",
            f'- Question ID: {_code_span(mapping["question_id"])}',
            f'- Domain Profile ID: {_code_span(profile_id)}',
            f'- Mapping Status: {_code_span(mapping["mapping_status"])}',
            f'- Freshness Status: {_code_span(freshness_status)}',
            f'- Linked Papers: {len(projection["linked_papers"])}',
            f'- Selected Card Units: {unit_count}',
            f'- Canonical Evidence: {evidence_count}',
            f'- Review Queue Boundaries: {queue_count}',
            f'- Mapping Updated At: {_code_span(mapping["updated_at"])}',
        ]
        if mapping["mapping_status"] == "needs_resolution":
            lines.extend(["", "> WARNING: This question mapping requires resolution."])

        lines.extend(["", "## Linked Papers And Selected Card Units", ""])
        for paper_index, item in enumerate(projection["linked_papers"]):
            if paper_index:
                lines.append("")
            _render_linked_paper(lines, item)

        lines.extend(["", "## Canonical Evidence Trace", ""])
        evidence_papers = [item for item in projection["linked_papers"] if item["evidence"]]
        if not evidence_papers:
            lines.append("None.")
        for paper_index, item in enumerate(evidence_papers):
            if paper_index:
                lines.append("")
            _render_evidence_paper(lines, item)

        lines.extend(
            [
                "",
                "## Review Queue Boundaries",
                "",
                "These records are risk and unresolved-context boundaries. They are not evidence.",
                "",
            ]
        )
        queue_papers = [item for item in projection["linked_papers"] if item["queues"]]
        if not queue_papers:
            lines.append("None.")
        for paper_index, item in enumerate(queue_papers):
            if paper_index:
                lines.append("")
            _render_queue_paper(lines, item)

        lines.extend(["", "## Freshness Diagnostics", ""])
        if not diagnostics:
            lines.append("None.")
        else:
            for diagnostic in diagnostics:
                rendered = diagnostic.to_dict()
                lines.append(
                    f'- {_code_span(diagnostic.code)} | '
                    f'{_code_span(diagnostic.record_kind)} | '
                    f'{_code_span(diagnostic.record_id or "None")} | '
                    f'{_code_span(diagnostic.json_path or "None")} | '
                    f'{_escape_inline(rendered["message"])}'
                )

        return ("\n".join(lines).rstrip("\n") + "\n").encode("utf-8")


def _render_linked_paper(lines: list[str], item: dict[str, Any]) -> None:
    paper = item["paper"]
    link = item["link"]
    bibliography = paper["bibliography"]
    title = bibliography["title"] or "Untitled"
    authors = bibliography["authors"]
    lines.extend(
        [
            f'### {_escape_inline(title)} ({_code_span(paper["paper_id"])})',
            "",
            f'- Question Link ID: {_code_span(link["question_link_id"])}',
            f'- Authors: {_escape_inline("; ".join(authors)) if authors else "Unknown authors"}',
            f'- Year: {bibliography["year"] if bibliography["year"] is not None else "Unknown year"}',
            f'- DOI: {_escape_inline(bibliography["doi"]) if bibliography["doi"] else "No DOI"}',
            f'- Screening Status: {_code_span(paper["screening_status"])}',
            f'- Role In Question: {_code_span(link["role_in_question"])}',
            f'- Relevance Rationale: {_escape_inline(link["relevance_rationale"])}',
        ]
    )
    for section in item["sections"]:
        lines.extend(
            [
                "",
                f'#### {_escape_inline(section["label"])} ({_code_span(section["section_id"])})',
            ]
        )
        for unit in section["units"]:
            evidence_ids = _id_list(unit["evidence_ids"])
            if not unit["evidence_ids"]:
                evidence_ids = "No canonical evidence projected."
            lines.extend(
                [
                    "",
                    f'##### Card Unit {_code_span(unit["unit_id"])}',
                    "",
                    f'- Statement: {_escape_inline(unit["statement"])}',
                    f'- Statement Type: {_code_span(unit["statement_type"])}',
                    f'- Grounding Status: {_code_span(unit["grounding_status"])}',
                    f'- Confidence: {_code_span(unit["confidence"])}',
                    f'- Source Page: {_source_page(unit["source_page"])}',
                    f'- Evidence IDs: {evidence_ids}',
                    f'- Boundary Refs: {_id_list(unit["boundary_refs"])}',
                ]
            )


def _render_evidence_paper(lines: list[str], item: dict[str, Any]) -> None:
    paper = item["paper"]
    title = paper["bibliography"]["title"] or "Untitled"
    lines.append(f'### {_escape_inline(title)} ({_code_span(paper["paper_id"])})')
    for evidence in item["evidence"]:
        lines.extend(
            [
                "",
                f'#### Evidence {_code_span(evidence["evidence_id"])}',
                "",
                f'- Paper ID: {_code_span(evidence["paper_id"])}',
                f'- Claim: {_escape_inline(evidence["claim"])}',
                f'- Evidence Type: {_code_span(evidence["evidence_type"])}',
                f'- Source Type: {_code_span(evidence["source_type"])}',
                "- Quote:",
                _blockquote(evidence["quote"]),
                f'- Source Page: {_source_page(evidence["source_page"])}',
                f'- Locator: {_code_span(evidence["locator"])}',
                f'- Support Scope: {_escape_inline(evidence["support_scope"])}',
                "- What It Does Not Support:",
            ]
        )
        lines.extend(
            f'  - {_escape_inline(value)}'
            for value in evidence["what_it_does_not_support"]
        )
        lines.extend(
            [
                f'- Review Status: {_code_span(evidence["review_status"])}',
                f'- Automation Status: {_code_span(evidence["automation_status"])}',
            ]
        )


def _render_queue_paper(lines: list[str], item: dict[str, Any]) -> None:
    paper = item["paper"]
    title = paper["bibliography"]["title"] or "Untitled"
    lines.append(f'### {_escape_inline(title)} ({_code_span(paper["paper_id"])})')
    for queue in item["queues"]:
        locator = _code_span(queue["locator"]) if queue["locator"] is not None else "Not available."
        lines.extend(
            [
                "",
                f'#### Boundary {_code_span(queue["queue_id"])}',
                "",
                f'- Paper ID: {_code_span(queue["paper_id"])}',
                f'- Issue Type: {_code_span(queue["issue_type"])}',
                f'- Claim Candidate: {_escape_inline(queue["claim_candidate"])}',
                f'- Reason: {_escape_inline(queue["reason"])}',
                f'- Source Page: {_source_page(queue["source_page"])}',
                f'- Locator: {locator}',
                f'- Resolution Status: {_code_span(queue["resolution_status"])}',
                f'- Review Status: {_code_span(queue["review_status"])}',
                f'- Automation Status: {_code_span(queue["automation_status"])}',
                "- Not Evidence: `true`",
            ]
        )


def _yaml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _normalize_lf(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n")


def _escape_inline(value: str) -> str:
    normalized = _normalize_lf(value).replace("\n", " ").replace("\\", "\\\\")
    return "".join(f"\\{character}" if character in _INLINE_MARKDOWN else character for character in normalized)


def _blockquote(value: str) -> str:
    return "\n".join(f"> {_escape_inline(line)}" for line in _normalize_lf(value).split("\n"))


def _code_span(value: str) -> str:
    normalized = _normalize_lf(value).replace("\n", " ")
    longest_run = max((len(match.group(0)) for match in re.finditer(r"`+", normalized)), default=0)
    if longest_run == 0 and normalized == normalized.strip():
        return f"`{normalized}`"
    fence = "`" * (longest_run + 1)
    return f"{fence} {normalized} {fence}"


def _source_page(value: dict[str, Any] | None) -> str:
    if value is None:
        return "Not available."
    parts: list[str] = []
    if value.get("pdf_page") is not None:
        parts.append(f'PDF Page: {value["pdf_page"]}')
    for key, label in (
        ("printed_page", "Printed Page"),
        ("section", "Section"),
        ("figure_or_table", "Figure/Table"),
    ):
        if value.get(key) is not None:
            parts.append(f'{label}: {_escape_inline(value[key])}')
    return "; ".join(parts) if parts else "Not available."


def _id_list(values: list[str]) -> str:
    return ", ".join(_code_span(value) for value in values) if values else "None."


__all__ = ["QuestionReadingViewService"]
