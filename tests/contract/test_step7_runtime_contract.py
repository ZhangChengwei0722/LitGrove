from __future__ import annotations

from copy import deepcopy

from research_kb.contracts.validator import validate_bundle
from research_kb.step7_support import candidate_freshness
from tests.fixture_factory import make_bundle


def _by_kind(bundle: dict) -> dict[str, list[dict]]:
    result: dict[str, list[dict]] = {}
    for entry in bundle["records"]:
        result.setdefault(entry["kind"], []).append(entry["record"])
    return result


def _entries(bundle: dict) -> list[tuple[str, dict]]:
    return [(entry["kind"], entry["record"]) for entry in bundle["records"]]


def test_newer_card_support_change_is_stale_not_structural_corruption() -> None:
    bundle = deepcopy(make_bundle("alpha"))
    records = _by_kind(bundle)
    candidate = records["step7-synthesis"][0]
    selected_id = candidate["paper_card_base"][0]["card_unit_ids"][0]
    card = next(item for item in records["paper-card"] if item["paper_id"] == candidate["paper_card_base"][0]["paper_id"])
    unit = next(unit for section in card["sections"] for unit in section["units"] if unit["unit_id"] == selected_id)
    replacement = next(item for item in records["evidence"] if item["paper_id"] == card["paper_id"] and item["evidence_id"] not in unit["evidence_ids"])
    unit["evidence_ids"] = [replacement["evidence_id"]]
    card["updated_at"] = "2026-01-02T00:00:00Z"

    assert validate_bundle(bundle, actor="stored") == []
    freshness = candidate_freshness(candidate, _entries(bundle))
    assert freshness["state"] == "stale_upstream"
    assert freshness["reasons"] == ["card_newer", "support_expansion_changed"]


def test_unexplained_support_mismatch_remains_integrity_error() -> None:
    bundle = deepcopy(make_bundle("alpha"))
    records = _by_kind(bundle)
    candidate = records["step7-insight"][0]
    candidate["evidence_base"] = [records["evidence"][0]["evidence_id"]]
    candidate["input_snapshot"]["evidence_ids"] = list(candidate["evidence_base"])

    diagnostics = validate_bundle(bundle, actor="stored")

    assert any(item.code == "RKBC-014" and item.record_id == candidate["candidate_id"] for item in diagnostics)


def test_newer_card_can_make_selected_unit_nonfactual_without_corrupting_bundle() -> None:
    bundle = deepcopy(make_bundle("alpha"))
    records = _by_kind(bundle)
    candidate = records["step7-insight"][0]
    card = next(item for item in records["paper-card"] if item["paper_id"] == candidate["paper_card_base"][0]["paper_id"])
    selected_id = candidate["paper_card_base"][0]["card_unit_ids"][0]
    unit = next(unit for section in card["sections"] for unit in section["units"] if unit["unit_id"] == selected_id)
    unit["grounding_status"] = "needs_resolution"
    unit["evidence_ids"] = []
    card["updated_at"] = "2026-01-02T00:00:00Z"

    assert validate_bundle(bundle, actor="stored") == []
    freshness = candidate_freshness(candidate, _entries(bundle))
    assert freshness["state"] == "stale_upstream"
    assert freshness["reasons"] == ["card_newer", "support_expansion_changed"]

    card["updated_at"] = candidate["updated_at"]
    diagnostics = validate_bundle(bundle, actor="stored")
    assert any(item.code == "RKBC-011" and item.record_id == candidate["candidate_id"] for item in diagnostics)


def test_newer_mapping_membership_change_is_stale_but_unversioned_change_is_error() -> None:
    bundle = deepcopy(make_bundle("alpha"))
    records = _by_kind(bundle)
    candidate = records["step7-synthesis"][0]
    mapping = next(item for item in records["question-mapping"] if item["question_id"] == candidate["question_id"])
    link = next(item for item in mapping["paper_links"] if item["paper_id"] == candidate["paper_card_base"][0]["paper_id"])
    card = next(item for item in records["paper-card"] if item["paper_id"] == link["paper_id"])
    replacement = next(unit for section in card["sections"] for unit in section["units"] if unit["grounding_status"] == "revised")
    link["selected_card_unit_ids"] = [replacement["unit_id"]]
    link["evidence_ids"] = list(replacement["evidence_ids"])
    link["boundary_refs"] = list(replacement["boundary_refs"])
    mapping["updated_at"] = "2026-01-02T00:00:00Z"

    assert validate_bundle(bundle, actor="stored") == []
    assert "mapping_membership_changed" in candidate_freshness(candidate, _entries(bundle))["reasons"]

    mapping["updated_at"] = candidate["updated_at"]
    diagnostics = validate_bundle(bundle, actor="stored")
    assert any(item.code == "RKBC-011" and item.record_id == candidate["candidate_id"] for item in diagnostics)


def test_newer_rejected_cross_view_source_is_stale_but_same_timestamp_is_error() -> None:
    bundle = deepcopy(make_bundle("alpha"))
    records = _by_kind(bundle)
    source = records["step7-synthesis"][0]
    cross_view = records["step7-cross-view"][0]
    source["candidate_status"] = "rejected"
    source["rejection_rationale"] = "The synthetic source was revised out."
    source["updated_at"] = "2026-01-02T00:00:00Z"

    assert validate_bundle(bundle, actor="stored") == []
    assert candidate_freshness(cross_view, _entries(bundle))["reasons"][-2:] == [
        "source_view_newer",
        "source_view_stale",
    ]

    source["updated_at"] = cross_view["updated_at"]
    diagnostics = validate_bundle(bundle, actor="stored")
    assert any(item.code == "RKBC-011" and item.record_id == cross_view["candidate_id"] for item in diagnostics)
