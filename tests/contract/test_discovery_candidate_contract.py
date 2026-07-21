from __future__ import annotations

import hashlib
import json
from copy import deepcopy

from research_kb.contracts.registry import SchemaRegistry
from research_kb.contracts.validator import validate_bundle, validate_record
from research_kb.identifiers import Namespace, validate_id


def candidate_record() -> dict:
    record = {
        "schema_version": "1.0",
        "candidate_id": "discovery_f0000001-0000-4000-8000-000000000001",
        "workspace_id": "workspace_a1111111-1111-4111-8111-111111111111",
        "domain_profile_id": "domain-alpha",
        "result_key": "doi:10.0000/synthetic.discovery",
        "title": "Targeted degradation delivery in an invented system",
        "authors": ["Alpha Researcher"],
        "first_publication_date": "2026-07-20",
        "journal_or_server": "Invented Journal",
        "doi": "10.0000/synthetic.discovery",
        "paper_type": "article",
        "publication_types": ["Journal Article"],
        "abstract": "Delivery was measured in the fabricated study.",
        "matched_keywords": ["targeted degradation", "delivery"],
        "match_location": "both",
        "discovery_sources": [
            {"provider": "europe-pmc", "source": "MED", "record_id": "SYNTH-DISCOVERY-1"}
        ],
        "full_text_status": "unknown",
        "version_relationship": {"status": "unresolved", "related_doi": None},
        "possible_duplicate_result_keys": [],
        "selection_contexts": [
            {
                "selection_context_id": "",
                "provider": "europe-pmc",
                "provider_api_version": "synthetic-6.9",
                "query": {
                    "date_from": "2026-07-14",
                    "date_until": "2026-07-21",
                    "title_keywords": ["targeted degradation"],
                    "abstract_keywords": ["delivery"],
                    "keyword_mode": "any",
                    "include_preprints": True,
                    "max_results": 15,
                },
                "report_sha256": "b" * 64,
                "target_question_ids": [],
                "selected_at": "2026-07-21T00:00:00Z",
            }
        ],
        "target_question_ids": [],
        "selection_status": "user_selected",
        "source_status": "metadata_only",
        "acquisition_status": "not_started",
        "not_evidence": True,
        "automation_status": "passed_auto_checks",
        "created_at": "2026-07-21T00:00:00Z",
        "updated_at": "2026-07-21T00:00:00Z",
        "fixture_origin": "synthetic_from_scratch",
    }
    identity = {
        "provider": "europe-pmc",
        "result_key": record["result_key"],
        "query": record["selection_contexts"][0]["query"],
        "target_question_ids": [],
    }
    canonical = (json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    record["selection_contexts"][0]["selection_context_id"] = "selection_sha256_" + hashlib.sha256(canonical).hexdigest()
    return record


def test_candidate_schema_and_discovery_namespace_are_public() -> None:
    registry = SchemaRegistry()
    record = candidate_record()

    assert "discovery-candidate" in registry.kinds
    assert validate_record("discovery-candidate", record, actor="stored") == []
    assert validate_id(record["candidate_id"], Namespace.DISCOVERY) == record["candidate_id"]


def test_candidate_fixed_boundaries_cannot_be_relaxed() -> None:
    for field, value in (
        ("selection_status", "automatically_selected"),
        ("source_status", "downloaded"),
        ("acquisition_status", "approved"),
        ("not_evidence", False),
        ("automation_status", "pending"),
    ):
        record = candidate_record()
        record[field] = value
        assert validate_record("discovery-candidate", record, actor="stored")


def test_bundle_requires_candidate_workspace_profile_and_question_references() -> None:
    record = candidate_record()
    question_id = "question_a1111111-1111-4111-8111-111111111111"
    record["target_question_ids"] = [question_id]
    record["selection_contexts"][0]["target_question_ids"] = [question_id]
    bundle = {
        "records": [
            {
                "kind": "workspace",
                "record": {
                    "contract_version": "1.0",
                    "workspace": {
                        "id": record["workspace_id"],
                        "knowledge_root": "./knowledge",
                        "source_roots": [],
                        "local_inbox": "./inbox",
                        "domain_profile": "./domain-profile.yaml",
                    },
                    "runtime": {
                        "path_serialization": "workspace_relative_posix",
                        "default_encoding": "utf-8",
                        "line_ending": "lf",
                    },
                },
            },
            {
                "kind": "domain-profile",
                "record": {
                    "contract_version": "1.0",
                    "domain_profile": {"id": "domain-alpha", "name": "Synthetic", "version": "1.0"},
                    "paper_card_sections": [],
                    "evidence_axes": [],
                    "question_types": [],
                    "terminology": {},
                    "step7_extensions": {},
                },
            },
            {"kind": "discovery-candidate", "record": record},
        ]
    }

    diagnostics = validate_bundle(bundle, actor="stored")

    assert any(item.code == "RKBC-005" and item.json_path.endswith("target_question_ids") for item in diagnostics)


def test_bundle_rejects_duplicate_result_keys_and_context_union_mismatch() -> None:
    first = candidate_record()
    second = deepcopy(first)
    second["candidate_id"] = "discovery_f0000002-0000-4000-8000-000000000002"
    second["selection_contexts"][0]["selection_context_id"] = first["selection_contexts"][0]["selection_context_id"]
    first["target_question_ids"] = ["question_a1111111-1111-4111-8111-111111111111"]

    diagnostics = validate_bundle(
        {"records": [{"kind": "discovery-candidate", "record": first}, {"kind": "discovery-candidate", "record": second}]},
        actor="stored",
    )

    assert any(item.code == "RKBC-004" and item.json_path == "/result_key" for item in diagnostics)
    assert any(item.code == "RKBC-014" and item.json_path == "/target_question_ids" for item in diagnostics)
