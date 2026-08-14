from __future__ import annotations

from contextlib import nullcontext
from datetime import timedelta
from pathlib import Path

import pytest

from research_kb.services.workspace_materialization import WorkspaceMaterializationApplicationService
from tests.unit.test_workspace_materialization_service import (
    FakeRootSecurityController,
    _request,
    _service,
)


def test_interrupted_owned_staging_has_closed_recovery_action(tmp_path: Path) -> None:
    request = _request(tmp_path)
    controller = FakeRootSecurityController()

    def crash(phase: str) -> None:
        if phase == "generated_files_written":
            raise RuntimeError("synthetic crash")

    service = _service(phase_hook=crash)
    proposal = service.prepare(request, controller)
    with pytest.raises(RuntimeError, match="synthetic crash"):
        service.commit(
            proposal,
            preview_digest=proposal.preview_digest,
            actor="user",
            root_security_controller=controller,
            writer_mutex=lambda _key: nullcontext(),
        )

    recovery = WorkspaceMaterializationApplicationService().inspect_recovery(
        request.workspace_parent,
        proposal.operation_id,
        controller,
    )
    assert recovery.actions == ("discard_unchanged_owned_staging",)
    assert not proposal.target.exists()

    discarded = WorkspaceMaterializationApplicationService().recover(
        proposal,
        action="discard_unchanged_owned_staging",
        actor="user",
        root_security_controller=controller,
        writer_mutex=lambda _key: nullcontext(),
    )
    assert discarded.state == "discarded"
    assert not (request.workspace_parent / f".{request.workspace_name}.{proposal.operation_id}.stage").exists()


def test_changed_staging_requires_manual_resolution(tmp_path: Path) -> None:
    request = _request(tmp_path)
    controller = FakeRootSecurityController()

    def crash(phase: str) -> None:
        if phase == "journal_written":
            raise RuntimeError("synthetic crash")

    service = _service(phase_hook=crash)
    proposal = service.prepare(request, controller)
    with pytest.raises(RuntimeError):
        service.commit(
            proposal,
            preview_digest=proposal.preview_digest,
            actor="user",
            root_security_controller=controller,
            writer_mutex=lambda _key: nullcontext(),
        )
    staging = request.workspace_parent / f".{request.workspace_name}.{proposal.operation_id}.stage"
    (staging / "foreign.txt").write_text("changed", encoding="utf-8")

    recovery = WorkspaceMaterializationApplicationService().inspect_recovery(
        request.workspace_parent,
        proposal.operation_id,
        controller,
    )
    assert recovery.actions == ("manual_resolution_required",)


def test_published_generation_without_receipt_is_resumable(tmp_path: Path) -> None:
    request = _request(tmp_path)
    controller = FakeRootSecurityController()

    def crash(phase: str) -> None:
        if phase == "published":
            raise RuntimeError("synthetic crash after rename")

    service = _service(phase_hook=crash)
    proposal = service.prepare(request, controller)
    with pytest.raises(RuntimeError, match="after rename"):
        service.commit(
            proposal,
            preview_digest=proposal.preview_digest,
            actor="user",
            root_security_controller=controller,
            writer_mutex=lambda _key: nullcontext(),
        )

    recovery = WorkspaceMaterializationApplicationService().inspect_recovery(
        request.workspace_parent,
        proposal.operation_id,
        controller,
    )
    assert recovery.actions == ("resume_matching_published_generation",)

    resumed = WorkspaceMaterializationApplicationService(
        clock=lambda: request.expires_at + timedelta(seconds=1)
    ).recover(
        proposal,
        action="resume_matching_published_generation",
        actor="user",
        root_security_controller=controller,
        writer_mutex=lambda _key: nullcontext(),
    )
    assert resumed.result == "recovered"
    assert (proposal.target / ".research-kb-materialization" / "receipt.json").is_file()


def test_interruption_after_staged_validation_keeps_generation_unpublished(tmp_path: Path) -> None:
    request = _request(tmp_path)
    controller = FakeRootSecurityController()

    def crash(phase: str) -> None:
        if phase == "staged_generation_validated":
            raise RuntimeError("synthetic crash after validation")

    service = _service(phase_hook=crash)
    proposal = service.prepare(request, controller)
    with pytest.raises(RuntimeError, match="after validation"):
        service.commit(
            proposal,
            preview_digest=proposal.preview_digest,
            actor="user",
            root_security_controller=controller,
            writer_mutex=lambda _key: nullcontext(),
        )

    recovery = WorkspaceMaterializationApplicationService().inspect_recovery(
        request.workspace_parent,
        proposal.operation_id,
        controller,
    )
    assert recovery.state == "owned_incomplete"
    assert recovery.actions == ("discard_unchanged_owned_staging",)
    assert not proposal.target.exists()


def test_interruption_after_receipt_is_classified_complete(tmp_path: Path) -> None:
    request = _request(tmp_path)
    controller = FakeRootSecurityController()

    def crash(phase: str) -> None:
        if phase == "receipt_written":
            raise RuntimeError("synthetic crash after receipt")

    service = _service(phase_hook=crash)
    proposal = service.prepare(request, controller)
    with pytest.raises(RuntimeError, match="after receipt"):
        service.commit(
            proposal,
            preview_digest=proposal.preview_digest,
            actor="user",
            root_security_controller=controller,
            writer_mutex=lambda _key: nullcontext(),
        )

    recovery = WorkspaceMaterializationApplicationService().inspect_recovery(
        request.workspace_parent,
        proposal.operation_id,
        controller,
    )
    assert recovery.state == "complete"
    assert recovery.actions == ("no_change",)


def test_receipt_persisted_before_journal_close_is_completed_on_exact_retry(tmp_path: Path) -> None:
    request = _request(tmp_path)
    controller = FakeRootSecurityController()

    def crash(phase: str) -> None:
        if phase == "receipt_persisted":
            raise RuntimeError("synthetic crash before journal close")

    service = _service(phase_hook=crash)
    proposal = service.prepare(request, controller)
    with pytest.raises(RuntimeError, match="before journal close"):
        service.commit(
            proposal,
            preview_digest=proposal.preview_digest,
            actor="user",
            root_security_controller=controller,
            writer_mutex=lambda _key: nullcontext(),
        )

    pending = WorkspaceMaterializationApplicationService().inspect_recovery(
        request.workspace_parent,
        proposal.operation_id,
        controller,
    )
    assert pending.state == "receipt_close_pending"
    assert pending.actions == ("complete_matching_receipt_journal",)

    recovered = _service().commit(
        proposal,
        preview_digest=proposal.preview_digest,
        actor="user",
        root_security_controller=controller,
        writer_mutex=lambda _key: nullcontext(),
    )

    assert recovered.result == "recovered"
    assert WorkspaceMaterializationApplicationService().inspect_recovery(
        request.workspace_parent,
        proposal.operation_id,
        controller,
    ).state == "complete"


def test_completed_journal_with_deleted_receipt_requires_manual_resolution(tmp_path: Path) -> None:
    request = _request(tmp_path)
    controller = FakeRootSecurityController()
    service = _service()
    proposal = service.prepare(request, controller)
    service.commit(
        proposal,
        preview_digest=proposal.preview_digest,
        actor="user",
        root_security_controller=controller,
        writer_mutex=lambda _key: nullcontext(),
    )
    (proposal.target / ".research-kb-materialization" / "receipt.json").unlink()

    recovery = WorkspaceMaterializationApplicationService().inspect_recovery(
        request.workspace_parent,
        proposal.operation_id,
        controller,
    )

    assert recovery.state == "corrupt"
    assert recovery.actions == ("manual_resolution_required",)


def test_completed_generation_drift_is_not_classified_as_complete(tmp_path: Path) -> None:
    request = _request(tmp_path)
    controller = FakeRootSecurityController()
    service = _service()
    proposal = service.prepare(request, controller)
    service.commit(
        proposal,
        preview_digest=proposal.preview_digest,
        actor="user",
        root_security_controller=controller,
        writer_mutex=lambda _key: nullcontext(),
    )
    (proposal.target / "domain-profile.yaml").write_text("changed", encoding="utf-8")

    recovery = WorkspaceMaterializationApplicationService().inspect_recovery(
        request.workspace_parent,
        proposal.operation_id,
        controller,
    )

    assert recovery.state == "changed"
    assert recovery.actions == ("manual_resolution_required",)


def test_corrupt_published_journal_requires_manual_resolution(tmp_path: Path) -> None:
    request = _request(tmp_path)
    controller = FakeRootSecurityController()
    service = _service()
    proposal = service.prepare(request, controller)
    service.commit(
        proposal,
        preview_digest=proposal.preview_digest,
        actor="user",
        root_security_controller=controller,
        writer_mutex=lambda _key: nullcontext(),
    )
    journal = proposal.target / ".research-kb-materialization" / "journal.jsonl"
    journal.write_text("not-json\n", encoding="utf-8")

    recovery = WorkspaceMaterializationApplicationService().inspect_recovery(
        request.workspace_parent,
        proposal.operation_id,
        controller,
    )

    assert recovery.state == "corrupt"
    assert recovery.actions == ("manual_resolution_required",)
