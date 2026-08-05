from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any, BinaryIO

from research_kb.application import APPLICATION_SERVICE_INTERFACE_VERSION
from research_kb.bundle import load_workspace_entries, records_of_kind, validate_workspace_entries
from research_kb.catalog.models import canonical_digest
from research_kb.errors import (
    INCOMPLETE_TRANSACTION,
    INVALID_AUTHORITY,
    SCHEMA_VALIDATION_FAILED,
    UNRESOLVED_REFERENCE,
    WRITE_CONFLICT,
    Diagnostic,
    ResearchKBError,
)
from research_kb.identifiers import Namespace, validate_id
from research_kb.pipeline_jobs import TERMINAL_STATUSES, current_pipeline_states
from research_kb.process_events import read_process_events, timestamp, utc_now
from research_kb.services.deterministic_trunk import DeterministicTrunkService
from research_kb.services.local_source_intake import (
    MAX_PDF_BYTES,
    MAX_SCAN_ENTRIES,
    LocalSourceIntakeService,
)
from research_kb.services.pipeline_job import PipelineJobService
from research_kb.services.registry import RegistryService
from research_kb.services.source_adequacy import SourceAdequacyService
from research_kb.services.source_asset import SourceAssetService
from research_kb.services.workspace_session import WorkspaceSession
from research_kb.source_assets import current_source_asset_heads
from research_kb.storage.json_io import read_jsonl
from research_kb.workspace import WorkspaceLayout


Clock = Callable[[], datetime]
OperationHook = Callable[[str], None]

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_INBOX_HANDLE_PATTERN = re.compile(r"^inbox-v1:[0-9a-f]{64}$")
_REQUESTED_OPERATIONS = frozenset({"basic_paper_card", "basic_review_memory"})
_UPLOAD_FIELDS = frozenset(
    {
        "idempotency_key",
        "requested_operation",
        "document_route",
        "route_reason",
        "bibliography",
        "expected_sha256",
        "expected_size_bytes",
    }
)
_INBOX_FIELDS = frozenset(
    {
        "idempotency_key",
        "requested_operation",
        "document_route",
        "route_reason",
        "bibliography",
        "min_stable_age_seconds",
    }
)
_RESUME_FIELDS = frozenset(
    {"requested_operation", "document_route", "route_reason", "bibliography"}
)
_UPLOAD_AUTHORITIES = (
    "advance_deterministic_trunk",
    "assess_source_adequacy",
    "associate_source_asset",
    "copy_into_local_inbox",
    "observe_source",
    "parse_run",
    "registry_add",
)
_INBOX_AUTHORITIES = (
    "advance_deterministic_trunk",
    "assess_source_adequacy",
    "associate_source_asset",
    "observe_source",
    "parse_run",
    "register_by_reference",
    "registry_add",
    "select_inbox_candidate",
)
_INTAKE_PROGRESS_RANK = {
    "source_intake": 0,
    "registry": 1,
    "source_association": 2,
    "deterministic_trunk": 3,
}


class DeterministicIntakeApplicationService:
    def __init__(
        self,
        *,
        clock: Clock = utc_now,
        operation_hook: OperationHook | None = None,
    ):
        self.clock = clock
        self.operation_hook = operation_hook

    def scan_inbox(
        self,
        session: WorkspaceSession,
        *,
        max_entries: int,
        min_stable_age_seconds: int,
    ) -> dict[str, Any]:
        layout = _session_layout(session)
        report = LocalSourceIntakeService(layout, clock=self.clock).scan(
            max_entries=max_entries,
            min_stable_age_seconds=min_stable_age_seconds,
        )
        return {
            "status": "success",
            "interface_version": APPLICATION_SERVICE_INTERFACE_VERSION,
            "candidates": [
                {
                    "candidate_token": item["candidate_token"],
                    "name": item["name"],
                    "size_bytes": item["size_bytes"],
                }
                for item in report["candidates"]
            ],
            "persistent_writes": 0,
        }

    def start_upload(
        self,
        session: WorkspaceSession,
        stream: BinaryIO,
        request: Mapping[str, Any],
    ) -> dict[str, Any]:
        layout = _session_layout(session)
        normalized = _normalize_start_request(request, mode="upload")
        state, writes = self._create_or_replay_job(layout, normalized)
        if state["status"] in TERMINAL_STATUSES:
            source_state = _source_state_for_job(layout, state["job_id"], "upload")
            return self._continue(layout, state, source_state, normalized, writes)
        intake = LocalSourceIntakeService(layout, clock=self.clock).copy_stream(
            stream=stream,
            job_id=state["job_id"],
            paper_id=None,
            asset_role="main_pdf",
            actor="user",
            expected_sha256=normalized["expected_sha256"],
            expected_size=normalized["expected_size_bytes"],
        )
        writes += intake["persistent_writes"]
        self._hook("source_receipt")
        source_state = _source_state_for_job(layout, state["job_id"], "upload")
        return self._continue(layout, state, source_state, normalized, writes)

    def start_inbox(
        self,
        session: WorkspaceSession,
        candidate_token: str,
        request: Mapping[str, Any],
    ) -> dict[str, Any]:
        layout = _session_layout(session)
        if not isinstance(candidate_token, str) or not _INBOX_HANDLE_PATTERN.fullmatch(candidate_token):
            raise _request_error("inbox candidate token is invalid", "/candidate_token")
        normalized = _normalize_start_request(
            {**dict(request), "candidate_token": candidate_token},
            mode="watched_inbox",
        )
        state, writes = self._create_or_replay_job(layout, normalized)
        if state["status"] in TERMINAL_STATUSES:
            source_state = _source_state_for_job(layout, state["job_id"], "watched_inbox")
            return self._continue(layout, state, source_state, normalized, writes)
        intake = LocalSourceIntakeService(layout, clock=self.clock).select(
            candidate_handle=candidate_token,
            job_id=state["job_id"],
            paper_id=None,
            asset_role="main_pdf",
            actor="cli",
            min_stable_age_seconds=normalized["min_stable_age_seconds"],
        )
        writes += intake["persistent_writes"]
        self._hook("source_receipt")
        source_state = _source_state_for_job(layout, state["job_id"], "watched_inbox")
        return self._continue(layout, state, source_state, normalized, writes)

    def resume(
        self,
        session: WorkspaceSession,
        job_id: str,
        expected_state: Mapping[str, Any],
        request: Mapping[str, Any],
    ) -> dict[str, Any]:
        layout = _session_layout(session)
        job_id = validate_id(job_id, Namespace.JOB)
        normalized = _normalize_resume_request(request)
        state = _require_expected_state(PipelineJobService(layout).show(job_id)["current_state"], expected_state)
        mode = _ingress_mode(state)
        source_state = _source_state_for_job(layout, job_id, mode, missing_ok=True)
        if source_state is None:
            if state["status"] == "created":
                mutation = PipelineJobService(layout).transition(
                    job_id,
                    expected_state_id=state["state_id"],
                    expected_state_digest=canonical_digest(state),
                    status="waiting_user",
                    current_node="source_intake",
                    wait_reason="source_selection_required",
                    output_refs=[],
                    retry_increment=0,
                    recovery_action=None,
                    actor="user",
                )
                return _job_result(layout, mutation.state, None, normalized, 1)
            return _job_result(layout, state, None, normalized, 0)
        _validate_resume_against_receipts(layout, state, normalized)
        if mode == "upload":
            _, source_path = layout.resolve_source(
                source_state["source_ref"]["root_id"],
                source_state["source_ref"]["relative_path"],
            )
            if not source_path.exists():
                LocalSourceIntakeService(layout, clock=self.clock).recover_copy(
                    job_id=job_id,
                    actor="user",
                )
        return self._continue(layout, state, source_state, normalized, 0)

    def cancel(
        self,
        session: WorkspaceSession,
        job_id: str,
        expected_state: Mapping[str, Any],
    ) -> dict[str, Any]:
        layout = _session_layout(session)
        job_id = validate_id(job_id, Namespace.JOB)
        current = _require_expected_state(
            PipelineJobService(layout).show(job_id)["current_state"],
            expected_state,
        )
        mutation = PipelineJobService(layout).cancel(
            job_id,
            expected_state_id=current["state_id"],
            expected_state_digest=canonical_digest(current),
            actor="user",
        )
        return _job_result(layout, mutation.state, _paper_for_job(layout, job_id), None, 1)

    def list_jobs(
        self,
        session: WorkspaceSession,
        *,
        page_size: int,
        cursor: str | None,
        catalog_query: Any | None = None,
    ) -> dict[str, Any]:
        if catalog_query is not None:
            page = catalog_query.operational_page(
                item_kind="pipeline_job",
                page_size=page_size,
                cursor=cursor,
            )
            fields = (
                "job_id",
                "state_id",
                "revision",
                "requested_route",
                "requested_depth",
                "current_node",
                "status",
                "wait_reason",
                "retry_count",
                "terminal_receipt",
                "updated_at",
                "state_digest",
                "can_resume",
                "can_cancel",
            )
            return {
                "status": "success",
                "interface_version": APPLICATION_SERVICE_INTERFACE_VERSION,
                "jobs": [{field: item[field] for field in fields} for item in page["records"]],
                "next_cursor": page["next_cursor"],
                "projection_state": page["projection_state"],
                "persistent_writes": 0,
            }
        layout = _session_layout(session)
        jobs = PipelineJobService(layout)
        page = jobs.list(page_size=page_size, cursor=cursor)
        return {
            "status": "success",
            "interface_version": APPLICATION_SERVICE_INTERFACE_VERSION,
            "jobs": [
                _pipeline_projection(jobs.show(item["job_id"])["current_state"])
                for item in page["jobs"]
            ],
            "next_cursor": page["next_cursor"],
            "persistent_writes": 0,
        }

    def show_job(self, session: WorkspaceSession, job_id: str) -> dict[str, Any]:
        layout = _session_layout(session)
        job_id = validate_id(job_id, Namespace.JOB)
        state = PipelineJobService(layout).show(job_id)["current_state"]
        return _job_result(layout, state, _paper_for_job(layout, job_id), None, 0)

    def limits(self, session: WorkspaceSession) -> dict[str, Any]:
        _session_layout(session)
        return {
            "status": "success",
            "interface_version": APPLICATION_SERVICE_INTERFACE_VERSION,
            "max_pdf_bytes": MAX_PDF_BYTES,
            "max_scan_entries": MAX_SCAN_ENTRIES,
            "max_job_page_size": 100,
            "default_min_stable_age_seconds": 5,
            "ingress_modes": ["upload", "watched_inbox"],
            "requested_operations": sorted(_REQUESTED_OPERATIONS),
        }

    def _create_or_replay_job(
        self,
        layout: WorkspaceLayout,
        request: dict[str, Any],
    ) -> tuple[dict[str, Any], int]:
        effective_key, client_prefix = _effective_idempotency_key(request)
        states = read_jsonl(
            layout.pipeline_jobs_path,
            record_kind="pipeline-job-state",
            id_field="state_id",
        )
        roots = [
            item
            for item in states
            if item["revision"] == 1 and item["idempotency_key"].startswith(client_prefix)
        ]
        if len(roots) > 1:
            raise _receipt_error(None, "client idempotency key resolves to multiple Jobs")
        if roots:
            root = roots[0]
            if root["idempotency_key"] != effective_key:
                raise _conflict(root["job_id"], "idempotency key is already bound to another intake intent")
            current = next(
                item for item in current_pipeline_states(states) if item["job_id"] == root["job_id"]
            )
            _validate_job_authority(current, request["ingress_mode"])
            return current, 0

        authorities = (
            _UPLOAD_AUTHORITIES
            if request["ingress_mode"] == "upload"
            else _INBOX_AUTHORITIES
        )
        created = PipelineJobService(layout).create(
            requested_route="local_source",
            requested_depth="semantic_gate",
            current_node="source_intake",
            input_refs=[],
            authority_snapshot={
                "actor": "user",
                "granted_operations": list(authorities),
                "captured_at": timestamp(self.clock),
            },
            idempotency_key=effective_key,
            actor="user",
        )
        return created.state, 1

    def _continue(
        self,
        layout: WorkspaceLayout,
        state: dict[str, Any],
        source_state: dict[str, Any],
        request: dict[str, Any],
        writes: int,
    ) -> dict[str, Any]:
        jobs = PipelineJobService(layout)
        state, changed = _progress(
            jobs,
            state,
            node="registry",
            output_refs=[source_state["source_asset_id"], source_state["source_asset_state_id"]],
        )
        writes += changed
        paper, changed = _reconcile_paper(layout, state["job_id"], source_state, request["bibliography"])
        writes += changed
        self._hook("registry_add")
        state, changed = _progress(
            jobs,
            state,
            node="source_association",
            output_refs=[paper["paper_id"]],
        )
        writes += changed
        associated, changed = _reconcile_association(layout, state["job_id"], source_state, paper)
        writes += changed
        self._hook("source_association")
        state, changed = _progress(
            jobs,
            state,
            node="deterministic_trunk",
            output_refs=[associated["source_asset_id"], associated["source_asset_state_id"]],
        )
        writes += changed
        trunk = DeterministicTrunkService(layout).advance(
            job_id=state["job_id"],
            paper_id=paper["paper_id"],
            requested_operation=request["requested_operation"],
            adapter_name="pdfplumber-text-flow",
            actor="user",
            document_route=request["document_route"],
            route_reason=request["route_reason"],
        )
        writes += trunk.persistent_writes
        return _job_result(layout, trunk.state, paper, request, writes)

    def _hook(self, phase: str) -> None:
        if self.operation_hook is not None:
            self.operation_hook(phase)


def _session_layout(session: WorkspaceSession) -> WorkspaceLayout:
    if not isinstance(session, WorkspaceSession):
        raise _request_error("a Core-owned WorkspaceSession is required", "/session")
    return session._layout


def _normalize_start_request(request: Mapping[str, Any], *, mode: str) -> dict[str, Any]:
    if not isinstance(request, Mapping):
        raise _request_error("intake request must be an object", "/request")
    values = dict(request)
    candidate_handle = values.pop("candidate_token", None)
    expected = _UPLOAD_FIELDS if mode == "upload" else _INBOX_FIELDS
    if set(values) != expected:
        raise _request_error("intake request fields do not match the contract", "/request")
    key = values["idempotency_key"]
    if not isinstance(key, str) or not 1 <= len(key) <= 128:
        raise _request_error("idempotency key must contain 1 to 128 characters", "/idempotency_key")
    result = {
        "ingress_mode": mode,
        "client_idempotency_key": key,
        **_normalize_semantic_request(values),
    }
    if mode == "upload":
        digest = values["expected_sha256"]
        size = values["expected_size_bytes"]
        if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
            raise _request_error("expected SHA-256 is invalid", "/expected_sha256")
        if not isinstance(size, int) or isinstance(size, bool) or not 1 <= size <= MAX_PDF_BYTES:
            raise _request_error("expected PDF size is outside the bounded range", "/expected_size_bytes")
        result.update({"expected_sha256": digest, "expected_size_bytes": size})
    else:
        age = values["min_stable_age_seconds"]
        if not isinstance(age, int) or isinstance(age, bool) or not 0 <= age <= 86400:
            raise _request_error("stability window must be an integer between 0 and 86400", "/min_stable_age_seconds")
        result.update(
            {
                "candidate_token": candidate_handle,
                "min_stable_age_seconds": age,
            }
        )
    return result


def _normalize_resume_request(request: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(request, Mapping) or set(request) != _RESUME_FIELDS:
        raise _request_error("resume request fields do not match the contract", "/request")
    return _normalize_semantic_request(request)


def _normalize_semantic_request(request: Mapping[str, Any]) -> dict[str, Any]:
    operation = request.get("requested_operation")
    route = request.get("document_route")
    reason = request.get("route_reason")
    if operation not in _REQUESTED_OPERATIONS:
        raise _request_error("requested operation is not supported by deterministic intake", "/requested_operation")
    if route not in {None, "primary", "review"}:
        raise _request_error("document route must be primary, review or null", "/document_route")
    if reason not in {None, "mixed_document"}:
        raise _request_error("route reason is not registered", "/route_reason")
    if reason == "mixed_document":
        if route != "review" or operation != "basic_review_memory":
            raise _request_error("mixed document must use review route with basic Review Memory operation", "/route_reason")
    elif route == "primary" and operation != "basic_paper_card":
        raise _request_error("primary route requires basic Paper Card operation", "/requested_operation")
    elif route == "review" and operation != "basic_review_memory":
        raise _request_error("review route requires basic Review Memory operation", "/requested_operation")
    elif route is None and operation != "basic_paper_card":
        raise _request_error("undecided route uses the basic Paper Card operation", "/requested_operation")
    return {
        "requested_operation": operation,
        "document_route": route,
        "route_reason": reason,
        "bibliography": _normalize_bibliography(request.get("bibliography")),
    }


def _normalize_bibliography(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) - {"title", "authors", "year", "doi"}:
        raise _request_error("bibliography fields do not match the contract", "/bibliography")
    result = {"title": None, "authors": [], "year": None, "doi": None}
    result.update(value)
    if result["title"] is not None and not isinstance(result["title"], str):
        raise _request_error("bibliography title must be a string or null", "/bibliography/title")
    if not isinstance(result["authors"], list) or not all(isinstance(item, str) for item in result["authors"]):
        raise _request_error("bibliography authors must be an array of strings", "/bibliography/authors")
    if result["year"] is not None and (
        not isinstance(result["year"], int) or isinstance(result["year"], bool)
    ):
        raise _request_error("bibliography year must be an integer or null", "/bibliography/year")
    if result["doi"] is not None and not isinstance(result["doi"], str):
        raise _request_error("bibliography DOI must be a string or null", "/bibliography/doi")
    return result


def _effective_idempotency_key(request: Mapping[str, Any]) -> tuple[str, str]:
    client_hash = hashlib.sha256(request["client_idempotency_key"].encode("utf-8")).hexdigest()
    intent = {key: value for key, value in request.items() if key != "client_idempotency_key"}
    intent_hash = hashlib.sha256(
        json.dumps(intent, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    prefix = f"intake-v1:{client_hash}:"
    return prefix + intent_hash, prefix


def _validate_job_authority(state: Mapping[str, Any], mode: str) -> None:
    expected = _UPLOAD_AUTHORITIES if mode == "upload" else _INBOX_AUTHORITIES
    if (
        state["requested_route"] != "local_source"
        or state["requested_depth"] != "semantic_gate"
        or tuple(state["authority_snapshot"]["granted_operations"]) != tuple(sorted(expected))
    ):
        raise ResearchKBError(
            Diagnostic(
                INVALID_AUTHORITY,
                "deterministic-intake",
                state["job_id"],
                "/authority_snapshot",
                "Pipeline Job authority does not match the closed intake mode",
            )
        )


def _ingress_mode(state: Mapping[str, Any]) -> str:
    operations = set(state["authority_snapshot"]["granted_operations"])
    if operations == set(_UPLOAD_AUTHORITIES):
        return "upload"
    if operations == set(_INBOX_AUTHORITIES):
        return "watched_inbox"
    raise ResearchKBError(
        Diagnostic(
            INVALID_AUTHORITY,
            "deterministic-intake",
            state["job_id"],
            "/authority_snapshot",
            "Pipeline Job is not owned by a deterministic intake mode",
        )
    )


def _source_state_for_job(
    layout: WorkspaceLayout,
    job_id: str,
    mode: str,
    *,
    missing_ok: bool = False,
) -> dict[str, Any] | None:
    states = read_jsonl(
        layout.source_assets_path,
        record_kind="source-asset-state",
        id_field="source_asset_state_id",
    )
    roots = [
        item
        for item in states
        if item["revision"] == 1 and item["job_id"] == job_id and item["asset_role"] == "main_pdf"
    ]
    if not roots:
        if missing_ok:
            return None
        raise _receipt_error(job_id, "intake source receipt is missing")
    if len(roots) != 1:
        raise _receipt_error(job_id, "intake Job has multiple main source receipts")
    expected_reason = "copied_into_local_inbox" if mode == "upload" else "reference_registered"
    if roots[0]["reason"] != expected_reason:
        raise _receipt_error(job_id, "source receipt does not match the intake mode")
    expected_operation = (
        "source_asset_copy_into_local_inbox"
        if mode == "upload"
        else "source_asset_register_reference"
    )
    events = _job_events(layout, job_id, expected_operation)
    if len(events) != 1:
        raise _receipt_error(job_id, "intake source requires one correlated success event")
    expected_outputs = {
        roots[0]["source_asset_id"],
        roots[0]["source_asset_state_id"],
    }
    if set(events[0]["output_refs"]) != expected_outputs:
        raise _receipt_error(job_id, "intake source event does not match its source receipt")
    heads = {item["source_asset_id"]: item for item in current_source_asset_heads(states)}
    return heads[roots[0]["source_asset_id"]]


def _progress(
    jobs: PipelineJobService,
    state: dict[str, Any],
    *,
    node: str,
    output_refs: list[str],
) -> tuple[dict[str, Any], int]:
    if state["status"] in TERMINAL_STATUSES or state["status"].startswith("waiting_"):
        return state, 0
    if state["status"] == "running":
        current_rank = _INTAKE_PROGRESS_RANK.get(state["current_node"])
        target_rank = _INTAKE_PROGRESS_RANK[node]
        if current_rank is None or current_rank > target_rank:
            return state, 0
    intended_outputs = sorted(set(state["output_refs"]) | set(output_refs))
    if state["status"] == "running" and state["current_node"] == node and state["output_refs"] == intended_outputs:
        return state, 0
    mutation = jobs.transition(
        state["job_id"],
        expected_state_id=state["state_id"],
        expected_state_digest=canonical_digest(state),
        status="running",
        current_node=node,
        wait_reason=None,
        output_refs=output_refs,
        retry_increment=0,
        recovery_action=None,
        actor="cli",
    )
    return mutation.state, int(mutation.transaction is not None)


def _reconcile_paper(
    layout: WorkspaceLayout,
    job_id: str,
    source_state: Mapping[str, Any],
    bibliography: Mapping[str, Any],
) -> tuple[dict[str, Any], int]:
    events = _job_events(layout, job_id, "registry_add")
    if len(events) > 1:
        raise _receipt_error(job_id, "intake Job has multiple Registry receipts")
    if not events:
        paper, transaction = RegistryService(layout).add(
            root_id=source_state["source_ref"]["root_id"],
            relative_path=source_state["source_ref"]["relative_path"],
            metadata={"bibliography": dict(bibliography)},
            actor="cli",
            job_id=job_id,
        )
        return paper, int(transaction is not None)
    if not events[0]["output_refs"]:
        raise _receipt_error(job_id, "Registry receipt has no paper output")
    paper_id = validate_id(events[0]["output_refs"][0], Namespace.PAPER)
    source_root = _source_root_for_job(layout, job_id)
    entries = load_workspace_entries(layout)
    validate_workspace_entries(entries)
    paper = next(
        (item for item in records_of_kind(entries, "registry-paper") if item["paper_id"] == paper_id),
        None,
    )
    if paper is None:
        raise _receipt_error(job_id, "Registry receipt paper is missing")
    if (
        paper["source_ref"] != source_root["source_ref"]
        or paper["source_fingerprint"] != source_root["source_fingerprint"]
        or paper["bibliography"] != dict(bibliography)
    ):
        raise _conflict(job_id, "Registry receipt does not match the resumed intake request")
    return paper, 0


def _reconcile_association(
    layout: WorkspaceLayout,
    job_id: str,
    source_state: Mapping[str, Any],
    paper: Mapping[str, Any],
) -> tuple[dict[str, Any], int]:
    if source_state["paper_id"] is not None:
        if source_state["paper_id"] != paper["paper_id"]:
            raise _conflict(job_id, "source receipt is associated with another paper")
        events = _job_events(layout, job_id, "source_asset_associate")
        if len(events) != 1:
            raise _receipt_error(job_id, "associated source requires one correlated receipt")
        return dict(source_state), 0
    mutation = SourceAssetService(layout).associate(
        source_asset_id=source_state["source_asset_id"],
        job_id=job_id,
        paper_id=paper["paper_id"],
        expected_state_id=source_state["source_asset_state_id"],
        expected_state_digest=canonical_digest(source_state),
        actor="cli",
    )
    return mutation.state, int(mutation.transaction is not None)


def _validate_resume_against_receipts(
    layout: WorkspaceLayout,
    state: Mapping[str, Any],
    request: Mapping[str, Any],
) -> None:
    paper = _paper_for_job(layout, state["job_id"])
    if paper is not None:
        source_root = _source_root_for_job(layout, state["job_id"])
        if paper["bibliography"] != request["bibliography"]:
            raise _conflict(state["job_id"], "resume bibliography differs from the committed Registry receipt")
        if paper["source_fingerprint"] != source_root["source_fingerprint"]:
            raise _conflict(state["job_id"], "resume source differs from the committed Registry receipt")
        profiles = read_jsonl(
            layout.source_adequacy_path,
            record_kind="source-adequacy-profile",
            id_field="profile_id",
        )
        job_profiles = [item for item in profiles if item["job_id"] == state["job_id"]]
        if job_profiles and {item["requested_operation"] for item in job_profiles} != {
            request["requested_operation"]
        }:
            raise _conflict(state["job_id"], "resume operation differs from the committed adequacy receipt")


def _source_root_for_job(layout: WorkspaceLayout, job_id: str) -> dict[str, Any]:
    states = read_jsonl(
        layout.source_assets_path,
        record_kind="source-asset-state",
        id_field="source_asset_state_id",
    )
    roots = [
        item
        for item in states
        if item["revision"] == 1 and item["job_id"] == job_id and item["asset_role"] == "main_pdf"
    ]
    if len(roots) != 1:
        raise _receipt_error(job_id, "intake Job does not have exactly one main source receipt")
    return roots[0]


def _require_expected_state(
    state: dict[str, Any],
    expected: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(expected, Mapping) or set(expected) != {"state_id", "state_digest"}:
        raise _request_error("expected state fields do not match the contract", "/expected_state")
    expected_id = validate_id(expected["state_id"], Namespace.JOB_STATE)
    expected_digest = expected["state_digest"]
    if not isinstance(expected_digest, str) or not _SHA256.fullmatch(expected_digest):
        raise _request_error("expected state digest is invalid", "/expected_state/state_digest")
    if state["state_id"] != expected_id or canonical_digest(state) != expected_digest:
        raise _conflict(state["job_id"], "Pipeline Job state changed before the requested action")
    return state


def _job_events(layout: WorkspaceLayout, job_id: str, operation: str) -> list[dict[str, Any]]:
    return [
        item
        for item in read_process_events(layout.process_events_path)
        if item.get("job_id") == job_id
        and item["operation"] == operation
        and item["result"] == "success"
    ]


def _paper_for_job(layout: WorkspaceLayout, job_id: str) -> dict[str, Any] | None:
    events = _job_events(layout, job_id, "registry_add")
    if not events:
        return None
    if len(events) != 1 or not events[0]["output_refs"]:
        raise _receipt_error(job_id, "Registry receipt is ambiguous")
    paper_id = validate_id(events[0]["output_refs"][0], Namespace.PAPER)
    papers = read_jsonl(layout.registry_path, record_kind="registry-paper", id_field="paper_id")
    paper = next((item for item in papers if item["paper_id"] == paper_id), None)
    if paper is None:
        raise _receipt_error(job_id, "Registry receipt paper is missing")
    return paper


def _job_result(
    layout: WorkspaceLayout,
    state: dict[str, Any],
    paper: Mapping[str, Any] | None,
    request: Mapping[str, Any] | None,
    writes: int,
) -> dict[str, Any]:
    paper_id = None if paper is None else paper["paper_id"]
    operation = None if request is None else request.get("requested_operation")
    if operation is None and paper_id is not None:
        profiles = read_jsonl(
            layout.source_adequacy_path,
            record_kind="source-adequacy-profile",
            id_field="profile_id",
        )
        candidates = [item for item in profiles if item["job_id"] == state["job_id"]]
        if candidates:
            operation = candidates[-1]["requested_operation"]
    return {
        "status": "success",
        "interface_version": APPLICATION_SERVICE_INTERFACE_VERSION,
        "pipeline": _pipeline_projection(state),
        "ingress_mode": _ingress_mode(state),
        "paper_id": paper_id,
        "requested_operation": operation,
        "document_route": _document_route(state),
        "route_reason": _route_reason(state),
        "source_adequacy": _adequacy_projection(layout, paper_id, operation),
        "persistent_writes": writes,
    }


def _pipeline_projection(state: Mapping[str, Any]) -> dict[str, Any]:
    return {
        **PipelineJobService.summary(state),
        "state_digest": canonical_digest(state),
        "can_resume": state["status"] not in TERMINAL_STATUSES,
        "can_cancel": state["status"] not in TERMINAL_STATUSES,
    }


def _document_route(state: Mapping[str, Any]) -> str | None:
    node = state["current_node"]
    if node.startswith("primary_semantic_gate"):
        return "primary"
    if node.startswith("review_semantic_gate"):
        return "review"
    return None


def _route_reason(state: Mapping[str, Any]) -> str | None:
    if state["current_node"] == "review_semantic_gate_mixed_document":
        return "mixed_document"
    return None


def _adequacy_projection(
    layout: WorkspaceLayout,
    paper_id: str | None,
    operation: str | None,
) -> dict[str, Any] | None:
    if paper_id is None or operation is None:
        return None
    service = SourceAdequacyService(layout)
    shown = service.show(paper_id=paper_id, requested_operation=operation)
    if not shown["items"]:
        return None
    item = shown["items"][-1]
    gate = service.gate(paper_id=paper_id, requested_operation=operation)
    return {
        "requested_operation": operation,
        "gate_status": gate["status"],
        "required_capability": gate["required_capability"],
        "freshness": gate["freshness"],
        "capability_status": gate["capability_status"],
        "wait_reason": gate["wait_reason"],
        "capabilities": item["capabilities"],
        "known_limitations": item["known_limitations"],
        "recommended_actions": item["recommended_actions"],
    }


def _request_error(message: str, path: str) -> ResearchKBError:
    return ResearchKBError(
        Diagnostic(SCHEMA_VALIDATION_FAILED, "deterministic-intake", None, path, message)
    )


def _conflict(record_id: str | None, message: str) -> ResearchKBError:
    return ResearchKBError(
        Diagnostic(WRITE_CONFLICT, "deterministic-intake", record_id, "", message)
    )


def _receipt_error(record_id: str | None, message: str) -> ResearchKBError:
    return ResearchKBError(
        Diagnostic(INCOMPLETE_TRANSACTION, "deterministic-intake", record_id, "", message)
    )


__all__ = ["DeterministicIntakeApplicationService"]
