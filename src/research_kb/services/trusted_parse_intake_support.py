from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, timedelta
from typing import Any

from research_kb.bundle import load_workspace_entries, records_of_kind
from research_kb.catalog.models import canonical_digest
from research_kb.errors import (
    GROUNDING_MISMATCH,
    INCOMPLETE_TRANSACTION,
    INVALID_AUTHORITY,
    PARSE_SOURCE_UNSUPPORTED,
    SCHEMA_VALIDATION_FAILED,
    TRUST_AUTHORITY_INVALID,
    WRITE_CONFLICT,
    Diagnostic,
    ResearchKBError,
)
from research_kb.identifiers import Namespace, validate_id
from research_kb.process_events import Clock, read_process_events
from research_kb.services.parse_application import ParseAdapterRegistry
from research_kb.services.pipeline_job import PipelineJobService
from research_kb.services.trusted_parse_authority import TrustedParseAuthorityService
from research_kb.services.workspace_session import WorkspaceSession
from research_kb.source_assets import current_source_asset_heads
from research_kb.source_resolution import observe_paper_source
from research_kb.storage.json_io import read_jsonl
from research_kb.trusted_parse_authority import TrustedParseAuthorityPreview
from research_kb.trusted_parse_intake import (
    ALLOWED_OPERATION,
    AUTHORITY_PREFIX,
    ROUTE_SUFFIXES,
    TRUSTED_PREFIXES,
    TrustedParseIntakePreparation,
)
from research_kb.workspace import WorkspaceLayout


NonceFactory = Any


def authority_preparation(
    layout: WorkspaceLayout,
    job_id: str,
    paper_id: str,
    service: TrustedParseAuthorityService,
    parser: dict[str, str],
    profile_id: str,
    policy_version: str,
    clock: Clock,
    ttl: timedelta,
    nonce_factory: NonceFactory,
) -> tuple[TrustedParseAuthorityPreview, bool, dict[str, Any] | None]:
    events = [
        item
        for item in read_process_events(layout.process_events_path)
        if item.get("job_id") == job_id
        and item["operation"] == "trusted_parse_authority_commit"
        and item["result"] == "success"
    ]
    if len(events) > 1:
        raise service_error(
            INCOMPLETE_TRANSACTION,
            job_id,
            "/authority",
            "trusted Parse Job has multiple authority commit receipts",
        )
    if events:
        event = events[0]
        records = read_jsonl(
            layout.trusted_parse_authorities_path,
            record_kind="trusted-parse-authority",
            id_field="state_id",
        )
        matching = [
            item
            for item in records
            if item["authority_id"] in event["output_refs"]
            and item["state_id"] in event["output_refs"]
        ]
        if len(matching) != 1 or matching[0]["paper_id"] != paper_id:
            raise service_error(
                INCOMPLETE_TRANSACTION,
                job_id,
                "/authority",
                "trusted Parse authority receipt is incomplete",
            )
        record = matching[0]
        preview = TrustedParseAuthorityPreview(
            record["authority_id"],
            record["state_id"],
            canonical_digest(record),
            record,
        )
        return preview, True, event
    nonce = nonce_factory()
    if not isinstance(nonce, str) or not nonce or len(nonce) > 128:
        raise service_error(
            SCHEMA_VALIDATION_FAILED,
            job_id,
            "/nonce",
            "trusted Parse preparation nonce is invalid",
        )
    preview = service.preview(
        paper_id=paper_id,
        adapter_name=parser["adapter"],
        adapter_version=parser["version"],
        parser_profile_id=profile_id,
        policy_version=policy_version,
        allowed_operation=ALLOWED_OPERATION,
        idempotency_key=f"trusted-intake:{layout.workspace_id}:{job_id}:{nonce}",
        actor="user",
        expires_at=clock().astimezone(UTC) + ttl,
    )
    return preview, False, None


def preparation_payload(preparation: TrustedParseIntakePreparation) -> dict[str, Any]:
    return {
        "session_option_id": preparation.session_option_id,
        "workspace_id": preparation.workspace_id,
        "job_id": preparation.job_id,
        "job_state_id": preparation.job_state_id,
        "job_state_digest": preparation.job_state_digest,
        "route_suffix": preparation.route_suffix,
        "paper_id": preparation.paper_id,
        "source_ref": preparation.source_ref,
        "source_sha256": preparation.source_sha256,
        "source_name": preparation.source_name,
        "source_size_bytes": preparation.source_size_bytes,
        "parser": preparation.parser,
        "parser_profile_id": preparation.parser_profile_id,
        "policy_version": preparation.policy_version,
        "allowed_operation": preparation.allowed_operation,
        "expires_at": preparation.expires_at,
        "authority_preview_digest": preparation.authority_preview.preview_digest,
        "authority_committed": preparation.authority_committed,
        "authority_event_id": preparation.correlated_authority_event_id,
        "parsed_page_state": preparation.parsed_page_state,
        "correlated_parse_event_id": preparation.correlated_parse_event_id,
    }


def authority_event_for_job(
    layout: WorkspaceLayout,
    job_id: str,
    authority_id: str,
    state_id: str,
) -> dict[str, Any]:
    matches = [
        item
        for item in read_process_events(layout.process_events_path)
        if item.get("job_id") == job_id
        and item["operation"] == "trusted_parse_authority_commit"
        and item["result"] == "success"
        and authority_id in item["output_refs"]
        and state_id in item["output_refs"]
    ]
    if len(matches) != 1:
        raise service_error(
            INCOMPLETE_TRANSACTION,
            job_id,
            "/authority",
            "trusted Parse authority does not have exactly one Job receipt",
        )
    return matches[0]


def correlated_parse_event(
    layout: WorkspaceLayout,
    job_id: str,
    paper_id: str,
    authority: Mapping[str, Any],
    source_sha256: str,
    *,
    require_current_source: bool,
) -> dict[str, Any] | None:
    all_job_events = [
        item
        for item in read_process_events(layout.process_events_path)
        if item.get("job_id") == job_id and item["operation"] == "parse_run"
    ]
    successes = [item for item in all_job_events if item["result"] == "success"]
    if len(successes) > 1:
        raise service_error(
            INCOMPLETE_TRANSACTION,
            job_id,
            "/parse",
            "trusted Parse Job has multiple successful Parse receipts",
        )
    if not successes:
        return None
    event = successes[0]
    expected_refs = [paper_id, authority["authority_id"], authority["state_id"]]
    if event["input_refs"] != expected_refs:
        raise service_error(
            INCOMPLETE_TRANSACTION,
            job_id,
            "/parse",
            "trusted Parse event provenance does not match authority",
        )
    if not (authority["decision_at"] <= event["created_at"] < authority["expires_at"]):
        raise service_error(
            INCOMPLETE_TRANSACTION,
            job_id,
            "/parse",
            "trusted Parse event falls outside the authority interval",
        )
    pages = read_jsonl(layout.parse_path(paper_id), record_kind="parsed-page")
    if not pages or any(
        item["paper_id"] != paper_id
        or item["parse_run_id"] != event["event_id"]
        or item["parser"] != authority["parser"]
        for item in pages
    ):
        raise service_error(
            INCOMPLETE_TRANSACTION,
            job_id,
            "/parse",
            "trusted Parse pages do not match their Job receipt",
        )
    if authority["source_fingerprint"] != {"algorithm": "sha256", "value": source_sha256}:
        raise service_error(
            INCOMPLETE_TRANSACTION,
            job_id,
            "/parse",
            "trusted Parse authority source does not match preparation",
        )
    if require_current_source:
        entries = load_workspace_entries(layout)
        paper = next(
            item
            for item in records_of_kind(entries, "registry-paper")
            if item["paper_id"] == paper_id
        )
        observation = observe_paper_source(layout, entries, paper)
        if observation.state != "current" or observation.live_sha256 != source_sha256:
            raise service_error(
                GROUNDING_MISMATCH,
                job_id,
                "/source",
                "trusted Parse source changed after its receipt",
            )
    return event


def paper_for_job(layout: WorkspaceLayout, job_id: str) -> dict[str, Any]:
    events = [
        item
        for item in read_process_events(layout.process_events_path)
        if item.get("job_id") == job_id
        and item["operation"] == "registry_add"
        and item["result"] == "success"
    ]
    if len(events) != 1 or len(events[0]["output_refs"]) != 1:
        raise service_error(
            INCOMPLETE_TRANSACTION,
            job_id,
            "/registry",
            "trusted Parse Job requires one Registry receipt",
        )
    paper_id = validate_id(events[0]["output_refs"][0], Namespace.PAPER)
    papers = read_jsonl(layout.registry_path, record_kind="registry-paper", id_field="paper_id")
    matching = [item for item in papers if item["paper_id"] == paper_id]
    if len(matching) != 1:
        raise service_error(
            INCOMPLETE_TRANSACTION,
            job_id,
            "/registry",
            "trusted Parse Registry paper is missing or ambiguous",
        )
    return matching[0]


def require_source_association(layout: WorkspaceLayout, job_id: str, paper_id: str) -> None:
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
        raise service_error(
            INCOMPLETE_TRANSACTION,
            job_id,
            "/source",
            "trusted Parse Job requires one source receipt",
        )
    heads = {item["source_asset_id"]: item for item in current_source_asset_heads(states)}
    head = heads.get(roots[0]["source_asset_id"])
    events = [
        item
        for item in read_process_events(layout.process_events_path)
        if item.get("job_id") == job_id
        and item["operation"] == "source_asset_associate"
        and item["result"] == "success"
    ]
    if head is None or head["paper_id"] != paper_id or len(events) != 1:
        raise service_error(
            INCOMPLETE_TRANSACTION,
            job_id,
            "/source",
            "trusted Parse source association is incomplete",
        )


def validate_preparation_source_and_parser(
    layout: WorkspaceLayout,
    preparation: TrustedParseIntakePreparation,
    registry: ParseAdapterRegistry,
) -> None:
    if registry.identity(preparation.parser["adapter"]) != preparation.parser:
        raise service_error(
            TRUST_AUTHORITY_INVALID,
            preparation.job_id,
            "/parser",
            "trusted Parse parser identity changed",
        )
    entries = load_workspace_entries(layout)
    paper = next(
        (item for item in records_of_kind(entries, "registry-paper") if item["paper_id"] == preparation.paper_id),
        None,
    )
    if paper is None:
        raise service_error(
            INCOMPLETE_TRANSACTION,
            preparation.job_id,
            "/paper",
            "trusted Parse paper is missing",
        )
    observation = observe_paper_source(layout, entries, paper)
    if (
        observation.state != "current"
        or observation.source_ref != preparation.source_ref
        or observation.live_sha256 != preparation.source_sha256
    ):
        raise service_error(
            GROUNDING_MISMATCH,
            preparation.job_id,
            "/source",
            "trusted Parse source changed after preparation",
        )


def source_changed(layout: WorkspaceLayout, preparation: TrustedParseIntakePreparation) -> bool:
    try:
        entries = load_workspace_entries(layout)
        paper = next(
            item
            for item in records_of_kind(entries, "registry-paper")
            if item["paper_id"] == preparation.paper_id
        )
        observation = observe_paper_source(layout, entries, paper)
    except (ResearchKBError, StopIteration):
        return True
    return (
        observation.state != "current"
        or observation.source_ref != preparation.source_ref
        or observation.live_sha256 != preparation.source_sha256
    )


def transition_wait(
    jobs: PipelineJobService,
    head: dict[str, Any],
    *,
    status: str,
    current_node: str,
    wait_reason: str,
) -> int:
    writes = 0
    if head["status"] == status and head["current_node"] == current_node and head["wait_reason"] == wait_reason:
        return 0
    if head["status"] != "running" and status != "waiting_user":
        interim = jobs.transition(
            head["job_id"],
            expected_state_id=head["state_id"],
            expected_state_digest=canonical_digest(head),
            status="running",
            current_node="trusted_parse_failure_routing",
            wait_reason=None,
            output_refs=[],
            retry_increment=0,
            recovery_action=None,
            actor="cli",
        )
        head = interim.state
        writes += int(interim.transaction is not None)
    mutation = jobs.transition(
        head["job_id"],
        expected_state_id=head["state_id"],
        expected_state_digest=canonical_digest(head),
        status=status,
        current_node=current_node,
        wait_reason=wait_reason,
        output_refs=[],
        retry_increment=0,
        recovery_action=None,
        actor="cli",
    )
    return writes + int(mutation.transaction is not None)


def semantic_intent(route_suffix: str) -> tuple[str, str | None, str | None]:
    if route_suffix == "primary":
        return "basic_paper_card", "primary", None
    if route_suffix == "review":
        return "basic_review_memory", "review", None
    if route_suffix == "review_mixed":
        return "basic_review_memory", "review", "mixed_document"
    return "basic_paper_card", None, None


def route_suffix(node: object) -> str:
    if not isinstance(node, str):
        raise service_error(
            INVALID_AUTHORITY,
            None,
            "/current_node",
            "trusted Parse Job node is invalid",
        )
    for prefix in TRUSTED_PREFIXES:
        if node.startswith(prefix):
            suffix = node.removeprefix(prefix)
            if suffix in ROUTE_SUFFIXES:
                return suffix
    raise service_error(
        INVALID_AUTHORITY,
        None,
        "/current_node",
        "Pipeline Job is not at a trusted Parse checkpoint",
    )


def require_expected_state(state: dict[str, Any], expected: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(expected, Mapping) or set(expected) != {"state_id", "state_digest"}:
        raise service_error(
            SCHEMA_VALIDATION_FAILED,
            state["job_id"],
            "/expected_state",
            "expected Job state fields do not match the contract",
        )
    state_id = validate_id(expected["state_id"], Namespace.JOB_STATE)
    digest = expected["state_digest"]
    if state["state_id"] != state_id or not isinstance(digest, str) or canonical_digest(state) != digest:
        raise service_error(
            WRITE_CONFLICT,
            state["job_id"],
            "/expected_state",
            "Pipeline Job state changed before trusted Parse action",
        )
    return state


def session_layout(session: WorkspaceSession) -> WorkspaceLayout:
    if not isinstance(session, WorkspaceSession):
        raise service_error(
            SCHEMA_VALIDATION_FAILED,
            None,
            "/session",
            "Core-owned WorkspaceSession is required",
        )
    return session._layout


def service_error(code: str, record_id: str | None, path: str, message: str) -> ResearchKBError:
    return ResearchKBError(Diagnostic(code, "trusted-parse-intake", record_id, path, message))
