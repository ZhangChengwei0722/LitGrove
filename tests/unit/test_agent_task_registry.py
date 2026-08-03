from __future__ import annotations

import pytest

from research_kb.agent_task_registry import SUPPORTED_REGISTRY_VERSIONS, registry_projection, resolve_effective_classes
from research_kb.errors import ResearchKBError


POLICY = {
    "registry_version": "p4a-v1",
    "allowed_content_classes": ["metadata", "parsed_excerpt", "operational_context"],
    "execution_scope": "cloud_allowed",
    "max_prompt_bytes": 262_144,
    "max_result_bytes": 65_536,
}


def test_registry_publishes_route_and_primary_processing_as_available() -> None:
    projection = registry_projection()

    available = [item["task_kind"] for item in projection["task_kinds"] if item["runtime_status"] == "available"]

    assert projection["registry_version"] == "p8-v1"
    assert "p7d-v1" in SUPPORTED_REGISTRY_VERSIONS
    assert "p8-v1" in SUPPORTED_REGISTRY_VERSIONS
    assert available == [
        "document_route_resolution",
        "knowledge_query_report",
        "organization_proposal",
        "primary_semantic_processing",
        "question_screening_criteria_proposal",
        "question_screening_decision_proposal",
        "research_synthesis_drafting",
        "review_semantic_processing",
    ]
    assert projection["embedded_agent_runtime"] is False

    query = next(
        item for item in projection["task_kinds"]
        if item["task_kind"] == "knowledge_query_report"
    )
    assert query == {
        "task_kind": "knowledge_query_report",
        "required_content_classes": [
            "canonical_evidence",
            "operational_context",
            "paper_card_content",
        ],
        "optional_content_classes": [
            "metadata",
            "research_routing_context",
            "review_background",
        ],
        "result_contract": "p5c-knowledge-query-report@1.0",
        "runtime_status": "available",
        "max_items": 4,
        "max_payload_bytes": 1_048_576,
        "max_excerpt_bytes": 0,
        "max_result_bytes": 524_288,
    }


def test_p4c_registry_projection_remains_backward_compatible() -> None:
    projection = registry_projection("p4c-v1")

    assert projection["registry_version"] == "p4c-v1"
    assert [
        item["task_kind"]
        for item in projection["task_kinds"]
        if item["runtime_status"] == "available"
    ] == [
        "document_route_resolution",
        "primary_semantic_processing",
        "review_semantic_processing",
    ]


def test_privacy_intersection_is_explicit_and_non_hierarchical() -> None:
    _, executor, effective = resolve_effective_classes(
        task_kind="document_route_resolution",
        executor_id="codex_cli",
        workspace_policy=POLICY,
        approved_content_classes=["operational_context", "metadata", "parsed_excerpt"],
    )

    assert executor.executor_id == "codex_cli"
    assert effective == ("metadata", "operational_context", "parsed_excerpt")

    with pytest.raises(ResearchKBError, match="lacks a required"):
        resolve_effective_classes(
            task_kind="document_route_resolution",
            executor_id="codex_cli",
            workspace_policy=POLICY,
            approved_content_classes=["metadata", "operational_context"],
        )


def test_absent_policy_deferred_kind_and_local_only_cloud_handoff_fail_closed() -> None:
    with pytest.raises(ResearchKBError, match="policy is absent"):
        resolve_effective_classes(
            task_kind="document_route_resolution",
            executor_id="codex_cli",
            workspace_policy=None,
            approved_content_classes=["metadata", "parsed_excerpt", "operational_context"],
        )


def test_knowledge_query_uses_explicit_non_document_content_classes() -> None:
    policy = {
        **POLICY,
        "registry_version": "p5c-v1",
        "allowed_content_classes": [
            "metadata",
            "parsed_excerpt",
            "canonical_evidence",
            "paper_card_content",
            "review_background",
            "research_routing_context",
            "research_synthesis",
            "operational_context",
            "source_document",
        ],
    }

    definition, _, effective = resolve_effective_classes(
        task_kind="knowledge_query_report",
        executor_id="codex_cli",
        workspace_policy=policy,
        approved_content_classes=policy["allowed_content_classes"],
    )

    assert definition.max_result_bytes == 524_288
    assert effective == (
        "canonical_evidence",
        "metadata",
        "operational_context",
        "paper_card_content",
        "research_routing_context",
        "review_background",
    )

    with pytest.raises(ResearchKBError, match="not available"):
        resolve_effective_classes(
            task_kind="primary_semantic_processing",
            executor_id="codex_cli",
            workspace_policy=POLICY,
            approved_content_classes=["metadata", "parsed_excerpt", "operational_context"],
        )

    local_policy = {**POLICY, "execution_scope": "local_only"}
    with pytest.raises(ResearchKBError, match="local-only"):
        resolve_effective_classes(
            task_kind="document_route_resolution",
            executor_id="claude_code_cli",
            workspace_policy=local_policy,
            approved_content_classes=["metadata", "parsed_excerpt", "operational_context"],
        )
