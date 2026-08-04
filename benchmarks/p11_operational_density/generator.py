from __future__ import annotations

import hashlib
import os
import shutil
import uuid
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from benchmarks.p11_operational_density.profiles import (
    GENERATOR_CONTRACT_VERSION,
    OperationalProfile,
    profile_by_id,
)
from research_kb.agent_tasks import agent_task_chain_diagnostics
from research_kb.bundle import load_workspace_entries, validate_workspace_entries
from research_kb.catalog.models import canonical_digest
from research_kb.contracts.validator import RecordValidationSession
from research_kb.identifiers import Namespace
from research_kb.pipeline_jobs import pipeline_job_chain_diagnostics
from research_kb.services.bootstrap import WorkspaceBootstrapService
from research_kb.storage.json_io import file_sha256, read_jsonl, serialize_json
from research_kb.workspace import WorkspaceLayout


DEFAULT_SEED = "p11-operational-density-seed-v1"
FIXTURE_ORIGIN = "synthetic_from_scratch"
GENERATOR_MARKER = ".p11-operational-generator.json"
GENERATOR_MANIFEST = "p11-operational-generator-manifest.json"
BENCHMARK_ERROR = "RKBC-036"


@dataclass(frozen=True, slots=True)
class GeneratedOperationalWorkspace:
    target: Path
    profile: OperationalProfile
    seed: str
    layout: WorkspaceLayout
    manifest: dict[str, Any]


def generate_workspace(
    target: Path,
    *,
    profile_id: str,
    seed: str = DEFAULT_SEED,
    validate_full_bundle: bool = True,
) -> GeneratedOperationalWorkspace:
    profile = profile_by_id(profile_id)
    resolved = _prepare_target(Path(target), profile, seed)
    workspace_root = resolved / "workspace"
    fixture_root = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "p2_small" / "workspace"
    shutil.copytree(fixture_root, workspace_root, ignore=shutil.ignore_patterns(".research-kb"))
    bootstrap = WorkspaceBootstrapService(workspace_root / "workspace.yaml").run()
    if bootstrap.exit_code != 0:
        raise _error("synthetic seed workspace bootstrap failed")
    layout = WorkspaceLayout.load(workspace_root / "workspace.yaml")
    first_paper = read_jsonl(layout.registry_path, record_kind="registry-paper", id_field="paper_id")[0]
    _write_operational_payload(layout, profile, seed, first_paper)
    _validate_chain_files(layout)
    if validate_full_bundle:
        validate_workspace_entries(load_workspace_entries(layout))
    manifest = _manifest(resolved, layout, profile, seed, first_paper["paper_id"])
    (resolved / GENERATOR_MANIFEST).write_bytes(serialize_json(manifest))
    marker = {
        "contract_version": "1.0",
        "generator_contract_version": GENERATOR_CONTRACT_VERSION,
        "profile_id": profile.profile_id,
        "seed": seed,
        "state": "complete",
        "manifest_sha256": _hash_file(resolved / GENERATOR_MANIFEST),
        "fixture_origin": FIXTURE_ORIGIN,
    }
    (resolved / GENERATOR_MARKER).write_bytes(serialize_json(marker))
    return GeneratedOperationalWorkspace(resolved, profile, seed, layout, manifest)


def inspect_generated_workspace(target: Path, *, validate_full_bundle: bool = False) -> GeneratedOperationalWorkspace:
    resolved = Path(target).resolve()
    marker = _read_json(resolved / GENERATOR_MARKER)
    manifest = _read_json(resolved / GENERATOR_MANIFEST)
    if (
        marker.get("state") != "complete"
        or marker.get("generator_contract_version") != GENERATOR_CONTRACT_VERSION
        or marker.get("manifest_sha256") != _hash_file(resolved / GENERATOR_MANIFEST)
    ):
        raise _error("operational generator marker or manifest changed")
    profile = profile_by_id(str(marker.get("profile_id")))
    layout = WorkspaceLayout.load(resolved / "workspace" / "workspace.yaml")
    if _tracked_digests(layout) != manifest.get("tracked_digests"):
        raise _error("operational fixture tracked payload changed")
    if validate_full_bundle:
        validate_workspace_entries(load_workspace_entries(layout))
    return GeneratedOperationalWorkspace(resolved, profile, str(marker.get("seed")), layout, manifest)


def maintenance_triggers(profile: OperationalProfile, seed: str = DEFAULT_SEED) -> Iterator[dict[str, str]]:
    for index in range(profile.maintenance_trigger_count):
        key_index = index % profile.maintenance_key_count
        yield {
            "dependent_id": _id(Namespace.QUESTION, seed, key_index + 1),
            "upstream_revision": _id(Namespace.PRIMARY_REVISION, seed, key_index + 1),
            "reason": "upstream_revised",
            "trigger_ref": _id(Namespace.PROCESS_EVENT, f"{seed}-trigger", index + 1),
        }


def _write_operational_payload(
    layout: WorkspaceLayout,
    profile: OperationalProfile,
    seed: str,
    paper: dict[str, Any],
) -> None:
    existing_events = read_jsonl(layout.process_events_path, record_kind="process-event", id_field="event_id")
    _write_jsonl(layout.process_events_path, [*existing_events, *_events(profile, seed)])
    existing_reports = read_jsonl(layout.guardian_reports_path, record_kind="guardian-report", id_field="guardian_report_id")
    _write_jsonl(layout.guardian_reports_path, [*existing_reports, *_guardian_reports(layout.workspace_id, profile, seed)])
    _write_jsonl(layout.pipeline_jobs_path, _pipeline_states(layout.workspace_id, profile, seed))
    _write_jsonl(layout.agent_tasks_path, _agent_task_states(layout.workspace_id, paper, profile, seed))
    if not layout.review_queue_path.exists():
        layout.review_queue_path.parent.mkdir(parents=True, exist_ok=True)
        layout.review_queue_path.write_bytes(b"")
    target_digest = file_sha256(layout.review_queue_path)
    if target_digest is None:
        raise _error("synthetic journal target digest is unavailable")
    for index in range(profile.journal_count):
        event_id = _id(Namespace.PROCESS_EVENT, seed, index + 1)
        journal = {
            "schema_version": "1.0",
            "event_id": event_id,
            "operation": "synthetic_journal_target",
            "actor": "cli",
            "target_store": "review_queue",
            "target_relative_path": "review_queue/items.jsonl",
            "before_sha256": target_digest,
            "after_sha256": target_digest,
            "input_refs": [],
            "output_refs": [],
            "phase": "complete",
            "result": "success",
            "created_at": _timestamp(index),
            "updated_at": _timestamp(index),
        }
        layout.journal_path(event_id).write_bytes(serialize_json(journal))


def _events(profile: OperationalProfile, seed: str) -> Iterator[dict[str, Any]]:
    emitted = 0
    for index in range(profile.journal_count):
        emitted += 1
        yield _event(
            event_id=_id(Namespace.PROCESS_EVENT, seed, index + 1),
            operation="synthetic_journal_target",
            actor="cli",
            result="success",
            input_refs=[],
            output_refs=[],
            created_at=_timestamp(index),
        )
    for index in range(profile.job_count):
        job_id = _id(Namespace.JOB, seed, index + 1)
        for revision, operation in ((1, "pipeline_job_create"), (2, "pipeline_job_transition")):
            emitted += 1
            yield _event(
                event_id=_id(Namespace.PROCESS_EVENT, f"{seed}-job-event", index * 2 + revision),
                operation=operation,
                actor="cli",
                result="success",
                input_refs=[],
                output_refs=[_id(Namespace.JOB_STATE, seed, index * 2 + revision)],
                created_at=_timestamp(index + revision - 1),
                job_id=job_id,
            ) | {"fixture_origin": FIXTURE_ORIGIN}
    for index in range(profile.task_count):
        state_count = 4 if index < profile.report_only_result_count else 2
        operations = (
            ("agent_task_create", "agent_task_lease", "agent_task_submit", "agent_task_approve")
            if state_count == 4
            else ("agent_task_create", "agent_task_cancel")
        )
        for revision, operation in enumerate(operations, start=1):
            emitted += 1
            yield _event(
                event_id=_id(Namespace.PROCESS_EVENT, f"{seed}-task-event", index * 4 + revision),
                operation=operation,
                actor="cli",
                result="success",
                input_refs=[],
                output_refs=[_id(Namespace.AGENT_TASK_STATE, seed, index * 4 + revision)],
                created_at=_timestamp(index + revision - 1),
            ) | {"fixture_origin": FIXTURE_ORIGIN}
    for index in range(profile.process_event_count - emitted):
        yield _event(
            event_id=_id(Namespace.PROCESS_EVENT, f"{seed}-generic-event", index + 1),
            operation="synthetic_operational_activity",
            actor="cli",
            result="success",
            input_refs=[],
            output_refs=[],
            created_at=_timestamp(index),
        ) | {"fixture_origin": FIXTURE_ORIGIN}


def _guardian_reports(workspace_id: str, profile: OperationalProfile, seed: str) -> Iterator[dict[str, Any]]:
    for index in range(profile.guardian_report_count):
        yield {
            "schema_version": "1.0",
            "guardian_report_id": _id(Namespace.GUARDIAN_REPORT, seed, index + 1),
            "workspace_id": workspace_id,
            "status": "success",
            "findings": [],
            "created_at": _timestamp(index),
            "fixture_origin": FIXTURE_ORIGIN,
        }


def _pipeline_states(workspace_id: str, profile: OperationalProfile, seed: str) -> Iterator[dict[str, Any]]:
    for index in range(profile.job_count):
        job_id = _id(Namespace.JOB, seed, index + 1)
        created_at = _timestamp(index)
        root = {
            "schema_version": "1.0",
            "state_id": _id(Namespace.JOB_STATE, seed, index * 2 + 1),
            "job_id": job_id,
            "workspace_id": workspace_id,
            "revision": 1,
            "predecessor": None,
            "requested_route": "semantic_processing",
            "requested_depth": "semantic_gate",
            "current_node": "synthetic_terminal",
            "status": "created",
            "wait_reason": None,
            "input_refs": [],
            "output_refs": [],
            "authority_snapshot": {"actor": "cli", "granted_operations": [], "captured_at": created_at},
            "idempotency_key": f"p11-job-{index:08d}",
            "retry_count": 0,
            "recovery_action": None,
            "terminal_receipt": False,
            "created_at": created_at,
            "updated_at": created_at,
            "fixture_origin": FIXTURE_ORIGIN,
        }
        terminal = {
            **root,
            "state_id": _id(Namespace.JOB_STATE, seed, index * 2 + 2),
            "revision": 2,
            "predecessor": {"state_id": root["state_id"], "state_digest": canonical_digest(root)},
            "status": "cancelled",
            "terminal_receipt": True,
            "updated_at": _timestamp(index + 1),
        }
        yield root
        yield terminal


def _agent_task_states(
    workspace_id: str,
    paper: dict[str, Any],
    profile: OperationalProfile,
    seed: str,
) -> Iterator[dict[str, Any]]:
    for index in range(profile.task_count):
        task_id = _id(Namespace.AGENT_TASK, seed, index + 1)
        basis = _query_basis(paper)
        basis_digest = canonical_digest(basis)
        created_at = _timestamp(index)
        root = {
            "schema_version": "1.0",
            "state_id": _id(Namespace.AGENT_TASK_STATE, seed, index * 4 + 1),
            "task_id": task_id,
            "workspace_id": workspace_id,
            "revision": 1,
            "predecessor": None,
            "task_kind": "knowledge_query_report",
            "result_contract": "p5c-knowledge-query-report@1.0",
            "privacy_registry_version": "p8-v1",
            "executor_id": "codex_cli",
            "execution_scope": "cloud_allowed",
            "effective_content_classes": ["canonical_evidence", "operational_context", "paper_card_content"],
            "input_basis": basis,
            "input_basis_digest": basis_digest,
            "idempotency_key": f"p11-task-{index:08d}",
            "lineage": None,
            "status": "created",
            "lease": None,
            "staged_result": None,
            "decision": None,
            "terminal_receipt": False,
            "created_at": created_at,
            "updated_at": created_at,
            "fixture_origin": FIXTURE_ORIGIN,
        }
        yield root
        if index >= profile.report_only_result_count:
            yield {
                **root,
                "state_id": _id(Namespace.AGENT_TASK_STATE, seed, index * 4 + 2),
                "revision": 2,
                "predecessor": {"state_id": root["state_id"], "state_digest": canonical_digest(root)},
                "status": "cancelled",
                "terminal_receipt": True,
                "updated_at": _timestamp(index + 1),
            }
            continue
        lease = {"lease_id": _digest(f"lease-{index}"), "handoff_digest": basis_digest, "issued_at": created_at}
        leased = {
            **root,
            "state_id": _id(Namespace.AGENT_TASK_STATE, seed, index * 4 + 2),
            "revision": 2,
            "predecessor": {"state_id": root["state_id"], "state_digest": canonical_digest(root)},
            "status": "leased",
            "lease": lease,
            "updated_at": _timestamp(index + 1),
        }
        result = {
            "contract_version": "p5c-knowledge-query-report@1.0",
            "task_id": task_id,
            "input_basis_digest": basis_digest,
            "query_type": "methods",
            "answer_blocks": [{"block_role": "unresolved", "text": "Synthetic report-only result.", "support_refs": [], "background_refs": [], "background_only": False}],
            "unresolved_items": [],
            "persistence_status": "report_only",
            "canonical_scientific_write": False,
        }
        submitted = {
            **leased,
            "state_id": _id(Namespace.AGENT_TASK_STATE, seed, index * 4 + 3),
            "revision": 3,
            "predecessor": {"state_id": leased["state_id"], "state_digest": canonical_digest(leased)},
            "status": "submitted",
            "staged_result": result,
            "updated_at": _timestamp(index + 2),
        }
        approved = {
            **submitted,
            "state_id": _id(Namespace.AGENT_TASK_STATE, seed, index * 4 + 4),
            "revision": 4,
            "predecessor": {"state_id": submitted["state_id"], "state_digest": canonical_digest(submitted)},
            "status": "approved",
            "decision": {
                "action": "approved",
                "reason_code": "report_accepted",
                "feedback": None,
                "successor_task_id": None,
                "applied_job_state_id": None,
                "decided_at": _timestamp(index + 3),
            },
            "terminal_receipt": True,
            "updated_at": _timestamp(index + 3),
        }
        yield leased
        yield submitted
        yield approved


def _query_basis(paper: dict[str, Any]) -> dict[str, Any]:
    paper_id = paper["paper_id"]
    return {
        "query_type": "methods",
        "query_text": "Summarize the synthetic method.",
        "paper_ids": [paper_id],
        "include_review_background": False,
        "include_routing_context": False,
        "paper_snapshots": [{
            "paper_id": paper_id,
            "paper_record_digest": canonical_digest(paper),
            "canonical_paper_id": paper_id,
            "library_status": "active",
            "identity_projection_digest": _digest("identity"),
            "source_state": "current",
            "source_digest": paper["source_fingerprint"]["value"],
            "document_route": "unprocessed",
            "authority_mode": "none",
            "revision_id": None,
            "revision_digest": None,
            "card_digest": None,
            "evidence_digests": [],
            "review_memory_digest": None,
        }],
        "mapping_snapshots": [],
        "payload_digest": _digest("payload"),
    }


def _validate_chain_files(layout: WorkspaceLayout) -> None:
    jobs = read_jsonl(layout.pipeline_jobs_path, record_kind="pipeline-job-state", id_field="state_id")
    tasks = read_jsonl(layout.agent_tasks_path, record_kind="agent-task-state", id_field="state_id")
    diagnostics = pipeline_job_chain_diagnostics(jobs) + agent_task_chain_diagnostics(tasks)
    if diagnostics:
        raise _error(diagnostics[0].message)
    events = {
        item["event_id"]: item
        for item in read_jsonl(layout.process_events_path, record_kind="process-event", id_field="event_id")
    }
    journal_session = RecordValidationSession("transaction-journal", actor="stored")
    for path in sorted(layout.transactions_root.glob("*.json")):
        journal = _read_json(path)
        journal_diagnostics = journal_session.validate(journal)
        if journal_diagnostics:
            raise _error(journal_diagnostics[0].message)
        expected = _event(
            event_id=journal["event_id"],
            operation=journal["operation"],
            actor=journal["actor"],
            result=journal["result"],
            input_refs=journal["input_refs"],
            output_refs=journal["output_refs"],
            created_at=journal["created_at"],
            job_id=journal.get("job_id"),
        )
        if events.get(journal["event_id"]) != expected:
            raise _error("synthetic journal event closure is inconsistent")


def _event(
    *,
    event_id: str,
    operation: str,
    actor: str,
    result: str,
    input_refs: list[str],
    output_refs: list[str],
    created_at: str,
    job_id: str | None = None,
) -> dict[str, Any]:
    event = {
        "schema_version": "1.0",
        "event_id": event_id,
        "operation": operation,
        "actor": actor,
        "result": result,
        "input_refs": input_refs,
        "output_refs": output_refs,
        "created_at": created_at,
    }
    if job_id is not None:
        event["job_id"] = job_id
    return event


def _manifest(target: Path, layout: WorkspaceLayout, profile: OperationalProfile, seed: str, paper_id: str) -> dict[str, Any]:
    return {
        "contract_version": "1.0",
        "generator_contract_version": GENERATOR_CONTRACT_VERSION,
        "profile_id": profile.profile_id,
        "seed": seed,
        "parameters": profile.parameters(),
        "workspace_id": layout.workspace_id,
        "paper_id": paper_id,
        "tracked_digests": _tracked_digests(layout),
        "fixture_origin": FIXTURE_ORIGIN,
    }


def _tracked_digests(layout: WorkspaceLayout) -> dict[str, str | None]:
    return {
        "process/events.jsonl": file_sha256(layout.process_events_path),
        "process/jobs.jsonl": file_sha256(layout.pipeline_jobs_path),
        "process/agent_tasks.jsonl": file_sha256(layout.agent_tasks_path),
        "guardian/reports.jsonl": file_sha256(layout.guardian_reports_path),
        "transactions_tree": canonical_digest([
            [path.name, file_sha256(path)] for path in sorted(layout.transactions_root.glob("*.json"))
        ]),
    }


def _prepare_target(target: Path, profile: OperationalProfile, seed: str) -> Path:
    if not target.is_absolute() or os.path.lexists(target):
        raise _error("operational generator target must be an absent absolute path")
    repository_root = Path(__file__).resolve().parents[2]
    unresolved = Path(os.path.abspath(target))
    if unresolved == repository_root or unresolved.is_relative_to(repository_root):
        raise _error("operational generator target is forbidden inside the repository")
    if not unresolved.parent.is_dir():
        raise _error("operational generator target parent is missing")
    unresolved.mkdir()
    (unresolved / GENERATOR_MARKER).write_bytes(serialize_json({
        "contract_version": "1.0",
        "generator_contract_version": GENERATOR_CONTRACT_VERSION,
        "profile_id": profile.profile_id,
        "seed": seed,
        "state": "generating",
        "fixture_origin": FIXTURE_ORIGIN,
    }))
    return unresolved.resolve()


def _write_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        for record in records:
            handle.write(serialize_json(record))


def _id(namespace: Namespace, seed: str, ordinal: int) -> str:
    identity = f"{GENERATOR_CONTRACT_VERSION}|{seed}|{namespace.value}|{ordinal}"
    value = uuid.UUID(bytes=hashlib.sha256(identity.encode("utf-8")).digest()[:16], version=4)
    return f"{namespace.value}_{value}"


def _timestamp(ordinal: int) -> str:
    day, second = divmod(ordinal, 86_400)
    hour, remainder = divmod(second, 3_600)
    minute, value = divmod(remainder, 60)
    return f"2026-01-{day + 1:02d}T{hour:02d}:{minute:02d}:{value:02d}Z"


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    import json
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise _error("generator control file is not an object")
    return value


def _error(message: str) -> RuntimeError:
    return RuntimeError(f"{BENCHMARK_ERROR}: {message}")


__all__ = [
    "DEFAULT_SEED",
    "GeneratedOperationalWorkspace",
    "generate_workspace",
    "inspect_generated_workspace",
    "maintenance_triggers",
]
