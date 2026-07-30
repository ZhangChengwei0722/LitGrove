from __future__ import annotations

from typing import Any

from research_kb.errors import INVALID_AUTHORITY, UNRESOLVED_REFERENCE, Diagnostic, ResearchKBError
from research_kb.identifiers import Namespace, validate_id
from research_kb.pipeline_jobs import TERMINAL_STATUSES, current_pipeline_states
from research_kb.storage.json_io import read_jsonl
from research_kb.workspace import WorkspaceLayout


def require_job_authority(
    layout: WorkspaceLayout,
    job_id: str,
    operation: str,
) -> dict[str, Any]:
    job_id = validate_id(job_id, Namespace.JOB)
    states = read_jsonl(
        layout.pipeline_jobs_path,
        record_kind="pipeline-job-state",
        id_field="state_id",
    )
    current = next(
        (state for state in current_pipeline_states(states) if state["job_id"] == job_id),
        None,
    )
    if current is None:
        raise ResearchKBError(
            Diagnostic(UNRESOLVED_REFERENCE, "pipeline-job-state", job_id, "/job_id", "Pipeline Job does not exist")
        )
    if current["status"] in TERMINAL_STATUSES:
        raise ResearchKBError(
            Diagnostic(INVALID_AUTHORITY, "pipeline-job-state", job_id, "/status", "terminal Pipeline Job cannot authorize a mutation")
        )
    if operation not in current["authority_snapshot"]["granted_operations"]:
        raise ResearchKBError(
            Diagnostic(INVALID_AUTHORITY, "pipeline-job-state", job_id, "/authority_snapshot/granted_operations", "Pipeline Job does not grant the requested operation")
        )
    return current


__all__ = ["require_job_authority"]
