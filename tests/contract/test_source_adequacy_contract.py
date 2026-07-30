from copy import deepcopy

from research_kb.contracts.validator import validate_record


def _profile() -> dict:
    capability = {
        "status": "yes",
        "reasons": ["The synthetic parse satisfies the declared check."],
        "authority_layers": ["machine"],
    }
    return {
        "schema_version": "1.0",
        "profile_id": "adequacy_a0000001-0000-4000-8000-000000000001",
        "basis_profile": None,
        "workspace_id": "workspace_a0000001-0000-4000-8000-000000000001",
        "paper_id": "paper_a0000001-0000-4000-8000-000000000001",
        "job_id": "job_a0000001-0000-4000-8000-000000000001",
        "requested_operation": "basic_paper_card",
        "operation_registry_version": "p3c-v1",
        "source_snapshots": [
            {
                "source_asset_id": None,
                "source_asset_state_id": None,
                "role": "main_pdf",
                "source_ref": {"root_id": "synthetic-sources", "relative_path": "paper.txt"},
                "manifestation_id": f"sha256:{'a' * 64}",
                "availability": "available",
            }
        ],
        "parse_snapshot": {
            "active_parse_ref": "event_a0000001-0000-4000-8000-000000000001",
            "parser_identity": {
                "adapter_id": "synthetic-text",
                "version": "1.0",
                "profile_digest": "b" * 64,
            },
            "output_bundle_digest": "c" * 64,
            "page_count": 1,
        },
        "assessment_rule_version": "p3c-v1",
        "assessed_by": "cli",
        "assessed_at": "2026-01-01T00:00:00Z",
        "machine_observations": [
            {
                "code": "source_digest_matches",
                "status": "pass",
                "hard_failure": False,
                "affected_capabilities": ["basic_paper_understanding"],
                "reason": "The synthetic source digest matches.",
            }
        ],
        "agent_assessment": None,
        "user_decision": None,
        "capabilities": {
            "basic_paper_understanding": deepcopy(capability),
            "complete_reading": deepcopy(capability),
            "continuous_text_citation": deepcopy(capability),
            "figure_table_evidence_extraction": deepcopy(capability),
            "formula_or_layout_sensitive_analysis": deepcopy(capability),
            "supplementary_material_analysis": deepcopy(capability),
        },
        "known_limitations": [],
        "recommended_actions": [],
        "fixture_origin": "synthetic_from_scratch",
    }


def test_source_adequacy_profile_contract_accepts_closed_synthetic_record() -> None:
    assert validate_record("source-adequacy-profile", _profile(), actor="stored") == []


def test_source_adequacy_profile_rejects_unpaired_source_asset_refs() -> None:
    record = _profile()
    record["source_snapshots"][0]["source_asset_id"] = (
        "sourceasset_a0000001-0000-4000-8000-000000000001"
    )
    diagnostics = validate_record("source-adequacy-profile", record, actor="stored")
    assert diagnostics
    assert diagnostics[0].json_path.startswith("/source_snapshots/0")


def test_source_adequacy_profile_rejects_unknown_operation_and_capability() -> None:
    record = _profile()
    record["requested_operation"] = "invented_operation"
    record["machine_observations"][0]["affected_capabilities"] = ["invented_capability"]
    diagnostics = validate_record("source-adequacy-profile", record, actor="stored")
    assert len(diagnostics) == 2


def test_source_adequacy_profile_rejects_agent_as_direct_assessor() -> None:
    record = _profile()
    record["assessed_by"] = "agent"
    diagnostics = validate_record("source-adequacy-profile", record, actor="stored")
    assert diagnostics
    assert diagnostics[0].json_path == "/assessed_by"
