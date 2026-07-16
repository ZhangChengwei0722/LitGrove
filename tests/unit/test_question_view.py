from __future__ import annotations

from pathlib import Path

import pytest

from research_kb.errors import ResearchKBError
from research_kb.services.question_view import QuestionReadingViewService
from research_kb.storage.json_io import serialize_json, sha256_bytes
from tests.fixture_factory import invalid_bundle, make_bundle


ROOT = Path(__file__).resolve().parents[2]


def _entries(domain: str = "alpha") -> list[tuple[str, dict]]:
    return [
        (entry["kind"], entry["record"])
        for entry in make_bundle(domain)["records"]
    ]


def _records(entries: list[tuple[str, dict]], kind: str) -> list[dict]:
    return [record for entry_kind, record in entries if entry_kind == kind]


def _question_id(entries: list[tuple[str, dict]], index: int = 0) -> str:
    return _records(entries, "question-mapping")[index]["question_id"]


def _only_question(entries: list[tuple[str, dict]], question_id: str) -> list[tuple[str, dict]]:
    return [
        (kind, record)
        for kind, record in entries
        if not kind.startswith("step7-")
        and (kind != "question-mapping" or record["question_id"] == question_id)
    ]


def _record(entries: list[tuple[str, dict]], kind: str, key: str, value: str) -> dict:
    return next(record for record in _records(entries, kind) if record[key] == value)


def test_render_alpha_matches_reviewed_golden() -> None:
    entries = _entries()

    rendered = QuestionReadingViewService(entries).render(_question_id(entries))

    assert rendered == (
        ROOT / "tests" / "fixtures" / "rendered" / "question_reading_view_alpha.md"
    ).read_bytes()


def test_render_beta_uses_domain_profile_labels_and_equivalent_structure() -> None:
    entries = _entries("beta")

    rendered = QuestionReadingViewService(entries).render(_question_id(entries)).decode("utf-8")

    assert 'domain_profile_id: "domain-beta"' in rendered
    assert "#### Research Problem (`research_problem`)" in rendered
    assert "#### Conclusions Applications (`conclusions_applications`)" in rendered
    assert "question_b0000001-0000-4000-8000-000000000001" in rendered
    assert "domain-alpha" not in rendered


def test_selected_units_follow_profile_order_and_repeated_evidence_renders_once() -> None:
    entries = _entries()
    question_id = _question_id(entries)
    entries = _only_question(entries, question_id)
    mapping = _records(entries, "question-mapping")[0]
    paper_id = mapping["paper_links"][0]["paper_id"]
    card = _record(entries, "paper-card", "paper_id", paper_id)
    units = [unit for section in card["sections"] for unit in section["units"]]
    first, second = units[0], units[1]
    second["evidence_ids"] = list(first["evidence_ids"])
    link = mapping["paper_links"][0]
    link["selected_card_unit_ids"] = [second["unit_id"], first["unit_id"]]
    link["evidence_ids"] = list(first["evidence_ids"])
    mapping["paper_links"] = [link]

    rendered = QuestionReadingViewService(entries).render(question_id).decode("utf-8")

    assert rendered.index(first["unit_id"]) < rendered.index(second["unit_id"])
    evidence_heading = f'#### Evidence `{first["evidence_ids"][0]}`'
    assert rendered.count(evidence_heading) == 1


def test_paper_links_sort_by_paper_id_and_projection_excludes_extra_records() -> None:
    entries = _entries()
    mapping = _records(entries, "question-mapping")[0]
    mapping["paper_links"].reverse()
    first_paper_id, second_paper_id = sorted(link["paper_id"] for link in mapping["paper_links"])
    extra_unit_id = _records(entries, "paper-card")[0]["sections"][2]["units"][0]["unit_id"]
    extra_evidence_id = _records(entries, "evidence")[1]["evidence_id"]

    rendered = QuestionReadingViewService(entries).render(mapping["question_id"]).decode("utf-8")

    assert rendered.index(first_paper_id) < rendered.index(second_paper_id)
    assert extra_unit_id not in rendered
    assert extra_evidence_id not in rendered


def test_interpretive_mapping_renders_explicit_no_evidence_and_empty_sections() -> None:
    entries = _entries()
    question_id = _question_id(entries)
    entries = _only_question(entries, question_id)
    mapping = _records(entries, "question-mapping")[0]
    paper_id = mapping["paper_links"][0]["paper_id"]
    card = _record(entries, "paper-card", "paper_id", paper_id)
    interpretive = next(
        unit
        for section in card["sections"]
        for unit in section["units"]
        if unit["grounding_status"] == "interpretive"
    )
    link = mapping["paper_links"][0]
    link["selected_card_unit_ids"] = [interpretive["unit_id"]]
    link["evidence_ids"] = []
    link["boundary_refs"] = []
    mapping["paper_links"] = [link]

    rendered = QuestionReadingViewService(entries).render(question_id).decode("utf-8")

    assert "- Evidence IDs: No canonical evidence projected." in rendered
    assert "## Canonical Evidence Trace\n\nNone." in rendered
    assert (
        "These records are risk and unresolved-context boundaries. They are not evidence.\n\nNone."
        in rendered
    )
    assert "## Freshness Diagnostics\n\nNone." in rendered


def test_background_and_needs_resolution_units_have_no_evidence_projection() -> None:
    entries = _entries()
    question_id = _question_id(entries)
    entries = _only_question(entries, question_id)
    mapping = _records(entries, "question-mapping")[0]
    second_link = mapping["paper_links"][1]
    second_card = _record(entries, "paper-card", "paper_id", second_link["paper_id"])
    units = [unit for section in second_card["sections"] for unit in section["units"]]
    background = next(unit for unit in units if unit["grounding_status"] == "background_only")
    unresolved = next(unit for unit in units if unit["grounding_status"] == "needs_resolution")
    second_link["selected_card_unit_ids"] = [unresolved["unit_id"], background["unit_id"]]
    second_link["evidence_ids"] = []
    second_link["boundary_refs"] = list(unresolved["boundary_refs"])
    mapping["paper_links"] = [second_link]
    mapping["mapping_status"] = "needs_resolution"

    rendered = QuestionReadingViewService(entries).render(question_id).decode("utf-8")

    assert rendered.count("- Evidence IDs: No canonical evidence projected.") == 2
    assert "> WARNING: This question mapping requires resolution." in rendered
    assert "## Canonical Evidence Trace\n\nNone." in rendered


def test_needs_resolution_and_stale_freshness_are_independent() -> None:
    entries = _entries()
    question_id = _question_id(entries)
    entries = _only_question(entries, question_id)
    mapping = _records(entries, "question-mapping")[0]
    mapping["mapping_status"] = "needs_resolution"
    linked_card = _record(
        entries,
        "paper-card",
        "paper_id",
        mapping["paper_links"][0]["paper_id"],
    )
    linked_card["updated_at"] = "2026-01-02T00:00:00Z"

    rendered = QuestionReadingViewService(entries).render(question_id).decode("utf-8")

    assert 'mapping_status: "needs_resolution"' in rendered
    assert 'freshness_status: "stale"' in rendered
    assert "> WARNING: This question mapping requires resolution." in rendered
    diagnostic = (
        "- `RKBC-014` | `question-mapping` | "
        f'`{question_id}` | `/updated_at` | '
        "question mapping is older than a linked Paper Card, evidence, or review queue record"
    )
    assert diagnostic in rendered
    assert "paper-card" not in rendered.split("## Freshness Diagnostics", 1)[1]


def test_bibliography_fallbacks_do_not_reveal_source_path() -> None:
    entries = _entries()
    mapping = _records(entries, "question-mapping")[0]
    first_paper = _record(
        entries,
        "registry-paper",
        "paper_id",
        mapping["paper_links"][0]["paper_id"],
    )
    first_paper["bibliography"] = {
        "title": None,
        "authors": [],
        "year": None,
        "doi": None,
    }

    rendered = QuestionReadingViewService(entries).render(mapping["question_id"]).decode("utf-8")

    assert f'### Untitled (`{first_paper["paper_id"]}`)' in rendered
    assert "- Authors: Unknown authors" in rendered
    assert "- Year: Unknown year" in rendered
    assert "- DOI: No DOI" in rendered
    assert first_paper["source_ref"]["relative_path"] not in rendered


def test_inline_and_multiline_markdown_are_escaped_without_changing_line_order() -> None:
    entries = _entries()
    mapping = _records(entries, "question-mapping")[0]
    mapping["question_text"] = "What #value [x]?\r\nNext >"
    mapping["scope"] = "Scope *bounded* (only)."
    evidence = _records(entries, "evidence")[0]
    evidence["quote"] = "line # one\r\nline > two"
    evidence["locator"] = " page`one``two "
    evidence["source_page"] = {
        "pdf_page": 3,
        "printed_page": "A[1]",
        "section": "Result#1",
        "figure_or_table": "Table|2",
    }

    rendered = QuestionReadingViewService(entries).render(mapping["question_id"]).decode("utf-8")

    assert "# What \\#value \\[x\\]? Next \\>" in rendered
    assert "Scope \\*bounded\\* \\(only\\)\\." in rendered
    assert "> line \\# one\n> line \\> two" in rendered
    assert "- Locator: ```  page`one``two  ```" in rendered
    assert (
        "PDF Page: 3; Printed Page: A\\[1\\]; Section: Result\\#1; Figure/Table: Table\\|2"
        in rendered
    )
    assert "\r" not in rendered


def test_source_page_null_and_partial_formatting() -> None:
    entries = _entries()
    mapping = _records(entries, "question-mapping")[0]
    card = _record(
        entries,
        "paper-card",
        "paper_id",
        mapping["paper_links"][0]["paper_id"],
    )
    selected = next(
        unit
        for section in card["sections"]
        for unit in section["units"]
        if unit["unit_id"] == mapping["paper_links"][0]["selected_card_unit_ids"][0]
    )
    selected["source_page"] = None
    boundary = _records(entries, "review-queue")[0]
    boundary["source_page"] = {
        "pdf_page": 4,
        "printed_page": "S2",
        "section": None,
        "figure_or_table": None,
    }

    rendered = QuestionReadingViewService(entries).render(mapping["question_id"]).decode("utf-8")

    assert "- Source Page: Not available." in rendered
    assert "- Source Page: PDF Page: 4; Printed Page: S2" in rendered


def test_frontmatter_and_snapshot_digest_follow_exact_wrapper_contract() -> None:
    entries = _entries()
    mapping = _records(entries, "question-mapping")[0]
    linked_paper_ids = sorted(link["paper_id"] for link in mapping["paper_links"])
    evidence_ids = sorted({value for link in mapping["paper_links"] for value in link["evidence_ids"]})
    queue_ids = sorted({value for link in mapping["paper_links"] for value in link["boundary_refs"]})
    profile = _records(entries, "domain-profile")[0]
    wrapper_records = {
        "question-mapping": [(mapping["question_id"], mapping)],
        "domain-profile": [(profile["domain_profile"]["id"], profile)],
        "registry-paper": [
            (paper_id, _record(entries, "registry-paper", "paper_id", paper_id))
            for paper_id in linked_paper_ids
        ],
        "paper-card": [
            (paper_id, _record(entries, "paper-card", "paper_id", paper_id))
            for paper_id in linked_paper_ids
        ],
        "evidence": [
            (evidence_id, _record(entries, "evidence", "evidence_id", evidence_id))
            for evidence_id in evidence_ids
        ],
        "review-queue": [
            (queue_id, _record(entries, "review-queue", "queue_id", queue_id))
            for queue_id in queue_ids
        ],
    }
    inputs = [
        {"record_kind": kind, "record_id": record_id, "record": record}
        for kind in (
            "question-mapping",
            "domain-profile",
            "registry-paper",
            "paper-card",
            "evidence",
            "review-queue",
        )
        for record_id, record in wrapper_records[kind]
    ]
    expected_digest = sha256_bytes(
        serialize_json({"view_contract_version": "1.0", "inputs": inputs})
    )

    rendered = QuestionReadingViewService(entries).render(mapping["question_id"])

    assert f'source_snapshot_sha256: "{expected_digest}"\n'.encode() in rendered
    assert b'view_type: "question_reading_view"\n' in rendered
    assert b'canonical: false\ngenerated_view: true\neditable_source: false\n' in rendered
    assert not rendered.startswith(b"\xef\xbb\xbf")
    assert b"\r" not in rendered
    assert rendered.endswith(b"\n") and not rendered.endswith(b"\n\n")


def test_render_missing_and_invalid_question_ids_use_structured_diagnostics() -> None:
    entries = _entries()

    with pytest.raises(ResearchKBError) as invalid:
        QuestionReadingViewService(entries).render("not-a-question")
    with pytest.raises(ResearchKBError) as missing:
        QuestionReadingViewService(entries).render(
            "question_f0000000-0000-4000-8000-000000000001"
        )

    assert invalid.value.diagnostic.code == "RKBC-002"
    assert missing.value.diagnostic.code == "RKBC-005"
    assert missing.value.diagnostic.record_kind == "question-mapping"


def test_invalid_bundle_fails_before_rendering() -> None:
    bundle = invalid_bundle("duplicate_id")
    entries = [(entry["kind"], entry["record"]) for entry in bundle["records"]]

    with pytest.raises(ResearchKBError) as caught:
        QuestionReadingViewService(entries).render(_question_id(entries))

    assert caught.value.diagnostic.code == "RKBC-004"
