from __future__ import annotations

import hashlib
import io
import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from research_kb.catalog.models import canonical_digest
from research_kb.errors import (
    INCOMPLETE_TRANSACTION,
    INVALID_AUTHORITY,
    PARSER_WORKER_FAILED,
    Diagnostic,
    ResearchKBError,
)
from research_kb.services import DeterministicIntakeApplicationService, WorkspaceSessionService
from research_kb.services import TrustedParseIntakeApplicationService
from research_kb.guardian import GuardianService
from research_kb.process_events import read_process_events
from research_kb.parse.pdfplumber_adapter import PdfPlumberTextFlowAdapter
from research_kb.parse.worker_protocol import WorkerParseResult
from research_kb.services.parse import ParseService
from research_kb.services.pipeline_job import PipelineJobService
from research_kb.services.trusted_parse_authority import TrustedParseAuthorityService
from research_kb.storage.json_io import read_jsonl, serialize_jsonl
from tests.pdf_helpers import write_synthetic_pdf
from tests.runtime_helpers import make_runtime_workspace


NOW = datetime(2026, 8, 8, 8, 0, tzinfo=UTC)


def _request(
    payload: bytes,
    *,
    route: str | None = "primary",
    route_reason: str | None = None,
) -> dict[str, object]:
    operation = "basic_review_memory" if route == "review" else "basic_paper_card"
    return {
        "idempotency_key": "trusted-intake-0001",
        "requested_operation": operation,
        "document_route": route,
        "route_reason": route_reason,
        "bibliography": {
            "title": "Synthetic trusted intake",
            "authors": ["A. Example"],
            "year": 2026,
            "doi": None,
        },
        "expected_sha256": hashlib.sha256(payload).hexdigest(),
        "expected_size_bytes": len(payload),
    }


def test_trusted_intake_stops_before_parse_and_generic_resume_rejects(tmp_path: Path) -> None:
    layout = make_runtime_workspace(
        tmp_path,
        local_inbox="./sources/inbox",
        create_local_inbox=True,
    )
    session = WorkspaceSessionService({"alpha": layout.config.path}).open("alpha")
    payload = write_synthetic_pdf(
        tmp_path / "trusted-intake.pdf",
        ["Synthetic trusted page."],
    ).read_bytes()
    service = DeterministicIntakeApplicationService(
        parse_policy="trusted_supervised_parse",
        clock=lambda: NOW,
    )

    started = service.start_upload(session, io.BytesIO(payload), _request(payload))

    assert started["pipeline"]["status"] == "waiting_user"
    assert started["pipeline"]["current_node"] == "trusted_parse_authority_primary"
    assert started["pipeline"]["wait_reason"] == "authority_required"
    assert started["paper_id"] is not None
    assert not layout.parse_path(started["paper_id"]).exists()

    with pytest.raises(ResearchKBError) as caught:
        service.resume(
            session,
            started["pipeline"]["job_id"],
            {
                "state_id": started["pipeline"]["state_id"],
                "state_digest": started["pipeline"]["state_digest"],
            },
            {
                "requested_operation": "basic_paper_card",
                "document_route": "primary",
                "route_reason": None,
                "bibliography": _request(payload)["bibliography"],
            },
        )

    assert caught.value.diagnostic.code == "RKBC-006"
    shown = service.show_job(session, started["pipeline"]["job_id"])
    assert shown["pipeline"]["state_digest"] == canonical_digest(
        PipelineJobService(layout).show(started["pipeline"]["job_id"])["current_state"]
    )


def _trusted_case(
    tmp_path: Path,
    *,
    route: str | None = "primary",
    route_reason: str | None = None,
):
    layout = make_runtime_workspace(
        tmp_path,
        local_inbox="./sources/inbox",
        create_local_inbox=True,
    )
    session = WorkspaceSessionService({"alpha": layout.config.path}).open("alpha")
    payload = write_synthetic_pdf(
        tmp_path / "trusted-case.pdf",
        ["Synthetic trusted page one.", "Synthetic trusted page two."],
    ).read_bytes()
    started = DeterministicIntakeApplicationService(
        parse_policy="trusted_supervised_parse",
        clock=lambda: NOW,
    ).start_upload(
        session,
        io.BytesIO(payload),
        _request(payload, route=route, route_reason=route_reason),
    )
    return layout, session, started


def _expected_state(started: dict[str, object]) -> dict[str, str]:
    pipeline = started["pipeline"]
    assert isinstance(pipeline, dict)
    return {
        "state_id": pipeline["state_id"],
        "state_digest": pipeline["state_digest"],
    }


def _tree_bytes(root: Path) -> dict[Path, bytes]:
    return {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def _worker_result(request) -> WorkerParseResult:
    pages = (
        {
            "pdf_page": 1,
            "printed_page": None,
            "text": "Synthetic trusted worker page one.",
            "locator": "page:1:text",
        },
        {
            "pdf_page": 2,
            "printed_page": None,
            "text": "Synthetic trusted worker page two.",
            "locator": "page:2:text",
        },
    )
    return WorkerParseResult(
        pages=pages,
        source_sha256=request.source_sha256,
        parser={"adapter": request.adapter_name, "version": request.adapter_version},
        output_utf8_bytes=sum(len(item["text"].encode("utf-8")) for item in pages),
    )


@pytest.mark.parametrize(
    ("route", "route_reason", "expected_suffix"),
    [
        ("primary", None, "primary"),
        ("review", None, "review"),
        ("review", "mixed_document", "review_mixed"),
        (None, None, "undecided"),
    ],
)
def test_prepare_is_zero_write_redacted_and_route_bound(
    tmp_path: Path,
    route: str | None,
    route_reason: str | None,
    expected_suffix: str,
) -> None:
    layout, session, started = _trusted_case(
        tmp_path,
        route=route,
        route_reason=route_reason,
    )
    service = TrustedParseIntakeApplicationService(
        clock=lambda: NOW,
        nonce_factory=lambda: "stable-nonce",
    )
    before = _tree_bytes(layout.knowledge_root)

    preparation = service.prepare(
        session,
        started["pipeline"]["job_id"],
        _expected_state(started),
    )
    repeated = service.prepare(
        session,
        started["pipeline"]["job_id"],
        _expected_state(started),
    )
    public = preparation.public_projection()

    assert preparation.route_suffix == expected_suffix
    assert repeated.preparation_digest == preparation.preparation_digest
    assert public["persistent_writes"] == 0
    assert public["source"]["identity_status"] == "current"
    assert public["aggregate_preview_digest"] == preparation.preparation_digest
    serialized = str(public).lower()
    for forbidden in ("source_ref", "fingerprint", "authority_id", "state_id", str(layout.knowledge_root).lower()):
        assert forbidden not in serialized
    assert _tree_bytes(layout.knowledge_root) == before


def test_approval_uses_job_correlated_supervised_parse_and_continues_trunk(tmp_path: Path) -> None:
    layout, session, started = _trusted_case(tmp_path)
    service = TrustedParseIntakeApplicationService(
        clock=lambda: NOW,
        nonce_factory=lambda: "approval-nonce",
    )
    preparation = service.prepare(session, started["pipeline"]["job_id"], _expected_state(started))

    approved = service.approve(
        session,
        preparation,
        aggregate_preview_digest=preparation.preparation_digest,
        actor="user",
    )

    assert approved.outcome == "continued"
    assert approved.parse_run_id is not None
    assert approved.result["pipeline"]["current_node"] == "primary_semantic_gate"
    pages = read_jsonl(layout.parse_path(started["paper_id"]), record_kind="parsed-page")
    assert {item["parse_run_id"] for item in pages} == {approved.parse_run_id}
    events = read_process_events(layout.process_events_path)
    authority_event = next(item for item in events if item["operation"] == "trusted_parse_authority_commit")
    parse_event = next(item for item in events if item["event_id"] == approved.parse_run_id)
    assert authority_event["job_id"] == started["pipeline"]["job_id"]
    assert parse_event["job_id"] == started["pipeline"]["job_id"]
    assert parse_event["input_refs"][1:] == authority_event["output_refs"]

    current = PipelineJobService(layout).show(started["pipeline"]["job_id"])["current_state"]
    replay = DeterministicIntakeApplicationService(
        parse_policy="trusted_supervised_parse",
        clock=lambda: NOW,
    ).resume(
        session,
        started["pipeline"]["job_id"],
        {"state_id": current["state_id"], "state_digest": canonical_digest(current)},
        {
            "requested_operation": "basic_paper_card",
            "document_route": "primary",
            "route_reason": None,
            "bibliography": {
                "title": "Synthetic trusted intake",
                "authors": ["A. Example"],
                "year": 2026,
                "doi": None,
            },
        },
    )
    assert replay["persistent_writes"] == 0
    assert replay["pipeline"]["state_id"] == current["state_id"]


def test_trusted_undecided_route_resume_reuses_receipted_parse_without_new_parse(
    tmp_path: Path,
) -> None:
    layout, session, started = _trusted_case(tmp_path, route=None)
    trusted = TrustedParseIntakeApplicationService(
        clock=lambda: NOW,
        nonce_factory=lambda: "undecided-nonce",
    )
    preparation = trusted.prepare(session, started["pipeline"]["job_id"], _expected_state(started))
    approved = trusted.approve(
        session,
        preparation,
        aggregate_preview_digest=preparation.preparation_digest,
        actor="user",
    )
    assert approved.result["pipeline"]["current_node"] == "semantic_route"
    assert approved.result["pipeline"]["wait_reason"] == "route_ambiguous"
    before = read_process_events(layout.process_events_path)

    current = PipelineJobService(layout).show(started["pipeline"]["job_id"])["current_state"]
    resumed = DeterministicIntakeApplicationService(
        parse_policy="trusted_supervised_parse",
        clock=lambda: NOW,
    ).resume(
        session,
        started["pipeline"]["job_id"],
        {"state_id": current["state_id"], "state_digest": canonical_digest(current)},
        {
            "requested_operation": "basic_paper_card",
            "document_route": "primary",
            "route_reason": None,
            "bibliography": _request(b"placeholder")["bibliography"],
        },
    )
    after = read_process_events(layout.process_events_path)

    assert resumed["pipeline"]["status"] == "completed"
    assert resumed["pipeline"]["current_node"] == "primary_semantic_gate"
    for operation in ("trusted_parse_authority_commit", "parse_run"):
        assert len([item for item in before if item["operation"] == operation]) == 1
        assert len([item for item in after if item["operation"] == operation]) == 1


@pytest.mark.parametrize("corruption", ["missing_authority", "broken_parse_link"])
def test_trusted_undecided_route_resume_rejects_incomplete_parse_lineage(
    tmp_path: Path,
    corruption: str,
) -> None:
    layout, session, started = _trusted_case(tmp_path, route=None)
    trusted = TrustedParseIntakeApplicationService(
        clock=lambda: NOW,
        nonce_factory=lambda: "undecided-corrupt-nonce",
    )
    preparation = trusted.prepare(session, started["pipeline"]["job_id"], _expected_state(started))
    approved = trusted.approve(
        session,
        preparation,
        aggregate_preview_digest=preparation.preparation_digest,
        actor="user",
    )
    assert approved.result["pipeline"]["wait_reason"] == "route_ambiguous"
    events = read_process_events(layout.process_events_path)
    if corruption == "missing_authority":
        events = [item for item in events if item["operation"] != "trusted_parse_authority_commit"]
    else:
        parse_event = next(item for item in events if item["operation"] == "parse_run")
        parse_event["input_refs"] = parse_event["input_refs"][:-1]
    layout.process_events_path.write_bytes(serialize_jsonl(events))
    current = PipelineJobService(layout).show(started["pipeline"]["job_id"])["current_state"]

    with pytest.raises(ResearchKBError) as caught:
        DeterministicIntakeApplicationService(
            parse_policy="trusted_supervised_parse",
            clock=lambda: NOW,
        ).resume(
            session,
            started["pipeline"]["job_id"],
            {"state_id": current["state_id"], "state_digest": canonical_digest(current)},
            {
                "requested_operation": "basic_paper_card",
                "document_route": "primary",
                "route_reason": None,
                "bibliography": _request(b"placeholder")["bibliography"],
            },
        )

    assert caught.value.diagnostic.code == INCOMPLETE_TRANSACTION
    unchanged = PipelineJobService(layout).show(started["pipeline"]["job_id"])["current_state"]
    assert unchanged == current


def test_source_drift_after_preparation_routes_to_waiting_source(tmp_path: Path) -> None:
    layout, session, started = _trusted_case(tmp_path)
    service = TrustedParseIntakeApplicationService(clock=lambda: NOW, nonce_factory=lambda: "drift-nonce")
    preparation = service.prepare(session, started["pipeline"]["job_id"], _expected_state(started))
    _root, source = layout.resolve_source(
        preparation.source_ref["root_id"],
        preparation.source_ref["relative_path"],
    )
    source.write_bytes(source.read_bytes() + b"changed")

    result = service.approve(
        session,
        preparation,
        aggregate_preview_digest=preparation.preparation_digest,
        actor="user",
    )

    assert result.outcome == "waiting"
    assert result.result["pipeline"]["status"] == "waiting_source"
    assert result.result["pipeline"]["wait_reason"] == "source_changed"
    assert not layout.parse_path(started["paper_id"]).exists()


def test_accepted_cancellation_is_terminal_without_page_promotion(tmp_path: Path) -> None:
    layout, session, started = _trusted_case(tmp_path)
    service = TrustedParseIntakeApplicationService(clock=lambda: NOW, nonce_factory=lambda: "cancel-nonce")
    preparation = service.prepare(session, started["pipeline"]["job_id"], _expected_state(started))

    result = service.approve(
        session,
        preparation,
        aggregate_preview_digest=preparation.preparation_digest,
        actor="user",
        cancel_check=lambda: True,
    )

    assert result.outcome == "cancelled"
    assert result.result["pipeline"]["status"] == "cancelled"
    assert not layout.parse_path(started["paper_id"]).exists()


def test_authority_commit_crash_reconstructs_and_resumes_without_duplicate(tmp_path: Path) -> None:
    layout, session, started = _trusted_case(tmp_path)
    crash = {"enabled": True}

    def hook(phase: str) -> None:
        if crash["enabled"] and phase == "authority_commit":
            raise RuntimeError("synthetic authority checkpoint crash")

    service = TrustedParseIntakeApplicationService(
        clock=lambda: NOW,
        nonce_factory=lambda: "crash-nonce",
        operation_hook=hook,
    )
    first = service.prepare(session, started["pipeline"]["job_id"], _expected_state(started))
    with pytest.raises(RuntimeError, match="authority checkpoint"):
        service.approve(
            session,
            first,
            aggregate_preview_digest=first.preparation_digest,
            actor="user",
        )

    crash["enabled"] = False
    recovered = service.prepare(session, started["pipeline"]["job_id"], _expected_state(started))
    result = service.approve(
        session,
        recovered,
        aggregate_preview_digest=recovered.preparation_digest,
        actor="user",
    )

    assert recovered.authority_committed is True
    assert result.outcome == "continued"
    events = read_process_events(layout.process_events_path)
    assert len([item for item in events if item["operation"] == "trusted_parse_authority_commit"]) == 1
    assert len([item for item in events if item["operation"] == "parse_run" and item["result"] == "success"]) == 1


def test_parse_commit_crash_recovery_skips_duplicate_worker_and_parse(tmp_path: Path) -> None:
    layout, session, started = _trusted_case(tmp_path)
    crash = {"enabled": True}

    def hook(phase: str) -> None:
        if crash["enabled"] and phase == "parse_commit":
            raise RuntimeError("synthetic Parse checkpoint crash")

    service = TrustedParseIntakeApplicationService(
        clock=lambda: NOW,
        nonce_factory=lambda: "parse-crash-nonce",
        operation_hook=hook,
    )
    first = service.prepare(session, started["pipeline"]["job_id"], _expected_state(started))
    with pytest.raises(RuntimeError, match="Parse checkpoint"):
        service.approve(
            session,
            first,
            aggregate_preview_digest=first.preparation_digest,
            actor="user",
        )

    crash["enabled"] = False
    current = PipelineJobService(layout).show(started["pipeline"]["job_id"])["current_state"]
    recovered = service.prepare(
        session,
        started["pipeline"]["job_id"],
        {"state_id": current["state_id"], "state_digest": canonical_digest(current)},
    )
    result = service.approve(
        session,
        recovered,
        aggregate_preview_digest=recovered.preparation_digest,
        actor="user",
    )

    assert recovered.correlated_parse_event_id is not None
    assert result.parse_run_id == recovered.correlated_parse_event_id
    events = read_process_events(layout.process_events_path)
    assert len([item for item in events if item["operation"] == "parse_run" and item["result"] == "success"]) == 1


def test_execution_transition_crash_recovers_one_authority_and_parse(tmp_path: Path) -> None:
    layout, session, started = _trusted_case(tmp_path)
    crash = {"enabled": True}

    def hook(phase: str) -> None:
        if crash["enabled"] and phase == "execution_transition":
            raise RuntimeError("synthetic execution transition crash")

    service = TrustedParseIntakeApplicationService(
        clock=lambda: NOW,
        nonce_factory=lambda: "execution-crash-nonce",
        operation_hook=hook,
        worker_runner=_worker_result,
    )
    first = service.prepare(session, started["pipeline"]["job_id"], _expected_state(started))
    with pytest.raises(RuntimeError, match="execution transition"):
        service.approve(
            session,
            first,
            aggregate_preview_digest=first.preparation_digest,
            actor="user",
        )

    crash["enabled"] = False
    current = PipelineJobService(layout).show(started["pipeline"]["job_id"])["current_state"]
    assert current["current_node"] == "trusted_parse_execution_primary"
    recovered = service.prepare(
        session,
        started["pipeline"]["job_id"],
        {"state_id": current["state_id"], "state_digest": canonical_digest(current)},
    )
    result = service.approve(
        session,
        recovered,
        aggregate_preview_digest=recovered.preparation_digest,
        actor="user",
    )

    assert result.outcome == "continued"
    events = read_process_events(layout.process_events_path)
    assert len(
        [
            item
            for item in events
            if item.get("job_id") == started["pipeline"]["job_id"]
            and item["operation"] == "trusted_parse_authority_commit"
            and item["result"] == "success"
        ]
    ) == 1
    assert len(
        [
            item
            for item in events
            if item.get("job_id") == started["pipeline"]["job_id"]
            and item["operation"] == "parse_run"
            and item["result"] == "success"
        ]
    ) == 1


def test_reconcile_transition_crash_recovers_without_duplicate_parse(tmp_path: Path) -> None:
    layout, session, started = _trusted_case(tmp_path)
    crash = {"enabled": True}
    worker_calls: list[str] = []

    def hook(phase: str) -> None:
        if crash["enabled"] and phase == "reconcile_transition":
            raise RuntimeError("synthetic reconcile transition crash")

    def runner(request):
        worker_calls.append(request.operation_id)
        return _worker_result(request)

    service = TrustedParseIntakeApplicationService(
        clock=lambda: NOW,
        nonce_factory=lambda: "reconcile-crash-nonce",
        operation_hook=hook,
        worker_runner=runner,
    )
    first = service.prepare(session, started["pipeline"]["job_id"], _expected_state(started))
    with pytest.raises(RuntimeError, match="reconcile transition"):
        service.approve(
            session,
            first,
            aggregate_preview_digest=first.preparation_digest,
            actor="user",
        )

    crash["enabled"] = False
    current = PipelineJobService(layout).show(started["pipeline"]["job_id"])["current_state"]
    assert current["current_node"] == "trusted_parse_reconcile_primary"
    recovered = service.prepare(
        session,
        started["pipeline"]["job_id"],
        {"state_id": current["state_id"], "state_digest": canonical_digest(current)},
    )
    result = service.approve(
        session,
        recovered,
        aggregate_preview_digest=recovered.preparation_digest,
        actor="user",
    )

    assert result.outcome == "continued"
    assert len(worker_calls) == 1
    events = read_process_events(layout.process_events_path)
    assert len(
        [
            item
            for item in events
            if item.get("job_id") == started["pipeline"]["job_id"]
            and item["operation"] == "trusted_parse_authority_commit"
            and item["result"] == "success"
        ]
    ) == 1
    assert len(
        [
            item
            for item in events
            if item.get("job_id") == started["pipeline"]["job_id"]
            and item["operation"] == "parse_run"
            and item["result"] == "success"
        ]
    ) == 1


def test_worker_failure_preserves_existing_pages_and_routes_parse_failed(tmp_path: Path) -> None:
    layout, session, started = _trusted_case(tmp_path)
    ParseService(layout).run(
        paper_id=started["paper_id"],
        adapter=PdfPlumberTextFlowAdapter(),
        actor="cli",
    )
    parsed_path = layout.parse_path(started["paper_id"])
    before = parsed_path.read_bytes()
    before_digest = hashlib.sha256(before).hexdigest()

    def failing_runner(_request):
        raise ResearchKBError(
            Diagnostic(
                PARSER_WORKER_FAILED,
                "parser-worker",
                None,
                "/worker",
                "synthetic worker failure",
            )
        )

    service = TrustedParseIntakeApplicationService(
        clock=lambda: NOW,
        nonce_factory=lambda: "worker-failure-nonce",
        worker_runner=failing_runner,
    )
    preparation = service.prepare(session, started["pipeline"]["job_id"], _expected_state(started))
    result = service.approve(
        session,
        preparation,
        aggregate_preview_digest=preparation.preparation_digest,
        actor="user",
    )

    assert result.outcome == "waiting"
    assert result.result["pipeline"]["status"] == "waiting_source"
    assert result.result["pipeline"]["wait_reason"] == "parse_failed"
    after = parsed_path.read_bytes()
    assert after == before
    assert hashlib.sha256(after).hexdigest() == before_digest
    events = read_process_events(layout.process_events_path)
    assert not any(
        item.get("job_id") == started["pipeline"]["job_id"]
        and item["operation"] == "parse_run"
        and item["result"] == "success"
        for item in events
    )


@pytest.mark.parametrize("invalidation", ["expired", "revoked"])
def test_authority_invalidated_before_worker_start_does_not_promote_pages(
    tmp_path: Path,
    invalidation: str,
) -> None:
    layout, session, started = _trusted_case(tmp_path)
    now = {"value": NOW}
    worker_calls: list[str] = []
    preparation = None

    def clock() -> datetime:
        return now["value"]

    def hook(phase: str) -> None:
        if phase != "execution_transition":
            return
        assert preparation is not None
        if invalidation == "expired":
            now["value"] = NOW + timedelta(hours=1)
        else:
            TrustedParseAuthorityService(layout, clock=clock).revoke(
                preparation.authority_preview.authority_id,
                actor="user",
                reason="synthetic pre-worker revocation",
            )

    def runner(request):
        worker_calls.append(request.operation_id)
        return _worker_result(request)

    service = TrustedParseIntakeApplicationService(
        clock=clock,
        nonce_factory=lambda: f"authority-{invalidation}-nonce",
        operation_hook=hook,
        worker_runner=runner,
    )
    preparation = service.prepare(session, started["pipeline"]["job_id"], _expected_state(started))
    result = service.approve(
        session,
        preparation,
        aggregate_preview_digest=preparation.preparation_digest,
        actor="user",
    )

    assert result.outcome == "waiting"
    assert result.result["pipeline"]["status"] == "waiting_user"
    assert result.result["pipeline"]["wait_reason"] == "authority_required"
    assert worker_calls == []
    assert not layout.parse_path(started["paper_id"]).exists()


def test_wrong_workspace_session_fails_closed_before_authority_or_parse_write(tmp_path: Path) -> None:
    (tmp_path / "source").mkdir()
    (tmp_path / "other").mkdir()
    layout, session, started = _trusted_case(tmp_path / "source")
    other_layout = make_runtime_workspace(
        tmp_path / "other",
        local_inbox="./sources/inbox",
        create_local_inbox=True,
    )
    other_session = WorkspaceSessionService({"beta": other_layout.config.path}).open("beta")
    service = TrustedParseIntakeApplicationService(
        clock=lambda: NOW,
        nonce_factory=lambda: "wrong-session-nonce",
        worker_runner=_worker_result,
    )
    preparation = service.prepare(session, started["pipeline"]["job_id"], _expected_state(started))
    before_source_workspace = _tree_bytes(layout.knowledge_root)
    before_other_workspace = _tree_bytes(other_layout.knowledge_root)

    with pytest.raises(ResearchKBError) as caught:
        service.approve(
            other_session,
            preparation,
            aggregate_preview_digest=preparation.preparation_digest,
            actor="user",
        )

    assert caught.value.diagnostic.code == INVALID_AUTHORITY
    assert _tree_bytes(layout.knowledge_root) == before_source_workspace
    assert _tree_bytes(other_layout.knowledge_root) == before_other_workspace
    for target in (layout, other_layout):
        events = read_process_events(target.process_events_path)
        assert not any(
            item["operation"] in {"trusted_parse_authority_commit", "parse_run"}
            for item in events
        )


def test_watched_inbox_trusted_path_stops_at_authority_and_completes_facade(tmp_path: Path) -> None:
    layout = make_runtime_workspace(
        tmp_path,
        local_inbox="./sources/inbox",
        create_local_inbox=True,
    )
    session = WorkspaceSessionService({"alpha": layout.config.path}).open("alpha")
    source = write_synthetic_pdf(
        layout.local_inbox / "trusted-watched.pdf",
        ["Synthetic watched trusted page one.", "Synthetic watched trusted page two."],
    )
    old = (NOW - timedelta(minutes=10)).timestamp()
    os.utime(source, (old, old))
    intake = DeterministicIntakeApplicationService(
        parse_policy="trusted_supervised_parse",
        clock=lambda: NOW,
    )
    scan = intake.scan_inbox(session, max_entries=10, min_stable_age_seconds=30)
    candidate = scan["candidates"][0]
    started = intake.start_inbox(
        session,
        candidate["candidate_token"],
        {
            "idempotency_key": "trusted-watched-0001",
            "requested_operation": "basic_review_memory",
            "document_route": "review",
            "route_reason": None,
            "bibliography": {
                "title": "Synthetic watched trusted review",
                "authors": [],
                "year": 2026,
                "doi": None,
            },
            "min_stable_age_seconds": 30,
        },
    )

    assert started["pipeline"]["status"] == "waiting_user"
    assert started["pipeline"]["current_node"] == "trusted_parse_authority_review"
    assert not layout.parse_path(started["paper_id"]).exists()

    service = TrustedParseIntakeApplicationService(
        clock=lambda: NOW,
        nonce_factory=lambda: "trusted-watched-nonce",
        worker_runner=_worker_result,
    )
    preparation = service.prepare(session, started["pipeline"]["job_id"], _expected_state(started))
    result = service.approve(
        session,
        preparation,
        aggregate_preview_digest=preparation.preparation_digest,
        actor="user",
    )

    assert result.outcome == "continued"
    assert result.result["pipeline"]["status"] == "completed"
    assert result.result["pipeline"]["current_node"] == "review_semantic_gate"
    assert result.parse_run_id is not None
    assert layout.parse_path(started["paper_id"]).exists()


def test_unrelated_existing_pages_require_supervised_replacement(tmp_path: Path) -> None:
    layout, session, started = _trusted_case(tmp_path)
    _legacy_pages, legacy = ParseService(layout).run(
        paper_id=started["paper_id"],
        adapter=PdfPlumberTextFlowAdapter(),
        actor="cli",
    )
    service = TrustedParseIntakeApplicationService(
        clock=lambda: NOW,
        nonce_factory=lambda: "replacement-nonce",
    )

    preparation = service.prepare(session, started["pipeline"]["job_id"], _expected_state(started))
    result = service.approve(
        session,
        preparation,
        aggregate_preview_digest=preparation.preparation_digest,
        actor="user",
    )

    assert preparation.parsed_page_state == "supervised_reparse_required"
    assert preparation.public_projection()["supervised_reparse_required"] is True
    assert result.parse_run_id != legacy.event_id
    pages = read_jsonl(layout.parse_path(started["paper_id"]), record_kind="parsed-page")
    assert {item["parse_run_id"] for item in pages} == {result.parse_run_id}


def test_promotion_barrier_closes_cancellation_window(tmp_path: Path) -> None:
    layout, session, started = _trusted_case(tmp_path)
    cancellation = {"requested": False}
    service = TrustedParseIntakeApplicationService(
        clock=lambda: NOW,
        nonce_factory=lambda: "late-cancel-nonce",
    )
    preparation = service.prepare(session, started["pipeline"]["job_id"], _expected_state(started))

    result = service.approve(
        session,
        preparation,
        aggregate_preview_digest=preparation.preparation_digest,
        actor="user",
        cancel_check=lambda: cancellation["requested"],
        before_promotion=lambda: cancellation.update(requested=True),
    )

    assert cancellation["requested"] is True
    assert result.outcome == "continued"
    assert result.parse_run_id is not None
    assert layout.parse_path(started["paper_id"]).exists()


def test_stale_cas_and_tampered_preparation_fail_before_authority_write(tmp_path: Path) -> None:
    layout, session, started = _trusted_case(tmp_path)
    service = TrustedParseIntakeApplicationService(
        clock=lambda: NOW,
        nonce_factory=lambda: "tamper-nonce",
    )
    preparation = service.prepare(session, started["pipeline"]["job_id"], _expected_state(started))
    tampered = replace(preparation, parser={"adapter": "pdfplumber-text-flow", "version": "0.0"})

    with pytest.raises(ResearchKBError) as changed:
        service.approve(
            session,
            tampered,
            aggregate_preview_digest=preparation.preparation_digest,
            actor="user",
        )
    assert changed.value.diagnostic.code == "RKBC-026"
    assert not layout.trusted_parse_authorities_path.exists()

    tampered_source_name = replace(preparation, source_name="tampered-source.pdf")
    with pytest.raises(ResearchKBError) as source_name_changed:
        service.approve(
            session,
            tampered_source_name,
            aggregate_preview_digest=preparation.preparation_digest,
            actor="user",
        )
    assert source_name_changed.value.diagnostic.code == "RKBC-026"
    assert not layout.trusted_parse_authorities_path.exists()
    assert not layout.parse_path(started["paper_id"]).exists()

    PipelineJobService(layout).cancel(
        started["pipeline"]["job_id"],
        expected_state_id=started["pipeline"]["state_id"],
        expected_state_digest=started["pipeline"]["state_digest"],
        actor="user",
    )
    with pytest.raises(ResearchKBError) as stale:
        service.approve(
            session,
            preparation,
            aggregate_preview_digest=preparation.preparation_digest,
            actor="user",
        )
    assert stale.value.diagnostic.code == "RKBC-017"
    assert not layout.trusted_parse_authorities_path.exists()


def test_guardian_validates_complete_trusted_parse_provenance_join(tmp_path: Path) -> None:
    layout, session, started = _trusted_case(tmp_path)
    service = TrustedParseIntakeApplicationService(
        clock=lambda: NOW,
        nonce_factory=lambda: "guardian-nonce",
    )
    preparation = service.prepare(session, started["pipeline"]["job_id"], _expected_state(started))
    service.approve(
        session,
        preparation,
        aggregate_preview_digest=preparation.preparation_digest,
        actor="user",
    )
    assert GuardianService(layout).check(write_report=False).report["findings"] == []

    events = read_process_events(layout.process_events_path)
    parse_event = next(item for item in events if item["operation"] == "parse_run")
    parse_event["input_refs"] = parse_event["input_refs"][:-1]
    layout.process_events_path.write_bytes(serialize_jsonl(events))

    findings = GuardianService(layout).check(write_report=False).report["findings"]
    assert any("trusted Parse" in item["message"] for item in findings)
