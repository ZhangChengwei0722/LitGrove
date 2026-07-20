from __future__ import annotations

from copy import deepcopy

import pytest

from research_kb.contracts.validator import validate_bundle
from research_kb.evidence_provenance import (
    normalize_legacy_block_text,
    parse_locator,
)
from tests.fixture_factory import make_bundle


def _records(bundle: dict, kind: str) -> list[dict]:
    return [entry["record"] for entry in bundle["records"] if entry["kind"] == kind]


def _diagnostics(bundle: dict) -> list:
    return validate_bundle(bundle, actor="stored")


def _diagnostic_codes(bundle: dict) -> set[str]:
    return {item.code for item in _diagnostics(bundle)}


def _character_grounded_bundle(*, text: str = "Prefix alpha beta suffix.", quote: str = "alpha beta") -> dict:
    bundle = deepcopy(make_bundle("alpha"))
    paper_id = _records(bundle, "registry-paper")[0]["paper_id"]
    pages = [item for item in _records(bundle, "parsed-page") if item["paper_id"] == paper_id]
    evidence = next(item for item in _records(bundle, "evidence") if item["paper_id"] == paper_id)
    pages[0]["text"] = text + "\nA matched control was used for the fabricated procedure."
    start = text.index(quote)
    end = start + len(quote)
    evidence["quote"] = quote
    evidence["locator"] = f"page:1:char:{start}-{end}"
    evidence["source_page"]["pdf_page"] = 1
    return bundle


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("page:2:char:0-7", ("char", 2, 0, 7, None)),
        ("page:3:block:4", ("block", 3, None, None, 4)),
    ],
)
def test_parse_locator_accepts_supported_forms(value: str, expected: tuple) -> None:
    locator = parse_locator(value)

    assert (locator.kind, locator.page, locator.start, locator.end, locator.block) == expected


@pytest.mark.parametrize(
    "value",
    [
        "",
        "page:1:text",
        "page:0:block:1",
        "page:1:block:0",
        "page:1:char:2-2",
        "page:1:char:4-2",
        "page:1:char:-1-2",
        "page:one:char:0-1",
    ],
)
def test_parse_locator_rejects_malformed_or_empty_ranges(value: str) -> None:
    with pytest.raises(ValueError, match="unsupported evidence locator"):
        parse_locator(value)


def test_normalize_legacy_block_text_collapses_unicode_whitespace() -> None:
    assert normalize_legacy_block_text(" alpha\r\n beta\t gamma ") == "alpha beta gamma"


def test_character_locator_resolves_exact_unicode_python_slice() -> None:
    bundle = _character_grounded_bundle(text="Prefix alpha βeta suffix.", quote="alpha βeta")

    assert _diagnostics(bundle) == []


@pytest.mark.parametrize(
    ("locator", "quote"),
    [
        ("page:1:char:7-16", "alpha beta"),
        ("page:1:char:7-200", "alpha beta"),
        ("page:1:char:7-17", "wrong text"),
    ],
)
def test_character_locator_rejects_wrong_range_or_quote(locator: str, quote: str) -> None:
    bundle = _character_grounded_bundle()
    evidence = _records(bundle, "evidence")[0]
    evidence["locator"] = locator
    evidence["quote"] = quote

    assert "RKBC-009" in _diagnostic_codes(bundle)


def test_locator_page_must_equal_source_page() -> None:
    bundle = _character_grounded_bundle()
    evidence = _records(bundle, "evidence")[0]
    evidence["source_page"]["pdf_page"] = 2

    diagnostic = next(item for item in _diagnostics(bundle) if item.code == "RKBC-009")
    assert diagnostic.json_path == "/locator"


def test_missing_same_paper_page_is_unresolved_even_when_other_paper_has_page() -> None:
    bundle = _character_grounded_bundle()
    evidence = _records(bundle, "evidence")[0]
    other_paper_id = _records(bundle, "registry-paper")[1]["paper_id"]
    other_page = next(
        item for item in _records(bundle, "parsed-page") if item["paper_id"] == other_paper_id
    )
    other_page["pdf_page"] = 2
    other_page["locator"] = "page:2:block:1"
    other_evidence = next(
        item for item in _records(bundle, "evidence") if item["paper_id"] == other_paper_id
    )
    other_evidence["source_page"]["pdf_page"] = 2
    other_evidence["locator"] = "page:2:block:1"
    evidence["source_page"]["pdf_page"] = 2
    evidence["locator"] = "page:2:char:0-5"

    diagnostic = next(item for item in _diagnostics(bundle) if item.record_id == evidence["evidence_id"])
    assert diagnostic.code == "RKBC-005"
    assert diagnostic.json_path == "/source_page/pdf_page"


def test_legacy_block_locator_uses_whitespace_normalized_containment() -> None:
    bundle = deepcopy(make_bundle("alpha"))
    evidence = _records(bundle, "evidence")[0]
    page = next(
        item for item in _records(bundle, "parsed-page") if item["paper_id"] == evidence["paper_id"]
    )
    page["text"] = (
        "Before\nPrimary response was higher in the fabricated comparison.\nAfter\n"
        "A matched control was used for the fabricated procedure."
    )
    evidence["quote"] = "Primary response was higher\r\n in the fabricated comparison."

    assert _diagnostics(bundle) == []


def test_legacy_block_locator_rejects_absent_quote_without_exposing_payload() -> None:
    bundle = deepcopy(make_bundle("alpha"))
    evidence = _records(bundle, "evidence")[0]
    evidence["quote"] = "SENSITIVE INVENTED QUOTE"

    diagnostic = next(item for item in _diagnostics(bundle) if item.record_id == evidence["evidence_id"])
    assert diagnostic.code == "RKBC-009"
    assert "SENSITIVE" not in diagnostic.message
    assert _records(bundle, "parsed-page")[0]["text"] not in diagnostic.message


@pytest.mark.parametrize(
    "mutation",
    [
        "duplicate_page",
        "mixed_parse_run",
        "mixed_parser",
        "non_ascending_pages",
    ],
)
def test_active_page_set_must_have_one_ordered_parser_run(mutation: str) -> None:
    bundle = deepcopy(make_bundle("alpha"))
    first_page = _records(bundle, "parsed-page")[0]
    second_page = deepcopy(first_page)
    if mutation == "duplicate_page":
        pass
    elif mutation == "mixed_parse_run":
        second_page["pdf_page"] = 2
        second_page["parse_run_id"] = _records(bundle, "process-event")[0]["event_id"].replace("0000001", "0000009")
    elif mutation == "mixed_parser":
        second_page["pdf_page"] = 2
        second_page["parser"] = {"adapter": "other-parser", "version": "9.0"}
    else:
        first_page["pdf_page"] = 2
        second_page["pdf_page"] = 1
    insert_at = next(index for index, item in enumerate(bundle["records"]) if item["kind"] == "parsed-page") + 1
    bundle["records"].insert(insert_at, {"kind": "parsed-page", "record": second_page})

    assert "RKBC-009" in _diagnostic_codes(bundle)
