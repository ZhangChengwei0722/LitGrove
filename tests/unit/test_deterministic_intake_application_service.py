from __future__ import annotations

import hashlib
import io
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from research_kb.catalog.models import canonical_digest
from research_kb.errors import WRITE_CONFLICT, Diagnostic, ResearchKBError
from research_kb.services import DeterministicIntakeApplicationService, WorkspaceSessionService
from research_kb.services.pipeline_job import PipelineJobService
from research_kb.source_assets import current_source_asset_heads
from research_kb.identifiers import Namespace, allocate_id
from research_kb.storage.json_io import read_jsonl, serialize_jsonl
from tests.pdf_helpers import write_synthetic_pdf
from tests.runtime_helpers import make_runtime_workspace


NOW = datetime(2026, 7, 31, 1, 0, tzinfo=timezone.utc)


def _session(tmp_path: Path):
    layout = make_runtime_workspace(
        tmp_path,
        local_inbox="./sources/inbox",
        create_local_inbox=True,
    )
    session = WorkspaceSessionService({"alpha": layout.config.path}).open("alpha")
    return layout, session


def _pdf_bytes(tmp_path: Path) -> bytes:
    source = write_synthetic_pdf(
        tmp_path / "synthetic-intake.pdf",
        ["Synthetic deterministic intake text for source adequacy."],
    )
    return source.read_bytes()


def _bibliography() -> dict[str, object]:
    return {
        "title": "Synthetic deterministic intake",
        "authors": ["Fixture Author"],
        "year": 2026,
        "doi": None,
    }


def _upload_request(
    payload: bytes,
    *,
    idempotency_key: str = "browser-upload-1",
    requested_operation: str = "basic_paper_card",
    document_route: str | None = None,
    route_reason: str | None = None,
) -> dict[str, object]:
    return {
        "idempotency_key": idempotency_key,
        "requested_operation": requested_operation,
        "document_route": document_route,
        "route_reason": route_reason,
        "bibliography": _bibliography(),
        "expected_sha256": hashlib.sha256(payload).hexdigest(),
        "expected_size_bytes": len(payload),
    }


def _resume_request(
    *,
    requested_operation: str = "basic_paper_card",
    document_route: str | None = None,
    route_reason: str | None = None,
) -> dict[str, object]:
    return {
        "requested_operation": requested_operation,
        "document_route": document_route,
        "route_reason": route_reason,
        "bibliography": _bibliography(),
    }


def _records(layout):
    jobs = read_jsonl(layout.pipeline_jobs_path, record_kind="pipeline-job-state")
    papers = read_jsonl(layout.registry_path, record_kind="registry-paper")
    sources = read_jsonl(layout.source_assets_path, record_kind="source-asset-state")
    events = read_jsonl(layout.process_events_path, record_kind="process-event")
    return jobs, papers, sources, events


def test_upload_reaches_route_wait_and_exact_replay_is_zero_write(tmp_path: Path) -> None:
    layout, session = _session(tmp_path)
    payload = _pdf_bytes(tmp_path)
    service = DeterministicIntakeApplicationService(clock=lambda: NOW)

    first = service.start_upload(session, io.BytesIO(payload), _upload_request(payload))
    before = {
        path: path.read_bytes()
        for path in layout.knowledge_root.rglob("*")
        if path.is_file()
    }
    replay = service.start_upload(session, io.BytesIO(payload), _upload_request(payload))

    assert first["pipeline"]["status"] == "waiting_user"
    assert first["pipeline"]["wait_reason"] == "route_ambiguous"
    assert first["source_adequacy"]["gate_status"] == "allowed"
    assert replay["persistent_writes"] == 0
    assert replay["pipeline"] == first["pipeline"]
    assert before == {
        path: path.read_bytes()
        for path in layout.knowledge_root.rglob("*")
        if path.is_file()
    }
    assert not layout.paper_card_path(first["paper_id"]).exists()
    assert not layout.evidence_path(first["paper_id"]).exists()
    assert not layout.review_memory_path(first["paper_id"]).exists()
    assert not layout.review_queue_path.exists()


@pytest.mark.parametrize(
    ("requested_operation", "document_route", "route_reason", "expected_node"),
    [
        ("basic_paper_card", "primary", None, "primary_semantic_gate"),
        ("basic_review_memory", "review", None, "review_semantic_gate"),
        (
            "basic_review_memory",
            "review",
            "mixed_document",
            "review_semantic_gate_mixed_document",
        ),
    ],
)
def test_upload_route_presets_reach_the_expected_semantic_boundary(
    tmp_path: Path,
    requested_operation: str,
    document_route: str,
    route_reason: str | None,
    expected_node: str,
) -> None:
    _, session = _session(tmp_path)
    payload = _pdf_bytes(tmp_path)
    service = DeterministicIntakeApplicationService(clock=lambda: NOW)
    request = _upload_request(
        payload,
        requested_operation=requested_operation,
        document_route=document_route,
        route_reason=route_reason,
    )
    result = service.start_upload(
        session,
        io.BytesIO(payload),
        request,
    )
    replay = service.start_upload(session, io.BytesIO(payload), request)

    assert result["pipeline"]["status"] == "completed"
    assert result["pipeline"]["current_node"] == expected_node
    assert result["document_route"] == document_route
    assert result["route_reason"] == route_reason
    assert replay["persistent_writes"] == 0
    assert replay["pipeline"] == result["pipeline"]


def test_mixed_document_rejects_legacy_basic_paper_card_operation(tmp_path: Path) -> None:
    layout, session = _session(tmp_path)
    payload = _pdf_bytes(tmp_path)
    before = {
        path: path.read_bytes()
        for path in layout.knowledge_root.rglob("*")
        if path.is_file()
    }

    with pytest.raises(ResearchKBError, match="basic Review Memory"):
        DeterministicIntakeApplicationService(clock=lambda: NOW).start_upload(
            session,
            io.BytesIO(payload),
            _upload_request(
                payload,
                requested_operation="basic_paper_card",
                document_route="review",
                route_reason="mixed_document",
            ),
        )

    assert before == {
        path: path.read_bytes()
        for path in layout.knowledge_root.rglob("*")
        if path.is_file()
    }


def test_watched_inbox_scan_is_redacted_and_review_route_is_replay_safe(tmp_path: Path) -> None:
    layout, session = _session(tmp_path)
    source = write_synthetic_pdf(
        layout.local_inbox / "manual-review.pdf",
        ["Synthetic review background for deterministic intake."],
    )
    old = (NOW - timedelta(minutes=10)).timestamp()
    os.utime(source, (old, old))
    service = DeterministicIntakeApplicationService(clock=lambda: NOW)

    scan = service.scan_inbox(session, max_entries=10, min_stable_age_seconds=30)
    candidate = scan["candidates"][0]
    request = {
        "idempotency_key": "watched-review-1",
        "requested_operation": "basic_review_memory",
        "document_route": "review",
        "route_reason": None,
        "bibliography": {
            "title": "Synthetic watched review",
            "authors": [],
            "year": 2026,
            "doi": None,
        },
        "min_stable_age_seconds": 30,
    }
    first = service.start_inbox(session, candidate["candidate_token"], request)
    replay = service.start_inbox(session, candidate["candidate_token"], request)

    assert set(candidate) == {"candidate_token", "name", "size_bytes"}
    assert first["pipeline"]["current_node"] == "review_semantic_gate"
    assert replay["persistent_writes"] == 0
    rendered = json.dumps({"scan": scan, "result": first}, sort_keys=True)
    assert str(layout.local_inbox) not in rendered
    for forbidden in ("source_ref", "source_fingerprint", "relative_path", "root_id"):
        assert forbidden not in rendered


@pytest.mark.parametrize("crash_phase", ["source_receipt", "registry_add", "source_association"])
def test_fault_injection_recovers_one_job_paper_and_source_asset(
    tmp_path: Path,
    crash_phase: str,
) -> None:
    class Crash(BaseException):
        pass

    layout, session = _session(tmp_path)
    payload = _pdf_bytes(tmp_path)
    request = _upload_request(payload, idempotency_key=f"crash-{crash_phase}")

    def crash(current: str) -> None:
        if current == crash_phase:
            raise Crash()

    with pytest.raises(Crash):
        DeterministicIntakeApplicationService(
            clock=lambda: NOW,
            operation_hook=crash,
        ).start_upload(session, io.BytesIO(payload), request)

    resumed = DeterministicIntakeApplicationService(clock=lambda: NOW).start_upload(
        session,
        io.BytesIO(payload),
        request,
    )
    jobs, papers, sources, events = _records(layout)

    assert resumed["pipeline"]["status"] == "waiting_user"
    assert len({item["job_id"] for item in jobs}) == 1
    assert len(papers) == 1
    assert len([item for item in sources if item["revision"] == 1]) == 1
    assert len(current_source_asset_heads(sources)) == 1
    assert len([item for item in events if item["operation"] == "registry_add"]) == 1
    assert len([item for item in events if item["operation"] == "source_asset_associate"]) == 1


def test_resume_publishes_receipted_upload_partial_without_original_stream(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    layout, session = _session(tmp_path)
    payload = _pdf_bytes(tmp_path)
    request = _upload_request(payload, idempotency_key="streamless-partial-recovery")
    service = DeterministicIntakeApplicationService(clock=lambda: NOW)

    def interrupted_publish(*_args, **_kwargs) -> None:
        raise ResearchKBError(
            Diagnostic(
                WRITE_CONFLICT,
                "local-source-intake",
                None,
                "",
                "synthetic legacy publication failure",
            )
        )

    monkeypatch.setattr(
        "research_kb.services.local_source_intake._publish_owned",
        interrupted_publish,
    )
    with pytest.raises(ResearchKBError):
        service.start_upload(session, io.BytesIO(payload), request)
    monkeypatch.undo()

    current = PipelineJobService(layout).list(page_size=10, cursor=None)["jobs"][0]
    state = PipelineJobService(layout).show(current["job_id"])["current_state"]
    resumed = service.resume(
        session,
        state["job_id"],
        {
            "state_id": state["state_id"],
            "state_digest": canonical_digest(state),
        },
        _resume_request(document_route="primary"),
    )

    assert resumed["pipeline"]["current_node"] == "primary_semantic_gate"
    assert resumed["pipeline"]["status"] == "completed"
    assert (layout.local_inbox / f"{state['job_id']}.pdf").read_bytes() == payload
    assert not list(layout.local_inbox.glob(".research-kb-copy-*.part.pdf"))


@pytest.mark.parametrize("mutation", ["missing", "mismatched_outputs", "duplicate"])
def test_source_receipt_requires_one_matching_success_event(
    tmp_path: Path,
    mutation: str,
) -> None:
    layout, session = _session(tmp_path)
    payload = _pdf_bytes(tmp_path)
    request = _upload_request(payload, idempotency_key=f"receipt-{mutation}")
    service = DeterministicIntakeApplicationService(clock=lambda: NOW)
    service.start_upload(session, io.BytesIO(payload), request)
    events = read_jsonl(layout.process_events_path, record_kind="process-event")
    source_event = next(
        item
        for item in events
        if item["operation"] == "source_asset_copy_into_local_inbox"
    )
    if mutation == "missing":
        events.remove(source_event)
    elif mutation == "mismatched_outputs":
        source_event["output_refs"] = source_event["output_refs"][:1]
    else:
        duplicate = {
            **source_event,
            "event_id": allocate_id(Namespace.PROCESS_EVENT),
        }
        events.append(duplicate)
    layout.process_events_path.write_bytes(serialize_jsonl(events))

    with pytest.raises(ResearchKBError) as rejected:
        service.start_upload(session, io.BytesIO(payload), request)

    assert rejected.value.diagnostic.code == "RKBC-018"


def test_changed_start_intent_and_stale_resume_state_fail_closed(tmp_path: Path) -> None:
    _, session = _session(tmp_path)
    payload = _pdf_bytes(tmp_path)
    service = DeterministicIntakeApplicationService(clock=lambda: NOW)
    first = service.start_upload(session, io.BytesIO(payload), _upload_request(payload))

    changed = _upload_request(payload, document_route="primary")
    with pytest.raises(ResearchKBError) as conflict:
        service.start_upload(session, io.BytesIO(payload), changed)
    assert conflict.value.diagnostic.code == "RKBC-017"

    stale_expected = {
        "state_id": first["pipeline"]["state_id"],
        "state_digest": "0" * 64,
    }
    with pytest.raises(ResearchKBError) as stale:
        service.resume(
            session,
            first["pipeline"]["job_id"],
            stale_expected,
            _resume_request(document_route="primary"),
        )
    assert stale.value.diagnostic.code == "RKBC-017"


def test_changed_source_reenters_source_check_on_repeated_resume(tmp_path: Path) -> None:
    layout, session = _session(tmp_path)
    payload = _pdf_bytes(tmp_path)
    service = DeterministicIntakeApplicationService(clock=lambda: NOW)
    waiting = service.start_upload(session, io.BytesIO(payload), _upload_request(payload))
    source_root = next(
        item
        for item in read_jsonl(layout.source_assets_path, record_kind="source-asset-state")
        if item["revision"] == 1
    )
    _, source_path = layout.resolve_source(
        source_root["source_ref"]["root_id"],
        source_root["source_ref"]["relative_path"],
    )
    write_synthetic_pdf(source_path, ["Changed synthetic source bytes."])
    request = _resume_request(document_route="primary")

    first_wait = service.resume(
        session,
        waiting["pipeline"]["job_id"],
        {
            "state_id": waiting["pipeline"]["state_id"],
            "state_digest": waiting["pipeline"]["state_digest"],
        },
        request,
    )
    second_wait = service.resume(
        session,
        first_wait["pipeline"]["job_id"],
        {
            "state_id": first_wait["pipeline"]["state_id"],
            "state_digest": first_wait["pipeline"]["state_digest"],
        },
        request,
    )

    assert first_wait["pipeline"]["status"] == "waiting_source"
    assert first_wait["pipeline"]["wait_reason"] == "source_changed"
    assert second_wait["pipeline"]["status"] == "waiting_source"
    assert second_wait["pipeline"]["wait_reason"] == "source_changed"


def test_resume_and_cancel_use_projected_cas_without_exposing_internal_records(tmp_path: Path) -> None:
    layout, session = _session(tmp_path)
    payload = _pdf_bytes(tmp_path)
    service = DeterministicIntakeApplicationService(clock=lambda: NOW)
    waiting = service.start_upload(session, io.BytesIO(payload), _upload_request(payload))
    shown = service.show_job(session, waiting["pipeline"]["job_id"])
    expected = {
        "state_id": shown["pipeline"]["state_id"],
        "state_digest": shown["pipeline"]["state_digest"],
    }
    completed = service.resume(
        session,
        shown["pipeline"]["job_id"],
        expected,
        _resume_request(document_route="primary"),
    )

    assert completed["pipeline"]["status"] == "completed"
    listed = service.list_jobs(session, page_size=10, cursor=None)
    serialized = json.dumps({"show": shown, "list": listed, "completed": completed})
    assert str(layout.knowledge_root) not in serialized
    for forbidden in (
        "source_ref",
        "source_fingerprint",
        "idempotency_key",
        "authority_snapshot",
        "input_refs",
        "output_refs",
        "raw_text",
    ):
        assert forbidden not in serialized

    other_root = tmp_path / "cancel"
    other_root.mkdir()
    other_layout, other_session = _session(other_root)
    other_payload = _pdf_bytes(other_root)
    other = DeterministicIntakeApplicationService(clock=lambda: NOW)
    waiting_cancel = other.start_upload(
        other_session,
        io.BytesIO(other_payload),
        _upload_request(other_payload, idempotency_key="cancel-1"),
    )
    cancelled = other.cancel(
        other_session,
        waiting_cancel["pipeline"]["job_id"],
        {
            "state_id": waiting_cancel["pipeline"]["state_id"],
            "state_digest": waiting_cancel["pipeline"]["state_digest"],
        },
    )
    assert cancelled["pipeline"]["status"] == "cancelled"
    assert not other_layout.paper_card_path(cancelled["paper_id"]).exists()


def test_limits_are_authoritative_and_request_shape_is_strict(tmp_path: Path) -> None:
    _, session = _session(tmp_path)
    service = DeterministicIntakeApplicationService(clock=lambda: NOW)
    limits = service.limits(session)

    assert limits == {
        "status": "success",
        "interface_version": "1.19",
        "max_pdf_bytes": 64 * 1024 * 1024,
        "max_scan_entries": 1000,
        "max_job_page_size": 100,
        "default_min_stable_age_seconds": 5,
        "ingress_modes": ["upload", "watched_inbox"],
        "requested_operations": ["basic_paper_card", "basic_review_memory"],
    }
    payload = _pdf_bytes(tmp_path)
    invalid = _upload_request(payload)
    invalid["filesystem_path"] = "browser-path-is-not-accepted"
    with pytest.raises(ResearchKBError) as rejected:
        service.start_upload(session, io.BytesIO(payload), invalid)
    assert rejected.value.diagnostic.code == "RKBC-002"


def test_pipeline_progress_history_is_running_only_between_deterministic_substeps(
    tmp_path: Path,
) -> None:
    layout, session = _session(tmp_path)
    payload = _pdf_bytes(tmp_path)
    result = DeterministicIntakeApplicationService(clock=lambda: NOW).start_upload(
        session,
        io.BytesIO(payload),
        _upload_request(payload),
    )
    history = PipelineJobService(layout).show(result["pipeline"]["job_id"])["history"]

    running = [item for item in history if item["status"] == "running"]
    assert [item["current_node"] for item in running[:3]] == [
        "registry",
        "source_association",
        "deterministic_trunk",
    ]
    assert all(item["wait_reason"] is None for item in running)
