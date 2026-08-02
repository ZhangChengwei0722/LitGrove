from copy import deepcopy

import pytest

from research_kb.contracts.registry import SchemaRegistry
from research_kb.contracts.validator import validate_record


PAPER_ID = "paper_11111111-1111-4111-8111-111111111111"
UNIT_ID = "unit_22222222-2222-4222-8222-222222222222"
EVIDENCE_ID = "evidence_33333333-3333-4333-8333-333333333333"
LINK_ID = "orglink_44444444-4444-4444-8444-444444444444"
DIRECTION_ID = "direction_55555555-5555-4555-8555-555555555555"
ORGANIZATION_REVISION_ID = "orgrev_66666666-6666-4666-8666-666666666666"
FIELD_MAP_ID = "fieldmap_77777777-7777-4777-8777-777777777777"
QUESTION_ID = "question_88888888-8888-4888-8888-888888888888"
QUESTION_REVISION_ID = "questionrev_99999999-9999-4999-8999-999999999999"
TASK_ID = "task_aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
QUESTION_LINK_ID = "qlink_bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
QUESTION_BACKGROUND_ID = "qbackground_cccccccc-cccc-4ccc-8ccc-cccccccccccc"
TIMESTAMP = "2026-08-03T00:00:00Z"
DIGEST = "d" * 64


def _approval() -> dict:
    return {
        "task_id": TASK_ID,
        "task_result_digest": DIGEST,
        "approved_by": "user",
        "approved_at": TIMESTAMP,
        "origin": "user_approved_agent_proposal",
    }


def _factual_link() -> dict:
    return {
        "schema_version": "1.0",
        "organization_link_id": LINK_ID,
        "source_kind": "primary_unit",
        "paper_id": PAPER_ID,
        "source_unit_id": UNIT_ID,
        "source_revision_id": "primaryrev_eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
        "role": "factual_example",
        "rationale": "Synthetic factual example.",
        "evidence_ids": [EVIDENCE_ID],
        "background_only": False,
        "can_enter_canonical_evidence": False,
        "not_fact": False,
    }


def _direction() -> dict:
    return {
        "schema_version": "1.0",
        "direction_id": DIRECTION_ID,
        "name": "Synthetic direction",
        "scope": "Synthetic scope.",
        "status": "active",
        "links": [_factual_link()],
        "gap_notes": [],
    }


def _field_map_entry() -> dict:
    return {
        "schema_version": "1.0",
        "field_map_entry_id": FIELD_MAP_ID,
        "title": "Synthetic field entry",
        "entry_type": "mechanism",
        "definition": "Synthetic field definition.",
        "status": "active",
        "consensus_level": "review_plus_primary_examples",
        "direction_refs": [
            {"direction_id": DIRECTION_ID, "direction_revision_id": ORGANIZATION_REVISION_ID}
        ],
        "links": [_factual_link()],
        "aspect_notes": [],
    }


def _question_mapping() -> dict:
    return {
        "schema_version": "1.0",
        "question_id": QUESTION_ID,
        "question_text": "What is the synthetic effect?",
        "scope": "Synthetic scope.",
        "domain_profile_id": "domain-alpha",
        "paper_links": [
            {
                "question_link_id": QUESTION_LINK_ID,
                "paper_id": PAPER_ID,
                "selected_card_unit_ids": [UNIT_ID],
                "role_in_question": "support",
                "relevance_rationale": "Synthetic rationale.",
                "evidence_ids": [EVIDENCE_ID],
                "boundary_refs": [],
            }
        ],
        "mapping_status": "ai_checked",
        "created_at": TIMESTAMP,
        "updated_at": TIMESTAMP,
    }


def _records() -> dict[str, dict]:
    direction = _direction()
    field_map_entry = _field_map_entry()
    background_link = _factual_link()
    background_link.update(
        {
            "organization_link_id": "orglink_dddddddd-dddd-4ddd-8ddd-dddddddddddd",
            "role": "question_background",
            "evidence_ids": [],
            "background_only": True,
            "not_fact": True,
        }
    )
    return {
        "organization-link": _factual_link(),
        "direction": direction,
        "direction-bundle": {
            "schema_version": "1.0",
            "direction_id": DIRECTION_ID,
            "active_revision_id": ORGANIZATION_REVISION_ID,
            "revisions": [
                {
                    "revision_id": ORGANIZATION_REVISION_ID,
                    "revision_number": 1,
                    "predecessor": None,
                    "content_digest": DIGEST,
                    "approval": _approval(),
                    "direction": direction,
                    "created_at": TIMESTAMP,
                }
            ],
            "created_at": TIMESTAMP,
            "updated_at": TIMESTAMP,
        },
        "field-map-entry": field_map_entry,
        "field-map-bundle": {
            "schema_version": "1.0",
            "field_map_entry_id": FIELD_MAP_ID,
            "active_revision_id": ORGANIZATION_REVISION_ID,
            "revisions": [
                {
                    "revision_id": ORGANIZATION_REVISION_ID,
                    "revision_number": 1,
                    "predecessor": None,
                    "content_digest": DIGEST,
                    "approval": _approval(),
                    "field_map_entry": field_map_entry,
                    "created_at": TIMESTAMP,
                }
            ],
            "created_at": TIMESTAMP,
            "updated_at": TIMESTAMP,
        },
        "question-revision-bundle": {
            "schema_version": "1.0",
            "question_id": QUESTION_ID,
            "active_revision_id": QUESTION_REVISION_ID,
            "revisions": [
                {
                    "revision_id": QUESTION_REVISION_ID,
                    "revision_number": 1,
                    "predecessor": None,
                    "content_digest": DIGEST,
                    "approval": _approval(),
                    "question_mapping": _question_mapping(),
                    "background_links": [
                        {"question_background_id": QUESTION_BACKGROUND_ID, "link": background_link}
                    ],
                    "created_at": TIMESTAMP,
                }
            ],
            "created_at": TIMESTAMP,
            "updated_at": TIMESTAMP,
        },
    }


def test_p7a_research_organization_contracts_are_registered() -> None:
    registry = SchemaRegistry()

    expected = {
        "direction",
        "direction-bundle",
        "field-map-entry",
        "field-map-bundle",
        "organization-link",
        "question-revision-bundle",
    }

    assert expected <= set(registry.kinds)
    for kind in expected:
        assert registry.schema(kind)["$id"] == f"urn:research-kb:schema:1.0:{kind}"


@pytest.mark.parametrize("kind", sorted(_records()))
def test_p7a_contracts_accept_closed_synthetic_records(kind: str) -> None:
    assert validate_record(kind, _records()[kind], actor="stored") == []


@pytest.mark.parametrize("kind", sorted(_records()))
def test_p7a_contracts_reject_unknown_top_level_fields(kind: str) -> None:
    record = deepcopy(_records()[kind])
    record["unexpected"] = True

    assert validate_record(kind, record, actor="stored")


def test_review_organization_link_cannot_carry_evidence() -> None:
    record = _factual_link()
    record.update(
        {
            "source_kind": "review_unit",
            "source_unit_id": "reviewunit_eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
            "source_revision_id": "reviewrev_ffffffff-ffff-4fff-8fff-ffffffffffff",
            "role": "background_context",
            "background_only": True,
            "not_fact": True,
        }
    )

    diagnostics = validate_record("organization-link", record, actor="stored")

    assert diagnostics
