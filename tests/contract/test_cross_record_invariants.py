from copy import deepcopy

import pytest

from research_kb.contracts.validator import validate_bundle, validate_record
from tests.fixture_factory import invalid_bundle, make_bundle


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
