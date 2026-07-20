from copy import deepcopy

import pytest

from research_kb.contracts.validator import validate_bundle, validate_record
from tests.contract.test_review_memory_contract import review_memory_record
from tests.fixture_factory import invalid_bundle, make_bundle


def _review_bundle() -> dict:
    bundle = deepcopy(make_bundle("alpha"))
    retained = {"workspace", "domain-profile", "registry-paper", "parsed-page", "process-event"}
    bundle["records"] = [entry for entry in bundle["records"] if entry["kind"] in retained]
    event = next(entry["record"] for entry in bundle["records"] if entry["kind"] == "process-event")
    event["output_refs"] = []
    bundle["records"].append({"kind": "review-memory", "record": _review_for_bundle(bundle)})
    return bundle


def _review_for_bundle(bundle: dict) -> dict:
    memory = review_memory_record()
    paper = next(entry["record"] for entry in bundle["records"] if entry["kind"] == "registry-paper")
    page = next(
        entry["record"]
        for entry in bundle["records"]
        if entry["kind"] == "parsed-page" and entry["record"]["paper_id"] == paper["paper_id"]
    )
    memory["paper_id"] = paper["paper_id"]
    memory["source_fingerprint"] = deepcopy(paper["source_fingerprint"])
    memory["parse_snapshot"] = {
        "parse_run_id": page["parse_run_id"],
        "adapter": page["parser"]["adapter"],
        "version": page["parser"]["version"],
    }
    return memory


def test_card_unit_cannot_use_another_papers_evidence() -> None:
    diagnostics = validate_bundle(invalid_bundle("unit_cross_paper_evidence"), actor="cli")
    assert "RKBC-009" in {item.code for item in diagnostics}


def test_step7_evidence_must_equal_card_unit_expansion() -> None:
    diagnostics = validate_bundle(invalid_bundle("evidence_expansion_mismatch"), actor="cli")
    assert "RKBC-014" in {item.code for item in diagnostics}


def test_step7_card_unit_must_belong_to_declared_paper() -> None:
    diagnostics = validate_bundle(invalid_bundle("wrong_paper_unit"), actor="cli")
    assert "RKBC-011" in {item.code for item in diagnostics}


def test_question_evidence_must_equal_selected_unit_expansion() -> None:
    bundle = deepcopy(make_bundle("alpha"))
    by_kind = {}
    for entry in bundle["records"]:
        by_kind.setdefault(entry["kind"], []).append(entry["record"])
    by_kind["question-mapping"][0]["paper_links"][0]["evidence_ids"].append(
        by_kind["evidence"][1]["evidence_id"]
    )

    diagnostics = validate_bundle(bundle, actor="cli")

    assert "RKBC-014" in {item.code for item in diagnostics}


def test_question_cannot_omit_selected_unit_boundary() -> None:
    bundle = deepcopy(make_bundle("alpha"))
    by_kind = {}
    for entry in bundle["records"]:
        by_kind.setdefault(entry["kind"], []).append(entry["record"])
    queue_id = by_kind["review-queue"][0]["queue_id"]
    by_kind["paper-card"][0]["sections"][1]["units"][0]["boundary_refs"] = [queue_id]
    by_kind["question-mapping"][0]["paper_links"][0]["boundary_refs"] = []

    diagnostics = validate_bundle(bundle, actor="cli")

    assert "RKBC-014" in {item.code for item in diagnostics}


def test_question_cannot_link_same_paper_twice() -> None:
    bundle = deepcopy(make_bundle("alpha"))
    question = next(
        entry["record"] for entry in bundle["records"] if entry["kind"] == "question-mapping"
    )
    duplicate = deepcopy(question["paper_links"][0])
    duplicate["question_link_id"] = "qlink_f0000000-0000-4000-8000-000000000001"
    question["paper_links"].append(duplicate)

    diagnostics = validate_bundle(bundle, actor="cli")

    assert "RKBC-004" in {item.code for item in diagnostics}


def test_needs_resolution_unit_requires_needs_resolution_question_status() -> None:
    bundle = deepcopy(make_bundle("alpha"))
    by_kind = {}
    for entry in bundle["records"]:
        by_kind.setdefault(entry["kind"], []).append(entry["record"])
    question = by_kind["question-mapping"][1]
    link = question["paper_links"][0]
    link["paper_id"] = by_kind["registry-paper"][1]["paper_id"]
    link["selected_card_unit_ids"] = [by_kind["paper-card"][1]["sections"][5]["units"][0]["unit_id"]]
    link["evidence_ids"] = []
    link["boundary_refs"] = [by_kind["review-queue"][1]["queue_id"]]

    diagnostics = validate_bundle(bundle, actor="cli")

    assert "RKBC-009" in {item.code for item in diagnostics}


@pytest.mark.parametrize(
    ("case", "expected_code"),
    [
        ("ambiguous_source_path", "RKBC-007"),
        ("unknown_source_root", "RKBC-007"),
        ("fingerprint_mismatch", "RKBC-009"),
        ("card_sections_profile_mismatch", "RKBC-009"),
        ("card_boundary_cross_paper", "RKBC-009"),
        ("question_boundary_cross_paper", "RKBC-009"),
        ("step7_boundary_cross_paper", "RKBC-011"),
        ("synthesis_same_paper_twice", "RKBC-011"),
        ("crossview_self_reference", "RKBC-011"),
    ],
)
def test_precommit_contract_gaps_are_closed(case: str, expected_code: str) -> None:
    diagnostics = validate_bundle(invalid_bundle(case), actor="cli")
    assert expected_code in {item.code for item in diagnostics}


@pytest.mark.parametrize(
    ("status", "severities"),
    [
        ("success", ["warning"]),
        ("warning", []),
        ("warning", ["error"]),
        ("failure", ["warning"]),
    ],
)
def test_guardian_status_must_match_finding_severity(status: str, severities: list[str]) -> None:
    guardian = deepcopy(next(
        entry["record"] for entry in make_bundle("alpha")["records"] if entry["kind"] == "guardian-report"
    ))
    guardian["status"] = status
    guardian["findings"] = [
        {
            "code": "RKBC-002",
            "severity": severity,
            "record_ref": None,
            "message": "Synthetic contract finding.",
            "remediation": "Correct the synthetic fixture.",
        }
        for severity in severities
    ]
    diagnostics = validate_record("guardian-report", guardian, actor="cli")
    assert "RKBC-002" in {item.code for item in diagnostics}


@pytest.mark.parametrize(("status", "severity"), [("warning", "warning"), ("failure", "error")])
def test_guardian_non_success_status_accepts_matching_severity(status: str, severity: str) -> None:
    guardian = deepcopy(next(
        entry["record"] for entry in make_bundle("alpha")["records"] if entry["kind"] == "guardian-report"
    ))
    guardian["status"] = status
    guardian["findings"] = [{
        "code": "RKBC-002",
        "severity": severity,
        "record_ref": None,
        "message": "Synthetic contract finding.",
        "remediation": "Correct the synthetic fixture.",
    }]
    assert validate_record("guardian-report", guardian, actor="cli") == []


def test_review_memory_bundle_resolves_current_parse_and_source_notes() -> None:
    assert validate_bundle(_review_bundle(), actor="stored") == []


def test_one_review_memory_per_paper_is_enforced() -> None:
    bundle = _review_bundle()
    duplicate = deepcopy(bundle["records"][-1]["record"])
    duplicate["review_memory_id"] = "reviewmem_b1111111-1111-4111-8111-111111111111"
    duplicate["sections"][2]["units"][0]["review_unit_id"] = (
        "reviewunit_b2222222-2222-4222-8222-222222222222"
    )
    bundle["records"].append({"kind": "review-memory", "record": duplicate})

    diagnostics = validate_bundle(bundle, actor="stored")

    assert "RKBC-031" in {item.code for item in diagnostics}


def test_review_unit_must_match_parent_section_and_not_duplicate_content() -> None:
    bundle = _review_bundle()
    memory = bundle["records"][-1]["record"]
    original = memory["sections"][2]["units"][0]
    duplicate = deepcopy(original)
    duplicate["review_unit_id"] = "reviewunit_b2222222-2222-4222-8222-222222222222"
    memory["sections"][2]["units"].append(duplicate)
    original["section_id"] = "major_synthesis"

    diagnostics = validate_bundle(bundle, actor="stored")

    assert "RKBC-009" in {item.code for item in diagnostics}
    assert any("duplicate" in item.message.lower() for item in diagnostics)


@pytest.mark.parametrize(
    ("status", "remove_units"),
    [("reusable", True), ("low_value", False)],
)
def test_review_memory_value_must_match_retained_unit_count(status: str, remove_units: bool) -> None:
    bundle = _review_bundle()
    memory = bundle["records"][-1]["record"]
    memory["memory_value"] = {"status": status, "reason": "Synthetic boundary case."}
    if remove_units:
        memory["sections"][2]["units"] = []

    diagnostics = validate_bundle(bundle, actor="stored")

    assert "RKBC-009" in {item.code for item in diagnostics}


def test_primary_and_review_routes_are_mutually_exclusive() -> None:
    bundle = make_bundle("alpha")
    bundle["records"].append({"kind": "review-memory", "record": _review_for_bundle(bundle)})

    diagnostics = validate_bundle(bundle, actor="stored")

    assert any(
        item.code == "RKBC-009" and "route" in item.message.lower()
        for item in diagnostics
    )


def test_stale_review_snapshot_does_not_validate_old_quote_against_new_parse() -> None:
    bundle = _review_bundle()
    memory = bundle["records"][-1]["record"]
    memory["parse_snapshot"] = {
        "parse_run_id": "event_b3333333-3333-4333-8333-333333333333",
        "adapter": "synthetic-text",
        "version": "0.9",
    }
    note = memory["sections"][2]["units"][0]["source_notes"][0]
    note.update(
        {
            "note_type": "quote_excerpt",
            "text": "not present in the active parse",
            "locator": "page:1:char:0-5",
        }
    )
    bundle["records"].append(
        {
            "kind": "process-event",
            "record": {
                "schema_version": "1.0",
                "event_id": memory["parse_snapshot"]["parse_run_id"],
                "operation": "synthetic_old_parse",
                "actor": "cli",
                "result": "success",
                "input_refs": [memory["paper_id"]],
                "output_refs": [memory["review_memory_id"]],
                "created_at": "2025-01-01T00:00:00Z",
                "fixture_origin": "synthetic_from_scratch",
            },
        }
    )

    diagnostics = validate_bundle(bundle, actor="stored")

    assert not any(item.record_kind == "review-memory" for item in diagnostics)
