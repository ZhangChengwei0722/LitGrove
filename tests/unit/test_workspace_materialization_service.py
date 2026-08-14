from __future__ import annotations

from contextlib import nullcontext
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from research_kb.errors import ResearchKBError
from research_kb.services.workspace_materialization import WorkspaceMaterializationApplicationService
from research_kb.workspace import WorkspaceLayout
from research_kb.workspace_materialization import (
    ExternalSourceRoot,
    RootSecurityAttestation,
    WorkspaceMaterializationRequest,
    path_identity,
)


NOW = datetime(2026, 8, 7, 4, 0, tzinfo=UTC)


class FakeRootSecurityController:
    policy_id = "windows-acl-policy@1.0"

    def __init__(self, *, secure: bool = True) -> None:
        self.secure = secure
        self.created: list[Path] = []
        self.verified: list[Path] = []
        self.secure_overrides: dict[Path, bool] = {}
        self.volume_overrides: dict[Path, str] = {}
        self.reparse_overrides: dict[Path, bool] = {}

    def inspect(self, path: Path) -> RootSecurityAttestation:
        canonical = path.resolve(strict=False)
        return RootSecurityAttestation(
            path_identity=path_identity(path),
            volume_id=self.volume_overrides.get(canonical, "synthetic-volume-c"),
            filesystem="NTFS",
            local=True,
            reparse_free=self.reparse_overrides.get(canonical, True),
            acl_policy_id=self.policy_id,
            acl_secure=self.secure_overrides.get(canonical, self.secure),
        )

    def secure_create(self, path: Path, *, operation_id: str) -> RootSecurityAttestation:
        del operation_id
        path.mkdir()
        self.created.append(path)
        return self.inspect(path)

    def verify(self, path: Path) -> RootSecurityAttestation:
        self.verified.append(path)
        return self.inspect(path)


def _request(tmp_path: Path) -> WorkspaceMaterializationRequest:
    parent = tmp_path / "managed"
    parent.mkdir()
    sources = tmp_path / "external" / "sources"
    inbox = sources / "inbox"
    inbox.mkdir(parents=True)
    return WorkspaceMaterializationRequest(
        workspace_parent=parent,
        workspace_name="synthetic-workspace",
        workspace_label="Synthetic Workspace",
        source_roots=(ExternalSourceRoot("local-sources", sources),),
        local_inbox=inbox,
        idempotency_key="setup-session-0001",
        expires_at=NOW + timedelta(minutes=15),
    )


def _service(*, phase_hook=None) -> WorkspaceMaterializationApplicationService:
    return WorkspaceMaterializationApplicationService(
        clock=lambda: NOW,
        phase_hook=phase_hook,
    )


def test_prepare_is_zero_write_stable_and_redacted(tmp_path: Path) -> None:
    request = _request(tmp_path)
    controller = FakeRootSecurityController()
    before = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))

    first = _service().prepare(request, controller)
    second = _service().prepare(request, controller)

    assert first == second
    assert first.target == request.workspace_parent / request.workspace_name
    assert first.preview_digest != first.proposal_digest
    assert str(request.workspace_parent) not in str(first.preview)
    assert sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*")) == before


def test_prepare_identity_is_bound_to_the_target_even_when_idempotency_key_is_reused(tmp_path: Path) -> None:
    request = _request(tmp_path)
    controller = FakeRootSecurityController()
    first = _service().prepare(request, controller)
    second_parent = tmp_path / "managed-two"
    second_parent.mkdir()

    second = _service().prepare(replace(request, workspace_parent=second_parent), controller)

    assert first.workspace_id != second.workspace_id
    assert first.operation_id != second.operation_id


def test_prepare_rejects_insecure_parent_without_writes(tmp_path: Path) -> None:
    request = _request(tmp_path)
    before = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))

    with pytest.raises(ResearchKBError) as caught:
        _service().prepare(request, FakeRootSecurityController(secure=False))

    assert caught.value.diagnostic.code == "RKBC-039"
    assert sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*")) == before


def test_prepare_rejects_insecure_local_inbox_without_writes(tmp_path: Path) -> None:
    request = _request(tmp_path)
    controller = FakeRootSecurityController()
    controller.secure_overrides[request.local_inbox.resolve()] = False
    before = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))

    with pytest.raises(ResearchKBError) as caught:
        _service().prepare(request, controller)

    assert caught.value.diagnostic.code == "RKBC-039"
    assert sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*")) == before


def test_prepare_rejects_reparse_source_root_without_writes(tmp_path: Path) -> None:
    request = _request(tmp_path)
    controller = FakeRootSecurityController()
    source_root = request.source_roots[0].path.resolve()
    controller.reparse_overrides[source_root] = False
    before = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))

    with pytest.raises(ResearchKBError) as caught:
        _service().prepare(request, controller)

    assert caught.value.diagnostic.code == "RKBC-039"
    assert sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*")) == before


def test_prepare_rejects_windows_reserved_device_name_with_extension(tmp_path: Path) -> None:
    request = _request(tmp_path)
    controller = FakeRootSecurityController()

    with pytest.raises(ResearchKBError) as caught:
        _service().prepare(replace(request, workspace_name="CON.txt"), controller)

    assert caught.value.diagnostic.code == "RKBC-039"
    assert not (request.workspace_parent / "CON.txt").exists()


def test_commit_requires_exact_preview_and_materializes_current_workspace(tmp_path: Path) -> None:
    request = _request(tmp_path)
    controller = FakeRootSecurityController()
    service = _service()
    proposal = service.prepare(request, controller)

    with pytest.raises(ResearchKBError) as caught:
        service.commit(
            proposal,
            preview_digest="0" * 64,
            actor="user",
            root_security_controller=controller,
            writer_mutex=lambda _key: nullcontext(),
        )
    assert caught.value.diagnostic.code == "RKBC-026"
    assert not proposal.target.exists()

    receipt = service.commit(
        proposal,
        preview_digest=proposal.preview_digest,
        actor="user",
        root_security_controller=controller,
        writer_mutex=lambda _key: nullcontext(),
    )

    assert receipt.result == "created"
    assert receipt.workspace_id == proposal.workspace_id
    assert WorkspaceLayout.load(proposal.target / "workspace.yaml").workspace_id == proposal.workspace_id
    assert (proposal.target / ".research-kb-materialization" / "journal.jsonl").is_file()
    assert (proposal.target / ".research-kb-materialization" / "receipt.json").is_file()


def test_commit_rejects_mutated_nested_proposal_state(tmp_path: Path) -> None:
    request = _request(tmp_path)
    controller = FakeRootSecurityController()
    service = _service()
    proposal = service.prepare(request, controller)
    proposal.workspace_config["workspace"]["id"] = "workspace_00000000-0000-4000-8000-000000000000"

    with pytest.raises(ResearchKBError) as caught:
        service.commit(
            proposal,
            preview_digest=proposal.preview_digest,
            actor="user",
            root_security_controller=controller,
            writer_mutex=lambda _key: nullcontext(),
        )

    assert caught.value.diagnostic.code == "RKBC-026"
    assert not proposal.target.exists()


def test_commit_rejects_local_inbox_security_drift(tmp_path: Path) -> None:
    request = _request(tmp_path)
    controller = FakeRootSecurityController()
    service = _service()
    proposal = service.prepare(request, controller)
    controller.volume_overrides[request.local_inbox.resolve()] = "changed-volume"

    with pytest.raises(ResearchKBError) as caught:
        service.commit(
            proposal,
            preview_digest=proposal.preview_digest,
            actor="user",
            root_security_controller=controller,
            writer_mutex=lambda _key: nullcontext(),
        )

    assert caught.value.diagnostic.code == "RKBC-026"
    assert not proposal.target.exists()


def test_commit_rejects_source_root_security_drift(tmp_path: Path) -> None:
    request = _request(tmp_path)
    controller = FakeRootSecurityController()
    service = _service()
    proposal = service.prepare(request, controller)
    source_root = request.source_roots[0].path.resolve()
    controller.volume_overrides[source_root] = "changed-source-volume"

    with pytest.raises(ResearchKBError) as caught:
        service.commit(
            proposal,
            preview_digest=proposal.preview_digest,
            actor="user",
            root_security_controller=controller,
            writer_mutex=lambda _key: nullcontext(),
        )

    assert caught.value.diagnostic.code == "RKBC-026"
    assert not proposal.target.exists()


def test_foreign_target_appearing_after_prepare_fails_closed(tmp_path: Path) -> None:
    request = _request(tmp_path)
    controller = FakeRootSecurityController()
    service = _service()
    proposal = service.prepare(request, controller)
    proposal.target.mkdir()
    (proposal.target / "foreign.txt").write_text("foreign", encoding="utf-8")

    with pytest.raises(ResearchKBError) as caught:
        service.commit(
            proposal,
            preview_digest=proposal.preview_digest,
            actor="user",
            root_security_controller=controller,
            writer_mutex=lambda _key: nullcontext(),
        )

    assert caught.value.diagnostic.code == "RKBC-017"
    assert (proposal.target / "foreign.txt").read_text(encoding="utf-8") == "foreign"


def test_exact_commit_retry_is_no_change(tmp_path: Path) -> None:
    request = _request(tmp_path)
    controller = FakeRootSecurityController()
    service = _service()
    proposal = service.prepare(request, controller)

    created = service.commit(
        proposal,
        preview_digest=proposal.preview_digest,
        actor="user",
        root_security_controller=controller,
        writer_mutex=lambda _key: nullcontext(),
    )
    retried = service.commit(
        proposal,
        preview_digest=proposal.preview_digest,
        actor="user",
        root_security_controller=controller,
        writer_mutex=lambda _key: nullcontext(),
    )

    assert created.receipt_digest == retried.receipt_digest
    assert retried.result == "no_change"
