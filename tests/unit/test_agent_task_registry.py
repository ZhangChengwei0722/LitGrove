from __future__ import annotations

import pytest

from research_kb.agent_task_registry import registry_projection, resolve_effective_classes
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

    assert projection["registry_version"] == "p4b-v1"
    assert available == ["document_route_resolution", "primary_semantic_processing"]
    assert projection["embedded_agent_runtime"] is False


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
