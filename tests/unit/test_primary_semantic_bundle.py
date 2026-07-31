from __future__ import annotations

from copy import deepcopy

from research_kb.bundle import records_of_kind
from research_kb.catalog.models import canonical_digest
from research_kb.contracts.validator import validate_bundle, validate_record
from research_kb.primary_bundles import active_primary_entries, primary_bundle_diagnostics
from research_kb.services.question_mapping import mapping_freshness_diagnostics
from tests.fixture_factory import make_bundle


REVISION_ID = "primaryrev_a1111111-1111-4111-8111-111111111111"
TASK_ID = "task_a1111111-1111-4111-8111-111111111111"
PROFILE_ID = "adequacy_a1111111-1111-4111-8111-111111111111"


def _primary_bundle() -> tuple[dict, list[tuple[str, dict]]]:
    fixture = make_bundle("alpha")
    entries = [(item["kind"], item["record"]) for item in fixture["records"]]
    paper = records_of_kind(entries, "registry-paper")[0]
    paper_id = paper["paper_id"]
    card = next(item for item in records_of_kind(entries, "paper-card") if item["paper_id"] == paper_id)
    evidence = [item for item in records_of_kind(entries, "evidence") if item["paper_id"] == paper_id]
    queue = [item for item in records_of_kind(entries, "review-queue") if item["paper_id"] == paper_id]
    page = next(item for item in records_of_kind(entries, "parsed-page") if item["paper_id"] == paper_id)
    revision = {
        "revision_id": REVISION_ID,
        "revision_number": 1,
        "predecessor": None,
        "approval": {
            "task_id": TASK_ID,
            "task_result_digest": "1" * 64,
            "approved_by": "user",
            "approved_at": "2026-01-01T00:00:02Z",
        },
        "input_snapshot": {
            "source_fingerprint": paper["source_fingerprint"],
            "parse_run_id": page["parse_run_id"],
            "parse_output_digest": "2" * 64,
            "adequacy_profiles": [
                {
                    "requested_operation": "basic_paper_card",
                    "profile_id": PROFILE_ID,
                    "profile_digest": "3" * 64,
                }
            ],
        },
        "paper_card": card,
        "evidence": evidence,
        "review_queue": queue,
        "created_at": "2026-01-01T00:00:02Z",
    }
    bundle = {
        "schema_version": "1.0",
        "paper_id": paper_id,
        "active_revision_id": REVISION_ID,
        "revisions": [revision],
        "created_at": "2026-01-01T00:00:02Z",
        "updated_at": "2026-01-01T00:00:02Z",
        "fixture_origin": "synthetic_from_scratch",
    }
    legacy_without_active = [
        (kind, record)
        for kind, record in entries
        if not (
            (kind == "paper-card" and record["paper_id"] == paper_id)
            or (kind == "evidence" and record["paper_id"] == paper_id)
            or (kind == "review-queue" and record["paper_id"] == paper_id)
        )
    ]
    return bundle, legacy_without_active


def test_primary_bundle_schema_and_active_projection_are_complete() -> None:
    bundle, legacy = _primary_bundle()

    assert validate_record("primary-semantic-bundle", bundle, actor="stored") == []
    projected = active_primary_entries(bundle)
    assert [kind for kind, _ in projected].count("paper-card") == 1
    assert [kind for kind, _ in projected].count("evidence") == 2
    assert [kind for kind, _ in projected].count("review-queue") == 2

    diagnostics = validate_bundle(
        {"records": [{"kind": kind, "record": record} for kind, record in [*legacy, ("primary-semantic-bundle", bundle)]]},
        actor="stored",
    )
    assert {
        (item.json_path, item.message.split(" reference:", 1)[0])
        for item in diagnostics
    } == {
        ("/revisions/0/approval/task_id", "unresolved Agent Task"),
        (
            "/revisions/0/input_snapshot/adequacy_profiles/0/profile_id",
            "unresolved Source Adequacy profile",
        ),
    }


def test_primary_revision_chain_preserves_predecessor_digest() -> None:
    bundle, _ = _primary_bundle()
    first = bundle["revisions"][0]
    second = deepcopy(first)
    second["revision_id"] = "primaryrev_a2222222-2222-4222-8222-222222222222"
    second["revision_number"] = 2
    second["predecessor"] = {
        "revision_id": first["revision_id"],
        "revision_digest": canonical_digest(first),
    }
    for section in second["paper_card"]["sections"]:
        section["units"] = []
    second["evidence"] = []
    second["review_queue"] = []
    second["created_at"] = "2026-01-01T00:00:03Z"
    bundle["revisions"].append(second)
    bundle["active_revision_id"] = second["revision_id"]
    bundle["updated_at"] = second["created_at"]

    assert primary_bundle_diagnostics(bundle) == []
    second["predecessor"]["revision_digest"] = "0" * 64
    assert any("predecessor" in item.message for item in primary_bundle_diagnostics(bundle))


def test_primary_bundle_and_legacy_card_cannot_both_be_active() -> None:
    bundle, legacy = _primary_bundle()
    legacy.append(("paper-card", deepcopy(bundle["revisions"][0]["paper_card"])))

    diagnostics = validate_bundle(
        {"records": [{"kind": kind, "record": record} for kind, record in [*legacy, ("primary-semantic-bundle", bundle)]]},
        actor="stored",
    )

    assert any(item.code == "RKBC-013" for item in diagnostics)


def test_historical_primary_children_keep_downstream_refs_resolvable_but_stale() -> None:
    bundle, legacy = _primary_bundle()
    first = bundle["revisions"][0]
    second = deepcopy(first)
    second["revision_id"] = "primaryrev_a2222222-2222-4222-8222-222222222222"
    second["revision_number"] = 2
    second["predecessor"] = {
        "revision_id": first["revision_id"],
        "revision_digest": canonical_digest(first),
    }
    second["approval"] = {
        **second["approval"],
        "task_id": "task_a2222222-2222-4222-8222-222222222222",
        "task_result_digest": "4" * 64,
        "approved_at": "2026-01-02T00:00:00Z",
    }
    for section in second["paper_card"]["sections"]:
        section["units"] = []
    second["paper_card"]["updated_at"] = "2026-01-02T00:00:00Z"
    second["evidence"] = []
    second["review_queue"] = []
    second["created_at"] = "2026-01-02T00:00:00Z"
    bundle["revisions"].append(second)
    bundle["active_revision_id"] = second["revision_id"]
    bundle["updated_at"] = second["created_at"]
    entries = [*legacy, ("primary-semantic-bundle", bundle)]

    diagnostics = validate_bundle(
        {"records": [{"kind": kind, "record": record} for kind, record in entries]},
        actor="stored",
    )
    unresolved_messages = {
        item.message
        for item in diagnostics
        if item.code == "RKBC-003"
    }
    mapping = next(record for kind, record in legacy if kind == "question-mapping")

    assert not any(
        label in message
        for message in unresolved_messages
        for label in ("Card Unit", "evidence", "review queue")
    )
    assert mapping_freshness_diagnostics(mapping, entries)
