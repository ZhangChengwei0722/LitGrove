from __future__ import annotations

import json
from importlib import resources
from pathlib import Path
from typing import Any

from referencing import Registry, Resource

from research_kb.contracts.versions import SUPPORTED_VERSION
from research_kb.errors import UNKNOWN_SCHEMA_KIND, Diagnostic, ResearchKBError


SCHEMA_FILES: dict[str, str] = {
    "definitions": "definitions.schema.json",
    "workspace": "workspace.schema.json",
    "workspace-marker": "workspace-marker.schema.json",
    "compatibility-difference": "compatibility-difference.schema.json",
    "compatibility-report": "compatibility-report.schema.json",
    "domain-profile": "domain-profile.schema.json",
    "registry-paper": "registry-paper.schema.json",
    "parsed-page": "parsed-page.schema.json",
    "paper-card": "paper-card.schema.json",
    "evidence": "evidence.schema.json",
    "review-queue": "review-queue.schema.json",
    "review-memory": "review-memory.schema.json",
    "discovery-candidate": "discovery-candidate.schema.json",
    "question-mapping": "question-mapping.schema.json",
    "organization-link": "organization-link.schema.json",
    "direction": "direction.schema.json",
    "direction-bundle": "direction-bundle.schema.json",
    "field-map-entry": "field-map-entry.schema.json",
    "field-map-bundle": "field-map-bundle.schema.json",
    "question-revision-bundle": "question-revision-bundle.schema.json",
    "step7-common": "step7-common.schema.json",
    "step7-synthesis": "step7-synthesis.schema.json",
    "step7-review-angle": "step7-review-angle.schema.json",
    "step7-insight": "step7-insight.schema.json",
    "step7-cross-view": "step7-cross-view.schema.json",
    "process-event": "process-event.schema.json",
    "guardian-report": "guardian-report.schema.json",
    "pipeline-job-state": "pipeline-job-state.schema.json",
    "guardian-finding-disposition": "guardian-finding-disposition.schema.json",
    "source-asset-state": "source-asset-state.schema.json",
    "registry-identity-correction": "registry-identity-correction.schema.json",
    "source-adequacy-profile": "source-adequacy-profile.schema.json",
    "agent-task-state": "agent-task-state.schema.json",
    "document-route-decision": "document-route-decision.schema.json",
    "primary-semantic-bundle": "primary-semantic-bundle.schema.json",
    "primary-semantic-candidate": "primary-semantic-candidate.schema.json",
    "review-semantic-bundle": "review-semantic-bundle.schema.json",
    "review-semantic-candidate": "review-semantic-candidate.schema.json",
    "knowledge-query-report": "knowledge-query-report.schema.json",
    "mutation-request": "mutation-request.schema.json",
    "transaction-journal": "transaction-journal.schema.json",
}


class SchemaRegistry:
    def __init__(self, schema_root: Path | None = None):
        self.schema_root = schema_root
        self._schemas: dict[str, dict[str, Any]] | None = None

    @property
    def kinds(self) -> tuple[str, ...]:
        return tuple(kind for kind in SCHEMA_FILES if kind not in {"definitions", "step7-common"})

    def schema(self, kind: str) -> dict[str, Any]:
        if kind not in SCHEMA_FILES:
            raise ResearchKBError(
                Diagnostic(UNKNOWN_SCHEMA_KIND, kind, None, "", f"unknown schema kind: {kind}")
            )
        return self.schemas()[kind]

    def schemas(self) -> dict[str, dict[str, Any]]:
        if self._schemas is None:
            self._schemas = {kind: json.loads(self._read_text(filename)) for kind, filename in SCHEMA_FILES.items()}
        return self._schemas

    def referencing_registry(self) -> Registry:
        registry = Registry()
        for schema in self.schemas().values():
            registry = registry.with_resource(schema["$id"], Resource.from_contents(schema))
        return registry

    def _read_text(self, filename: str) -> str:
        if self.schema_root is not None:
            return (self.schema_root / SUPPORTED_VERSION / filename).read_text(encoding="utf-8")
        source_root = Path(__file__).resolve().parents[3] / "schemas" / SUPPORTED_VERSION
        if source_root.is_dir():
            return (source_root / filename).read_text(encoding="utf-8")
        packaged = resources.files("research_kb").joinpath("_data", "schemas", SUPPORTED_VERSION, filename)
        return packaged.read_text(encoding="utf-8")
