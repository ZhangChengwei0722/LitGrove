from pathlib import Path
import json
import re

import yaml

from research_kb.services.capability import CapabilityService


ROOT = Path(__file__).resolve().parents[2]
SKILL_ROOT = ROOT / "skills" / "research-kb"
EXPECTED_FILES = {
    "SKILL.md",
    "agents/openai.yaml",
    "references/authority-and-failure-boundaries.md",
    "references/cli-contract.md",
    "references/discovery-workflow.md",
    "references/local-intake-workflow.md",
    "references/knowledge-query-and-step7-workflow.md",
    "references/manuscript-audit-workflow.md",
    "references/review-intake-workflow.md",
    "references/task" "-report-contract.md",
}


def _skill_parts() -> tuple[dict[str, str], str]:
    content = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    _, frontmatter, body = content.split("---", 2)
    metadata = yaml.safe_load(frontmatter)
    assert isinstance(metadata, dict)
    return metadata, body


def _package_text() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(SKILL_ROOT.rglob("*"))
        if path.is_file()
    )


def test_portable_skill_has_exact_minimal_file_set() -> None:
    actual = {
        path.relative_to(SKILL_ROOT).as_posix()
        for path in SKILL_ROOT.rglob("*")
        if path.is_file()
    }

    assert actual == EXPECTED_FILES
    assert not (SKILL_ROOT / "scripts").exists()
    assert not (SKILL_ROOT / "assets").exists()


def test_skill_frontmatter_and_progressive_disclosure_contract() -> None:
    metadata, body = _skill_parts()

    assert set(metadata) == {"name", "description"}
    assert metadata["name"] == "research-kb"
    description = metadata["description"].lower()
    for trigger in (
        "workspace",
        "local",
        "primary",
        "pdf",
        "resume",
        "status",
        "evidence",
        "review",
        "question",
        "guardian",
        "query",
        "comparison",
        "trace-back",
        "step 7",
        "discovery",
        "manuscript",
    ):
        assert trigger in description
    assert len((SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8").splitlines()) < 500
    for reference in sorted(path for path in EXPECTED_FILES if path.startswith("references/")):
        target = reference.replace("task" + "-", "task%2D")
        assert f"]({target})" in body


def test_openai_metadata_is_exact_and_mentions_skill() -> None:
    metadata = yaml.safe_load((SKILL_ROOT / "agents" / "openai.yaml").read_text(encoding="utf-8"))

    assert metadata == {
        "interface": {
            "display_name": "Research KB",
            "short_description": "Discover, ingest, query, and audit traceable research",
            "default_prompt": (
                "Use $research-kb to discover papers, ingest local research, audit an exact manuscript against explicit criteria, answer traceable knowledge-base questions, or maintain Step 7 candidates."
            ),
        }
    }


def test_skill_is_generic_private_safe_and_has_no_hidden_fallback() -> None:
    text = _package_text()

    for forbidden in (
        "E" + ":\\",
        "C:" + "\\Users",
        "Q" + "001",
        "T" + "PD",
        "doi.org/",
        "research-kb ingest",
        "research-kb workspace create",
    ):
        assert forbidden not in text
    assert re.search(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+", text, flags=re.IGNORECASE) is None
    assert "Do not parse workspace or domain-profile configuration" in text
    assert "Do not read canonical JSON or JSONL files directly" in text
    assert "Review Memory is background-only" in text
    assert "can_enter_canonical_evidence: false" in text
    assert "review context" in text
    assert "Field Map integration" in text
    assert "Review Unit Question Mapping" in text
    assert "Review Memory is background-only" in text
    assert "cannot become primary support" in text


def test_skill_required_read_commands_match_public_capability() -> None:
    capability = CapabilityService(pdfplumber_probe=lambda: "0.11.10").show()
    required_reads = {
        "capability show",
        "guardian check",
        "intake inspect",
        "intake inspect-acquired",
        "paper context",
        "paper status",
        "parse show",
        "question list",
        "question render",
        "question show",
        "review context",
        "step7 context",
        "step7 render",
        "manuscript inspect",
        "discovery list",
        "discovery resolve",
        "discovery search",
        "discovery show",
    }

    assert required_reads <= set(capability["read_commands"])
    assert capability["features"]["real_pdf_parse"] is True
    assert capability["features"]["review_runtime"] is True
    assert capability["features"]["step7_runtime"] is True
    assert capability["features"]["on_demand_discovery"] is True
    assert capability["features"]["approved_discovery_candidate_handoff"] is True
    assert capability["features"]["explicit_oa_acquisition"] is True
    assert capability["features"]["legal_oa_resolution"] is True
    assert capability["features"]["manuscript_projection"] is True
    assert any(
        adapter["adapter"] == "pdfplumber-text-flow" and adapter["availability"] == "available"
        for adapter in capability["parse_adapters"]
    )


def test_skill_routes_new_scientific_parses_through_text_flow_adapter() -> None:
    skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    local = (SKILL_ROOT / "references" / "local-intake-workflow.md").read_text(encoding="utf-8")
    review = (SKILL_ROOT / "references" / "review-intake-workflow.md").read_text(encoding="utf-8")
    cli = (SKILL_ROOT / "references" / "cli-contract.md").read_text(encoding="utf-8")

    assert "pdfplumber-text-flow" in skill
    assert "parse run --adapter pdfplumber-text-flow" in local
    assert "pdfplumber-text-flow" in review
    assert "--adapter pdfplumber-text-flow" in cli
    assert "does not guarantee layout-correct reading order" in cli


def test_cli_reference_contains_minimal_stdin_promotion_envelopes() -> None:
    text = (SKILL_ROOT / "references" / "cli-contract.md").read_text(encoding="utf-8")

    for record_kind in (
        "evidence",
        "review-queue",
        "paper-card",
        "review-memory",
        "question-mapping",
        "step7-synthesis",
        "step7-review-angle",
        "step7-insight",
        "step7-cross-view",
    ):
        assert f'"record_kind": "{record_kind}"' in text
    for required_field in (
        '"target_record_id": null',
        '"context":',
        '"payload":',
        '"what_it_does_not_support":',
        '"selected_card_unit_ids":',
        '"question_origin": "user_supplied"',
    ):
        assert required_field in text
    assert "Do not submit CLI-owned IDs" in text
    assert "Omit `fixture_origin` in real tasks" in text
    for statement_type in (
        "background",
        "method_description",
        "reported_result",
        "author_conclusion",
        "limitation",
        "future_direction",
        "interpretation",
    ):
        assert f"| `{statement_type}` |" in text
    assert "There is no Card `other` type" in text
    assert '"question_origin": "existing_question"' in text
    assert "Do not submit `candidate_id`" in text
    assert "Core derives `evidence_base`" in text


def test_query_and_step7_workflow_separates_read_only_and_persistent_intent() -> None:
    text = (
        SKILL_ROOT / "references" / "knowledge-query-and-step7-workflow.md"
    ).read_text(encoding="utf-8")

    for required in (
        "ephemeral_query",
        "explicit_step7_maintenance",
        "full_workflow_step7_refresh",
        "persistent_writes: 0",
        "paper context",
        "question show",
        "step7 context",
        "step7 render",
        "record promote",
        "grounded",
        "revised",
        "exact rerun",
        "near-duplicate",
        "report-only",
        "cannot become primary support",
    ):
        assert required in text

    assert "If persistence intent is ambiguous, use `ephemeral_query`" in text
    assert "For query and maintenance, call `workspace init --dry-run` only" in text
    assert "Never call operational `workspace init` from these modes" in text
    assert "Do not write Step 7 JSONL directly" in text
    assert "Review queue records are boundaries, not support" in text
    assert "Do not invent section labels" in text


def test_step7_reference_envelopes_are_valid_json_and_exclude_core_owned_fields() -> None:
    text = (SKILL_ROOT / "references" / "cli-contract.md").read_text(encoding="utf-8")
    documents = [
        json.loads(block)
        for block in re.findall(r"```json\n(.*?)\n```", text, flags=re.DOTALL)
    ]
    step7 = {
        document["record_kind"]: document
        for document in documents
        if str(document.get("record_kind", "")).startswith("step7-")
    }

    assert set(step7) == {
        "step7-synthesis",
        "step7-review-angle",
        "step7-insight",
        "step7-cross-view",
    }
    core_owned = {
        "candidate_id",
        "type",
        "evidence_base",
        "review_queue_refs",
        "input_snapshot",
        "not_fact",
        "review_status",
        "automation_status",
        "created_at",
        "updated_at",
    }
    for document in step7.values():
        assert document["context"] == {
            "paper_id": None,
            "question_origin": "existing_question",
        }
        assert core_owned.isdisjoint(document["payload"])
    assert len(step7["step7-synthesis"]["payload"]["paper_card_base"]) >= 2


def test_skill_routes_modes_before_mutation_and_preserves_query_ephemerality() -> None:
    _, body = _skill_parts()

    for mode in (
        "local_intake",
        "on_demand_discovery",
        "explicit_oa_acquisition",
        "acquired_candidate_intake",
        "ephemeral_query",
        "explicit_step7_maintenance",
        "full_workflow_step7_refresh",
        "manuscript_projection",
        "manuscript_audit",
    ):
        assert f"`{mode}`" in body
    assert "Classify the invocation mode before any mutation" in body
    assert "Ordinary knowledge queries never persist" in body
    assert "Allow only `already_present` plus the planned `acquire_workspace_lock` action" in body
    assert "Exact reruns write nothing" in body


def test_manuscript_projection_mode_stops_before_semantic_audit() -> None:
    _, body = _skill_parts()
    text = _package_text()

    for required in (
        "exact user-supplied DOCX or PDF",
        "manuscript inspect",
        "persistent_writes: 0",
        "coverage_limits",
        "source_fingerprint",
    ):
        assert required in text
    assert "Stop after the projection report" in body
    assert "Do not perform or claim semantic claim extraction, criteria evaluation, evidence matching or rewriting" in text


def test_manuscript_audit_requires_explicit_criteria_bounded_scope_and_evidence() -> None:
    _, body = _skill_parts()
    workflow = (
        SKILL_ROOT / "references" / "manuscript-audit-workflow.md"
    ).read_text(encoding="utf-8")
    report = (SKILL_ROOT / "references" / ("task" + "-report-contract.md")).read_text(
        encoding="utf-8"
    )
    protocol = (ROOT / "agent_protocol" / "README.md").read_text(encoding="utf-8")

    assert "`manuscript_audit`" in body
    assert "before `manuscript inspect`" in workflow
    assert "must not silently add" in workflow
    assert "task_resolved" in workflow
    assert "must not infer a broad corpus" in workflow
    assert "resolution_basis" in workflow
    assert "exact_slice" in workflow
    assert "unit_only" in workflow
    assert "canonical Evidence IDs" in workflow
    assert "Review Memory, review queue and Step 7" in workflow
    assert "persistent_writes: 0" in workflow
    assert "rewritten prose" in workflow
    assert "manuscript_audit:" in report
    assert "does_not_meet_in_checked_scope" in report
    assert "For M3D-1" in protocol


def test_discovery_workflow_separates_zero_write_search_from_explicit_handoff() -> None:
    text = (SKILL_ROOT / "references" / "discovery-workflow.md").read_text(encoding="utf-8")

    for required in (
        "on_demand_discovery",
        "date_from",
        "date_until",
        "title_keywords",
        "abstract_keywords",
        "include_preprints",
        "max_results",
        "0-15",
        "persistent_writes: 0",
        "discovery search",
        "discovery select",
        "discovery resolve",
        "discovery acquire",
        "--actor user",
        "complete report",
        "metadata only",
        "report-only",
    ):
        assert required in text
    assert "Do not pad a zero-result search" in text
    assert "Do not infer approval" in text
    assert "user_selected" in text
    assert "auto_acquisition_eligible" in text
    assert "legal_oa_resolution" in text
    assert "explicit_oa_acquisition" in text
    assert "acquired_candidate_intake" in text
    assert "intake inspect-acquired" in text
    assert "--actor user" in text
    assert "Stop before `intake inspect-acquired`" in text


def test_acquired_candidate_intake_resumes_existing_workflow_only_in_later_task() -> None:
    _, skill = _skill_parts()
    local = (SKILL_ROOT / "references" / "local-intake-workflow.md").read_text(encoding="utf-8")
    discovery = (SKILL_ROOT / "references" / "discovery-workflow.md").read_text(encoding="utf-8")
    authority = (
        SKILL_ROOT / "references" / "authority-and-failure-boundaries.md"
    ).read_text(encoding="utf-8")
    report = (SKILL_ROOT / "references" / ("task" + "-report-contract.md")).read_text(
        encoding="utf-8"
    )

    for required in (
        "`registry_only`",
        "same Parse and mutually exclusive primary/review route",
        "Do not infer intake authority from `acquired` alone",
    ):
        assert required in skill
    assert "provider `paper_type` is metadata only" in local
    assert "otherwise resume the existing local intake workflow" in discovery
    assert "This later task does not weaken the acquisition stop above" in discovery
    assert "this never changes the acquisition command's stop boundary" in authority
    for field in (
        "requested_workflow_depth:",
        "continuation_outcome:",
        "completed_stage:",
    ):
        assert field in report
    assert "parse_started: false" not in report
    assert "bounded acquired-candidate route stops after Registry" not in _package_text()


def test_review_workflow_is_actionable_and_keeps_downstream_boundaries() -> None:
    text = (SKILL_ROOT / "references" / "review-intake-workflow.md").read_text(encoding="utf-8")

    for required in (
        "narrative_review",
        "systematic_review",
        "scoping_review",
        "meta_analysis",
        "perspective_or_commentary",
        "review_objective_scope",
        "primary_leads_reuse",
        "review context",
        "background_only: true",
        "locator: null",
        "zero Units",
        "Field Map",
        "Step 7",
    ):
        assert required in text
    assert "Do not summarize a review for completeness" in text


def test_task_report_defines_new_and_no_change_completion_outcomes() -> None:
    authority = (SKILL_ROOT / "references" / "authority-and-failure-boundaries.md").read_text(encoding="utf-8")
    report = (SKILL_ROOT / "references" / ("task" + "-report-contract.md")).read_text(encoding="utf-8")

    assert "| `completed` |" in authority
    assert "| `completed_no_change` |" in authority
    assert "Use `completed` when this run newly reaches Guardian" in report
    assert "persistent_writes: 0" in report
    assert "invocation_mode:" in report
    assert "step7_maintenance:" in report
    assert "discovery_candidate_handoff:" in report
    assert "oa_acquisition:" in report
    assert "acquired_candidate_intake:" in report
    assert "requested_workflow_depth:" in report
    assert "continuation_outcome:" in report
