from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from research_kb.errors import SCHEMA_VALIDATION_FAILED, Diagnostic, ResearchKBError


PRIVACY_REGISTRY_VERSION = "p5c-v1"
SUPPORTED_REGISTRY_VERSIONS = ("p4a-v1", "p4b-v1", "p4c-v1", "p5c-v1")
CONTENT_CLASSES = frozenset(
    {
        "metadata",
        "parsed_excerpt",
        "canonical_evidence",
        "paper_card_content",
        "review_background",
        "research_routing_context",
        "research_synthesis",
        "operational_context",
        "source_document",
    }
)


@dataclass(frozen=True, slots=True)
class TaskKindDefinition:
    task_kind: str
    required_content_classes: frozenset[str]
    optional_content_classes: frozenset[str]
    result_contract: str
    runtime_status: str
    max_items: int
    max_payload_bytes: int
    max_excerpt_bytes: int
    max_result_bytes: int

    def projection(self) -> dict[str, Any]:
        return {
            "task_kind": self.task_kind,
            "required_content_classes": sorted(self.required_content_classes),
            "optional_content_classes": sorted(self.optional_content_classes),
            "result_contract": self.result_contract,
            "runtime_status": self.runtime_status,
            "max_items": self.max_items,
            "max_payload_bytes": self.max_payload_bytes,
            "max_excerpt_bytes": self.max_excerpt_bytes,
            "max_result_bytes": self.max_result_bytes,
        }


@dataclass(frozen=True, slots=True)
class ExecutorDefinition:
    executor_id: str
    execution_scope: str
    allowed_content_classes: frozenset[str]

    def projection(self) -> dict[str, Any]:
        return {
            "executor_id": self.executor_id,
            "execution_scope": self.execution_scope,
            "allowed_content_classes": sorted(self.allowed_content_classes),
            "launch_mode": "external_manual_handoff",
        }


_ROUTE_CLASSES = frozenset({"metadata", "parsed_excerpt", "operational_context"})
_EXECUTOR_CLASSES = frozenset(CONTENT_CLASSES - {"source_document"})

P4A_TASK_KINDS: dict[str, TaskKindDefinition] = {
    "document_route_resolution": TaskKindDefinition(
        "document_route_resolution",
        _ROUTE_CLASSES,
        frozenset({"source_document"}),
        "p4a-document-route-decision@1.0",
        "available",
        128,
        262_144,
        131_072,
        262_144,
    ),
    "source_adequacy_assessment": TaskKindDefinition(
        "source_adequacy_assessment",
        _ROUTE_CLASSES,
        frozenset({"source_document"}),
        "deferred-p4",
        "deferred",
        0,
        0,
        0,
        0,
    ),
    "primary_semantic_processing": TaskKindDefinition(
        "primary_semantic_processing",
        _ROUTE_CLASSES,
        frozenset({"source_document", "research_routing_context"}),
        "deferred-p4b",
        "deferred",
        0,
        0,
        0,
        0,
    ),
    "review_semantic_processing": TaskKindDefinition(
        "review_semantic_processing",
        _ROUTE_CLASSES,
        frozenset({"source_document", "research_routing_context"}),
        "deferred-p4c",
        "deferred",
        0,
        0,
        0,
        0,
    ),
    "question_direction_mapping": TaskKindDefinition(
        "question_direction_mapping",
        frozenset({"paper_card_content", "research_routing_context", "operational_context"}),
        frozenset({"canonical_evidence", "review_background", "metadata"}),
        "deferred-p7",
        "deferred",
        0,
        0,
        0,
        0,
    ),
    "research_synthesis_drafting": TaskKindDefinition(
        "research_synthesis_drafting",
        frozenset(
            {
                "paper_card_content",
                "canonical_evidence",
                "research_routing_context",
                "operational_context",
            }
        ),
        frozenset({"review_background", "research_synthesis", "metadata"}),
        "deferred-p8",
        "deferred",
        0,
        0,
        0,
        0,
    ),
    "semantic_review": TaskKindDefinition(
        "semantic_review",
        frozenset({"operational_context"}),
        frozenset(),
        "deferred-p4b",
        "deferred",
        0,
        0,
        0,
        0,
    ),
}

TASK_KINDS = {
    **P4A_TASK_KINDS,
    "primary_semantic_processing": TaskKindDefinition(
        "primary_semantic_processing",
        _ROUTE_CLASSES,
        frozenset({"research_routing_context"}),
        "p4b-primary-semantic-candidate@1.0",
        "available",
        256,
        1_048_576,
        524_288,
        1_048_576,
    ),
}
P4C_TASK_KINDS = {
    **TASK_KINDS,
    "review_semantic_processing": TaskKindDefinition(
        "review_semantic_processing",
        _ROUTE_CLASSES,
        frozenset({"review_background"}),
        "p4c-review-semantic-candidate@1.0",
        "available",
        256,
        1_048_576,
        524_288,
        1_048_576,
    ),
}
P5C_TASK_KINDS = {
    **P4C_TASK_KINDS,
    "knowledge_query_report": TaskKindDefinition(
        "knowledge_query_report",
        frozenset({"paper_card_content", "canonical_evidence", "operational_context"}),
        frozenset({"metadata", "review_background", "research_routing_context"}),
        "p5c-knowledge-query-report@1.0",
        "available",
        4,
        1_048_576,
        0,
        524_288,
    ),
}
REGISTRY_TASK_KINDS = {
    "p4a-v1": P4A_TASK_KINDS,
    "p4b-v1": TASK_KINDS,
    "p4c-v1": P4C_TASK_KINDS,
    "p5c-v1": P5C_TASK_KINDS,
}

EXECUTORS: dict[str, ExecutorDefinition] = {
    "codex_cli": ExecutorDefinition("codex_cli", "cloud_allowed", _EXECUTOR_CLASSES),
    "claude_code_cli": ExecutorDefinition(
        "claude_code_cli", "cloud_allowed", _EXECUTOR_CLASSES
    ),
}


def registry_projection(registry_version: str | None = None) -> dict[str, Any]:
    selected_version = registry_version or PRIVACY_REGISTRY_VERSION
    definitions = REGISTRY_TASK_KINDS.get(selected_version)
    if definitions is None:
        raise _registry_error("Agent Task registry version is unsupported", "/registry_version")
    return {
        "status": "success",
        "registry_version": selected_version,
        "content_classes": sorted(CONTENT_CLASSES),
        "task_kinds": [definitions[key].projection() for key in sorted(definitions)],
        "executors": [EXECUTORS[key].projection() for key in sorted(EXECUTORS)],
        "embedded_agent_runtime": False,
    }


def resolve_effective_classes(
    *,
    task_kind: str,
    executor_id: str,
    workspace_policy: dict[str, Any] | None,
    approved_content_classes: object,
) -> tuple[TaskKindDefinition, ExecutorDefinition, tuple[str, ...]]:
    if workspace_policy is None:
        raise _registry_error("workspace Agent policy is absent; Agent Tasks are denied", "/agent_policy")
    registry_version = workspace_policy.get("registry_version")
    definitions = REGISTRY_TASK_KINDS.get(registry_version)
    if definitions is None:
        raise _registry_error("workspace Agent policy registry version is unsupported", "/agent_policy/registry_version")
    definition = definitions.get(task_kind)
    if definition is None:
        raise _registry_error("Agent Task kind is not registered", "/task_kind")
    if definition.runtime_status != "available":
        raise _registry_error("Agent Task kind is registered but not available in this runtime", "/task_kind")
    executor = EXECUTORS.get(executor_id)
    if executor is None:
        raise _registry_error("Agent executor profile is not registered", "/executor_id")
    if workspace_policy.get("execution_scope") == "local_only" and executor.execution_scope != "local_only":
        raise _registry_error("local-only Agent Task cannot use a cloud executor", "/executor_id")
    if not isinstance(approved_content_classes, list) or not all(
        isinstance(item, str) for item in approved_content_classes
    ):
        raise _registry_error("approved content classes must be a string array", "/approved_content_classes")
    approved = set(approved_content_classes)
    if len(approved) != len(approved_content_classes):
        raise _registry_error("approved content classes must be unique", "/approved_content_classes")
    unknown = approved - CONTENT_CLASSES
    if unknown:
        raise _registry_error("approved content class is not registered", "/approved_content_classes")
    workspace_classes = set(workspace_policy.get("allowed_content_classes", []))
    allowed_for_kind = definition.required_content_classes | definition.optional_content_classes
    effective = workspace_classes & approved & executor.allowed_content_classes & allowed_for_kind
    if not definition.required_content_classes.issubset(effective):
        raise _registry_error("effective privacy scope lacks a required content class", "/approved_content_classes")
    return definition, executor, tuple(sorted(effective))


def _registry_error(message: str, path: str) -> ResearchKBError:
    return ResearchKBError(
        Diagnostic(SCHEMA_VALIDATION_FAILED, "agent-task-registry", None, path, message)
    )


__all__ = [
    "CONTENT_CLASSES",
    "EXECUTORS",
    "PRIVACY_REGISTRY_VERSION",
    "SUPPORTED_REGISTRY_VERSIONS",
    "REGISTRY_TASK_KINDS",
    "TASK_KINDS",
    "ExecutorDefinition",
    "TaskKindDefinition",
    "registry_projection",
    "resolve_effective_classes",
]
