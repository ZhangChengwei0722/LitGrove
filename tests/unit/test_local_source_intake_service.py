from __future__ import annotations

from io import BytesIO
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from research_kb.catalog.models import canonical_digest
from research_kb.errors import ResearchKBError
from research_kb.guardian import GuardianService
from research_kb.services import local_source_intake as local_source_intake_module
from research_kb.services.local_source_intake import LocalSourceIntakeService
from research_kb.services.pipeline_job import PipelineJobService
from research_kb.services.registry import RegistryService
from research_kb.services.source_asset import SourceAssetService
from research_kb.storage.json_io import read_jsonl
from tests.runtime_helpers import make_runtime_workspace


PDF = bytes((37, 80, 68, 70, 45)) + b"1.4\nsynthetic local source\n%%EOF\n"
NOW = datetime(2026, 7, 30, 6, 0, tzinfo=timezone.utc)


def _job(layout, *operations: str) -> dict:
    return PipelineJobService(layout).create(
        requested_route="local_source",
        requested_depth="registry_only",
        current_node="source_intake",
        input_refs=[],
        authority_snapshot={
            "actor": "user",
            "granted_operations": list(operations),
            "captured_at": "2026-07-30T06:00:00Z",
        },
        idempotency_key="local-source-" + "-".join(operations),
        actor="user",
        fixture_origin="synthetic_from_scratch",
    ).state


def _paper(layout, name: str = "registered.pdf") -> str:
    source = layout.source_roots["alpha-sources"] / name
    source.write_bytes(PDF)
    return RegistryService(layout).add(
        root_id="alpha-sources",
        relative_path=name,
        metadata={"fixture_origin": "synthetic_from_scratch"},
    )[0]["paper_id"]


def test_copy_into_inbox_is_create_only_receipted_and_idempotent(tmp_path: Path) -> None:
    layout = make_runtime_workspace(
        tmp_path,
        local_inbox="./sources/inbox",
        create_local_inbox=True,
    )
    paper_id = _paper(layout)
    job = _job(layout, "copy_into_local_inbox")
    source = tmp_path / "user-selected.pdf"
    source.write_bytes(PDF)
    service = LocalSourceIntakeService(layout, clock=lambda: NOW)

    first = service.copy(
        source=source,
        job_id=job["job_id"],
        paper_id=paper_id,
        asset_role="supplement",
        actor="user",
        fixture_origin="synthetic_from_scratch",
    )
    second = service.copy(
        source=source,
        job_id=job["job_id"],
        paper_id=paper_id,
        asset_role="supplement",
        actor="user",
        fixture_origin="synthetic_from_scratch",
    )

    final = layout.local_inbox / f"{job['job_id']}.pdf"
    assert final.read_bytes() == source.read_bytes() == PDF
    assert first["result"] == "copied"
    assert first["persistent_writes"] == 2
    assert second["result"] == "no_change"
    assert second["persistent_writes"] == 0
    assert set(first) >= {"source_ref", "source_asset_id", "source_asset_state_id", "event_id"}
    assert "source_fingerprint" not in first
    assert str(tmp_path) not in repr(first)
    states = read_jsonl(layout.source_assets_path, record_kind="source-asset-state")
    assert len(states) == 1
    assert states[0]["reason"] == "copied_into_local_inbox"
    assert states[0]["job_id"] == job["job_id"]
    assert not list(layout.local_inbox.glob(".research-kb-copy-*.part.pdf"))


def test_copy_stream_is_the_path_independent_core_handoff(tmp_path: Path) -> None:
    layout = make_runtime_workspace(
        tmp_path,
        local_inbox="./sources/inbox",
        create_local_inbox=True,
    )
    paper_id = _paper(layout)
    job = _job(layout, "copy_into_local_inbox")

    result = LocalSourceIntakeService(layout, clock=lambda: NOW).copy_stream(
        stream=BytesIO(PDF),
        job_id=job["job_id"],
        paper_id=paper_id,
        asset_role="supplement",
        actor="user",
        fixture_origin="synthetic_from_scratch",
    )

    assert result["result"] == "copied"
    assert result["persistent_writes"] == 2
    assert (layout.local_inbox / f"{job['job_id']}.pdf").read_bytes() == PDF


def test_copy_rerun_after_registry_association_reuses_original_receipt(tmp_path: Path) -> None:
    layout = make_runtime_workspace(
        tmp_path,
        local_inbox="./sources/inbox",
        create_local_inbox=True,
    )
    job = _job(layout, "copy_into_local_inbox", "associate_source_asset")
    source = tmp_path / "later-associated.pdf"
    source.write_bytes(PDF)
    service = LocalSourceIntakeService(layout, clock=lambda: NOW)
    first = service.copy(
        source=source,
        job_id=job["job_id"],
        paper_id=None,
        asset_role="main_pdf",
        actor="user",
        fixture_origin="synthetic_from_scratch",
    )
    created = read_jsonl(layout.source_assets_path, record_kind="source-asset-state")[-1]
    paper, _ = RegistryService(layout).add(
        root_id=first["source_ref"]["root_id"],
        relative_path=first["source_ref"]["relative_path"],
        metadata={"fixture_origin": "synthetic_from_scratch"},
    )
    associated = SourceAssetService(layout).associate(
        source_asset_id=created["source_asset_id"],
        job_id=job["job_id"],
        paper_id=paper["paper_id"],
        expected_state_id=created["source_asset_state_id"],
        expected_state_digest=canonical_digest(created),
        actor="cli",
    )

    replayed = service.copy(
        source=source,
        job_id=job["job_id"],
        paper_id=None,
        asset_role="main_pdf",
        actor="user",
        fixture_origin="synthetic_from_scratch",
    )

    assert replayed["result"] == "no_change"
    assert replayed["persistent_writes"] == 0
    assert replayed["source_asset_state_id"] == associated.state["source_asset_state_id"]
    assert len(read_jsonl(layout.source_assets_path, record_kind="source-asset-state")) == 2


def test_copy_rerun_does_not_duplicate_asset_when_receipted_target_is_missing(
    tmp_path: Path,
) -> None:
    layout = make_runtime_workspace(
        tmp_path,
        local_inbox="./sources/inbox",
        create_local_inbox=True,
    )
    job = _job(layout, "copy_into_local_inbox")
    source = tmp_path / "missing-after-copy.pdf"
    source.write_bytes(PDF)
    service = LocalSourceIntakeService(layout, clock=lambda: NOW)
    first = service.copy(
        source=source,
        job_id=job["job_id"],
        paper_id=None,
        asset_role="main_pdf",
        actor="user",
        fixture_origin="synthetic_from_scratch",
    )
    copied = layout.resolve_source(
        first["source_ref"]["root_id"],
        first["source_ref"]["relative_path"],
    )[1]
    copied.unlink()

    resumed = service.copy(
        source=source,
        job_id=job["job_id"],
        paper_id=None,
        asset_role="main_pdf",
        actor="user",
        fixture_origin="synthetic_from_scratch",
    )

    assert resumed["result"] == "recovered"
    assert copied.read_bytes() == PDF
    assert len(read_jsonl(layout.source_assets_path, record_kind="source-asset-state")) == 1


def test_copy_collision_preserves_preexisting_final(tmp_path: Path) -> None:
    layout = make_runtime_workspace(
        tmp_path,
        local_inbox="./sources/inbox",
        create_local_inbox=True,
    )
    job = _job(layout, "copy_into_local_inbox")
    source = tmp_path / "selected.pdf"
    source.write_bytes(PDF)
    final = layout.local_inbox / f"{job['job_id']}.pdf"
    final.write_bytes(b"pre-existing")

    with pytest.raises(ResearchKBError) as error:
        LocalSourceIntakeService(layout, clock=lambda: NOW).copy(
            source=source,
            job_id=job["job_id"],
            paper_id=None,
            asset_role="main_pdf",
            actor="user",
        )

    assert error.value.diagnostic.code == "RKBC-017"
    assert final.read_bytes() == b"pre-existing"
    assert not layout.source_assets_path.exists()


def test_copy_requires_user_authority_and_rejects_invalid_pdf_inputs(
    tmp_path: Path,
    monkeypatch,
) -> None:
    layout = make_runtime_workspace(
        tmp_path,
        local_inbox="./sources/inbox",
        create_local_inbox=True,
    )
    job = _job(layout, "copy_into_local_inbox")
    service = LocalSourceIntakeService(layout, clock=lambda: NOW)
    source = tmp_path / "selected.pdf"
    source.write_bytes(PDF)

    with pytest.raises(ResearchKBError) as authority:
        service.copy(
            source=source,
            job_id=job["job_id"],
            paper_id=None,
            asset_role="main_pdf",
            actor="cli",
        )
    assert authority.value.diagnostic.code == "RKBC-006"

    source.write_bytes(b"not a PDF")
    with pytest.raises(ResearchKBError) as signature:
        service.copy(
            source=source,
            job_id=job["job_id"],
            paper_id=None,
            asset_role="main_pdf",
            actor="user",
        )
    assert signature.value.diagnostic.code == "RKBC-002"

    source.write_bytes(PDF + b"x" * 64)
    monkeypatch.setattr(local_source_intake_module, "MAX_PDF_BYTES", len(PDF))
    with pytest.raises(ResearchKBError) as oversized:
        service.copy(
            source=source,
            job_id=job["job_id"],
            paper_id=None,
            asset_role="main_pdf",
            actor="user",
        )
    assert oversized.value.diagnostic.code == "RKBC-002"
    assert not list(layout.local_inbox.iterdir())


def test_copy_rejects_hard_link_and_source_mutation_without_leaving_artifacts(tmp_path: Path) -> None:
    layout = make_runtime_workspace(
        tmp_path,
        local_inbox="./sources/inbox",
        create_local_inbox=True,
    )
    job = _job(layout, "copy_into_local_inbox")
    source = tmp_path / "selected.pdf"
    source.write_bytes(PDF)
    hard_link = tmp_path / "selected-hard-link.pdf"
    os.link(source, hard_link)

    with pytest.raises(ResearchKBError) as ambiguous:
        LocalSourceIntakeService(layout, clock=lambda: NOW).copy(
            source=source,
            job_id=job["job_id"],
            paper_id=None,
            asset_role="main_pdf",
            actor="user",
        )
    assert ambiguous.value.diagnostic.code == "RKBC-007"
    hard_link.unlink()

    def mutate(current: str) -> None:
        if current == "staged":
            source.write_bytes(PDF + b"changed")

    with pytest.raises(ResearchKBError) as changed:
        LocalSourceIntakeService(layout, clock=lambda: NOW, operation_hook=mutate).copy(
            source=source,
            job_id=job["job_id"],
            paper_id=None,
            asset_role="main_pdf",
            actor="user",
        )
    assert changed.value.diagnostic.code == "RKBC-009"
    assert not list(layout.local_inbox.iterdir())
    assert not layout.source_assets_path.exists()


def test_copy_does_not_delete_replaced_operation_path(tmp_path: Path) -> None:
    layout = make_runtime_workspace(
        tmp_path,
        local_inbox="./sources/inbox",
        create_local_inbox=True,
    )
    job = _job(layout, "copy_into_local_inbox")
    source = tmp_path / "selected.pdf"
    source.write_bytes(PDF)
    partial = layout.local_inbox / f".research-kb-copy-{job['job_id']}.part.pdf"
    replacement = b"replacement not owned by the copy operation"

    def replace(current: str) -> None:
        if current == "staged":
            partial.unlink()
            partial.write_bytes(replacement)

    with pytest.raises(ResearchKBError):
        LocalSourceIntakeService(layout, clock=lambda: NOW, operation_hook=replace).copy(
            source=source,
            job_id=job["job_id"],
            paper_id=None,
            asset_role="main_pdf",
            actor="user",
        )

    final = layout.local_inbox / f"{job['job_id']}.pdf"
    remaining = [path for path in (partial, final) if path.exists()]
    assert remaining
    assert all(path.read_bytes() == replacement for path in remaining)
    assert not layout.source_assets_path.exists()
    findings = GuardianService(layout).check().report["findings"]
    assert any("copy partial remains" in item["message"] for item in findings)


def test_copy_rechecks_hard_link_count_after_staging(tmp_path: Path) -> None:
    layout = make_runtime_workspace(
        tmp_path,
        local_inbox="./sources/inbox",
        create_local_inbox=True,
    )
    job = _job(layout, "copy_into_local_inbox")
    source = tmp_path / "late-hard-link.pdf"
    source.write_bytes(PDF)
    hard_link = tmp_path / "late-hard-link-alias.pdf"

    def link_after_staging(current: str) -> None:
        if current == "staged":
            hard_link.hardlink_to(source)

    with pytest.raises(ResearchKBError) as ambiguous:
        LocalSourceIntakeService(
            layout,
            clock=lambda: NOW,
            operation_hook=link_after_staging,
        ).copy(
            source=source,
            job_id=job["job_id"],
            paper_id=None,
            asset_role="main_pdf",
            actor="user",
        )

    assert ambiguous.value.diagnostic.code == "RKBC-009"
    assert not layout.source_assets_path.exists()
    assert not list(layout.local_inbox.iterdir())


def test_copy_detects_published_file_change_before_reporting_success(
    tmp_path: Path,
    monkeypatch,
) -> None:
    layout = make_runtime_workspace(
        tmp_path,
        local_inbox="./sources/inbox",
        create_local_inbox=True,
    )
    job = _job(layout, "copy_into_local_inbox")
    source = tmp_path / "publish-race.pdf"
    source.write_bytes(PDF)
    final = layout.local_inbox / f"{job['job_id']}.pdf"
    original_unlink = local_source_intake_module._safe_unlink_owned

    def tamper_after_publish(path, *, source_identity, expected_link_count=1):
        original_unlink(
            path,
            source_identity=source_identity,
            expected_link_count=expected_link_count,
        )
        if expected_link_count == 2 and final.exists():
            final.write_bytes(PDF[:-1] + b"X")

    monkeypatch.setattr(
        local_source_intake_module,
        "_safe_unlink_owned",
        tamper_after_publish,
    )

    with pytest.raises(ResearchKBError) as changed:
        LocalSourceIntakeService(layout, clock=lambda: NOW).copy(
            source=source,
            job_id=job["job_id"],
            paper_id=None,
            asset_role="main_pdf",
            actor="user",
        )

    assert changed.value.diagnostic.code == "RKBC-017"
    assert final.exists()
    assert final.read_bytes() != PDF
    assert len(read_jsonl(layout.source_assets_path, record_kind="source-asset-state")) == 1


def test_windows_unsupported_hardlink_falls_back_to_create_only_rename(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    layout = make_runtime_workspace(
        tmp_path,
        local_inbox="./sources/inbox",
        create_local_inbox=True,
    )
    job = _job(layout, "copy_into_local_inbox")
    source = tmp_path / "exfat-source.pdf"
    source.write_bytes(PDF)

    def unsupported_hardlink(*_args) -> None:
        error = OSError(22, "Incorrect function")
        error.winerror = 1
        raise error

    monkeypatch.setattr(local_source_intake_module, "_IS_WINDOWS", True, raising=False)
    monkeypatch.setattr(local_source_intake_module.os, "link", unsupported_hardlink)

    result = LocalSourceIntakeService(layout, clock=lambda: NOW).copy(
        source=source,
        job_id=job["job_id"],
        paper_id=None,
        asset_role="main_pdf",
        actor="user",
    )

    final = layout.local_inbox / f"{job['job_id']}.pdf"
    assert result["result"] == "copied"
    assert final.read_bytes() == PDF
    assert not list(layout.local_inbox.glob(".research-kb-copy-*.part.pdf"))


def test_windows_rename_fallback_fails_closed_on_destination_race(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    layout = make_runtime_workspace(
        tmp_path,
        local_inbox="./sources/inbox",
        create_local_inbox=True,
    )
    job = _job(layout, "copy_into_local_inbox")
    source = tmp_path / "rename-race.pdf"
    source.write_bytes(PDF)
    replacement = b"destination race"

    def unsupported_hardlink(*_args) -> None:
        error = OSError(22, "Incorrect function")
        error.winerror = 50
        raise error

    def destination_race(_temporary, final) -> None:
        Path(final).write_bytes(replacement)
        raise FileExistsError("destination appeared")

    monkeypatch.setattr(local_source_intake_module, "_IS_WINDOWS", True, raising=False)
    monkeypatch.setattr(local_source_intake_module.os, "link", unsupported_hardlink)
    monkeypatch.setattr(local_source_intake_module.os, "rename", destination_race)

    with pytest.raises(ResearchKBError) as conflict:
        LocalSourceIntakeService(layout, clock=lambda: NOW).copy(
            source=source,
            job_id=job["job_id"],
            paper_id=None,
            asset_role="main_pdf",
            actor="user",
        )

    final = layout.local_inbox / f"{job['job_id']}.pdf"
    assert conflict.value.diagnostic.code == "RKBC-017"
    assert final.read_bytes() == replacement
    assert list(layout.local_inbox.glob(".research-kb-copy-*.part.pdf"))


def test_windows_rename_fallback_failure_keeps_recoverable_partial(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    layout = make_runtime_workspace(
        tmp_path,
        local_inbox="./sources/inbox",
        create_local_inbox=True,
    )
    job = _job(layout, "copy_into_local_inbox")
    source = tmp_path / "rename-failure.pdf"
    source.write_bytes(PDF)

    def unsupported_hardlink(*_args) -> None:
        error = OSError(22, "Incorrect function")
        error.winerror = 1
        raise error

    def failed_rename(*_args) -> None:
        raise OSError(5, "Synthetic rename failure")

    monkeypatch.setattr(local_source_intake_module, "_IS_WINDOWS", True, raising=False)
    monkeypatch.setattr(local_source_intake_module.os, "link", unsupported_hardlink)
    monkeypatch.setattr(local_source_intake_module.os, "rename", failed_rename)

    with pytest.raises(ResearchKBError) as conflict:
        LocalSourceIntakeService(layout, clock=lambda: NOW).copy(
            source=source,
            job_id=job["job_id"],
            paper_id=None,
            asset_role="main_pdf",
            actor="user",
        )

    final = layout.local_inbox / f"{job['job_id']}.pdf"
    assert conflict.value.diagnostic.code == "RKBC-017"
    assert not final.exists()
    partials = list(layout.local_inbox.glob(".research-kb-copy-*.part.pdf"))
    assert len(partials) == 1
    assert partials[0].read_bytes() == PDF


def test_unexpected_hardlink_error_never_uses_rename_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    layout = make_runtime_workspace(
        tmp_path,
        local_inbox="./sources/inbox",
        create_local_inbox=True,
    )
    job = _job(layout, "copy_into_local_inbox")
    source = tmp_path / "unexpected-link-error.pdf"
    source.write_bytes(PDF)
    rename_called = False

    def denied_hardlink(*_args) -> None:
        error = OSError(13, "Permission denied")
        error.winerror = 5
        raise error

    def forbidden_rename(*_args) -> None:
        nonlocal rename_called
        rename_called = True

    monkeypatch.setattr(local_source_intake_module, "_IS_WINDOWS", True, raising=False)
    monkeypatch.setattr(local_source_intake_module.os, "link", denied_hardlink)
    monkeypatch.setattr(local_source_intake_module.os, "rename", forbidden_rename)

    with pytest.raises(ResearchKBError) as conflict:
        LocalSourceIntakeService(layout, clock=lambda: NOW).copy(
            source=source,
            job_id=job["job_id"],
            paper_id=None,
            asset_role="main_pdf",
            actor="user",
        )

    assert conflict.value.diagnostic.code == "RKBC-017"
    assert rename_called is False


def test_non_windows_never_uses_rename_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    layout = make_runtime_workspace(
        tmp_path,
        local_inbox="./sources/inbox",
        create_local_inbox=True,
    )
    job = _job(layout, "copy_into_local_inbox")
    source = tmp_path / "non-windows-fallback.pdf"
    source.write_bytes(PDF)
    rename_called = False

    def unsupported_hardlink(*_args) -> None:
        error = OSError(22, "Incorrect function")
        error.winerror = 1
        raise error

    def forbidden_rename(*_args) -> None:
        nonlocal rename_called
        rename_called = True

    monkeypatch.setattr(local_source_intake_module, "_IS_WINDOWS", False)
    monkeypatch.setattr(local_source_intake_module.os, "link", unsupported_hardlink)
    monkeypatch.setattr(local_source_intake_module.os, "rename", forbidden_rename)

    with pytest.raises(ResearchKBError) as conflict:
        LocalSourceIntakeService(layout, clock=lambda: NOW).copy(
            source=source,
            job_id=job["job_id"],
            paper_id=None,
            asset_role="main_pdf",
            actor="user",
        )

    assert conflict.value.diagnostic.code == "RKBC-017"
    assert rename_called is False


def test_receipted_partial_recovery_rejects_digest_mismatch(tmp_path: Path) -> None:
    class Crash(BaseException):
        pass

    layout = make_runtime_workspace(
        tmp_path,
        local_inbox="./sources/inbox",
        create_local_inbox=True,
    )
    job = _job(layout, "copy_into_local_inbox")
    source = tmp_path / "receipt-mismatch.pdf"
    source.write_bytes(PDF)

    def crash_after_receipt(current: str) -> None:
        if current == "receipted":
            raise Crash()

    with pytest.raises(Crash):
        LocalSourceIntakeService(
            layout,
            clock=lambda: NOW,
            operation_hook=crash_after_receipt,
        ).copy(
            source=source,
            job_id=job["job_id"],
            paper_id=None,
            asset_role="main_pdf",
            actor="user",
        )

    partial = next(layout.local_inbox.glob(".research-kb-copy-*.part.pdf"))
    partial.write_bytes(PDF + b"changed")
    with pytest.raises(ResearchKBError) as conflict:
        LocalSourceIntakeService(layout, clock=lambda: NOW).recover_copy(
            job_id=job["job_id"],
            actor="user",
        )

    assert conflict.value.diagnostic.code == "RKBC-017"
    assert partial.read_bytes() == PDF + b"changed"
    assert not (layout.local_inbox / f"{job['job_id']}.pdf").exists()


def test_scan_is_bounded_and_selection_revalidates_token(tmp_path: Path) -> None:
    layout = make_runtime_workspace(
        tmp_path,
        local_inbox="./sources/inbox",
        create_local_inbox=True,
    )
    paper_id = _paper(layout)
    stable = layout.local_inbox / "stable.pdf"
    recent = layout.local_inbox / "recent.pdf"
    stable.write_bytes(PDF)
    recent.write_bytes(PDF + b"recent")
    old = (NOW - timedelta(minutes=5)).timestamp()
    os.utime(stable, (old, old))
    recent_time = NOW.timestamp()
    os.utime(recent, (recent_time, recent_time))
    service = LocalSourceIntakeService(layout, clock=lambda: NOW)

    report = service.scan(max_entries=1, min_stable_age_seconds=30)

    assert report["persistent_writes"] == 0
    assert len(report["candidates"]) == 1
    candidate = report["candidates"][0]
    assert candidate["name"] == "stable.pdf"
    assert set(candidate) == {"candidate_token", "name", "source_ref", "size_bytes"}
    assert str(layout.local_inbox) not in repr(candidate)

    job = _job(layout, "select_inbox_candidate", "register_by_reference")
    stable.write_bytes(PDF + b"changed")
    os.utime(stable, (old, old))
    with pytest.raises(ResearchKBError) as changed:
        service.select(
            candidate_handle=candidate["candidate_token"],
            job_id=job["job_id"],
            paper_id=paper_id,
            asset_role="supplement",
            actor="cli",
            min_stable_age_seconds=30,
        )
    assert changed.value.diagnostic.code in {"RKBC-009", "RKBC-017"}
    assert not layout.source_assets_path.exists()


def test_scan_rejects_an_inbox_larger_than_the_inspection_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    layout = make_runtime_workspace(
        tmp_path,
        local_inbox="./sources/inbox",
        create_local_inbox=True,
    )
    monkeypatch.setattr(local_source_intake_module, "MAX_SCAN_ENTRIES", 3)
    for ordinal in range(4):
        (layout.local_inbox / f"entry-{ordinal}.txt").write_text("synthetic", encoding="utf-8")

    with pytest.raises(ResearchKBError) as too_large:
        LocalSourceIntakeService(layout, clock=lambda: NOW).scan(max_entries=3)

    assert too_large.value.diagnostic.code == "RKBC-030"


def test_select_rejects_a_non_integer_stability_window(tmp_path: Path) -> None:
    layout = make_runtime_workspace(
        tmp_path,
        local_inbox="./sources/inbox",
        create_local_inbox=True,
    )
    job = _job(layout, "select_inbox_candidate", "register_by_reference")

    with pytest.raises(ResearchKBError) as invalid:
        LocalSourceIntakeService(layout, clock=lambda: NOW).select(
            candidate_handle="inbox-v1:" + "0" * 64,
            job_id=job["job_id"],
            paper_id=None,
            asset_role="main_pdf",
            actor="cli",
            min_stable_age_seconds="5",  # type: ignore[arg-type]
        )

    assert invalid.value.diagnostic.code == "RKBC-002"


def test_scan_selection_associates_stable_inbox_pdf(tmp_path: Path) -> None:
    layout = make_runtime_workspace(
        tmp_path,
        local_inbox="./sources/inbox",
        create_local_inbox=True,
    )
    paper_id = _paper(layout)
    candidate_path = layout.local_inbox / "manual-download.pdf"
    candidate_path.write_bytes(PDF)
    old = (NOW - timedelta(minutes=5)).timestamp()
    os.utime(candidate_path, (old, old))
    service = LocalSourceIntakeService(layout, clock=lambda: NOW)
    candidate = service.scan(min_stable_age_seconds=30)["candidates"][0]
    job = _job(layout, "select_inbox_candidate", "register_by_reference")

    selected = service.select(
        candidate_handle=candidate["candidate_token"],
        job_id=job["job_id"],
        paper_id=paper_id,
        asset_role="supplement",
        actor="cli",
        min_stable_age_seconds=30,
    )

    assert selected["result"] == "selected"
    assert selected["persistent_writes"] == 1
    assert selected["source_ref"] == candidate["source_ref"]
    assert "source_fingerprint" not in selected
    states = read_jsonl(layout.source_assets_path, record_kind="source-asset-state")
    assert states[0]["paper_id"] == paper_id
    assert states[0]["source_ref"] == candidate["source_ref"]


def test_scan_ignores_hard_links_and_wrong_signatures(tmp_path: Path) -> None:
    layout = make_runtime_workspace(
        tmp_path,
        local_inbox="./sources/inbox",
        create_local_inbox=True,
    )
    linked = layout.local_inbox / "linked.pdf"
    linked.write_bytes(PDF)
    os.link(linked, layout.local_inbox / "linked-copy.pdf")
    wrong = layout.local_inbox / "wrong.pdf"
    wrong.write_bytes(b"not a PDF")
    old = (NOW - timedelta(minutes=5)).timestamp()
    for path in layout.local_inbox.iterdir():
        os.utime(path, (old, old))

    report = LocalSourceIntakeService(layout, clock=lambda: NOW).scan(
        min_stable_age_seconds=30,
    )

    assert report["candidates"] == []


def test_scan_excludes_legacy_registry_and_historical_source_refs(tmp_path: Path) -> None:
    layout = make_runtime_workspace(
        tmp_path,
        local_inbox="./sources/inbox",
        create_local_inbox=True,
    )
    legacy = layout.local_inbox / "legacy.pdf"
    original = layout.local_inbox / "original.pdf"
    alternate = layout.local_inbox / "alternate.pdf"
    for path in (legacy, original, alternate):
        path.write_bytes(PDF)
        os.utime(path, (1.0, 1.0))
    RegistryService(layout).add(
        root_id="alpha-sources",
        relative_path=legacy.relative_to(layout.source_roots["alpha-sources"]).as_posix(),
        metadata={"fixture_origin": "synthetic_from_scratch"},
    )
    job = _job(layout, "register_by_reference", "same_digest_relink")
    source_service = SourceAssetService(layout)
    created = source_service.register_reference(
        job_id=job["job_id"],
        paper_id=None,
        asset_role="supplement",
        root_id="alpha-sources",
        relative_path=original.relative_to(layout.source_roots["alpha-sources"]).as_posix(),
        actor="cli",
        fixture_origin="synthetic_from_scratch",
    )
    source_service.relink(
        source_asset_id=created.state["source_asset_id"],
        job_id=job["job_id"],
        root_id="alpha-sources",
        relative_path=alternate.relative_to(layout.source_roots["alpha-sources"]).as_posix(),
        expected_state_id=created.state["source_asset_state_id"],
        expected_state_digest=canonical_digest(created.state),
        actor="cli",
    )

    report = LocalSourceIntakeService(layout, clock=lambda: NOW).scan(
        min_stable_age_seconds=0,
    )

    assert report["candidates"] == []


def test_select_rejects_candidate_registered_after_scan(tmp_path: Path) -> None:
    layout = make_runtime_workspace(
        tmp_path,
        local_inbox="./sources/inbox",
        create_local_inbox=True,
    )
    candidate = layout.local_inbox / "registry-race.pdf"
    candidate.write_bytes(PDF)
    os.utime(candidate, (1.0, 1.0))
    service = LocalSourceIntakeService(layout, clock=lambda: NOW)
    candidate_handle = service.scan(min_stable_age_seconds=0)["candidates"][0]["candidate_token"]
    RegistryService(layout).add(
        root_id="alpha-sources",
        relative_path=candidate.relative_to(layout.source_roots["alpha-sources"]).as_posix(),
        metadata={"fixture_origin": "synthetic_from_scratch"},
    )
    job = _job(layout, "select_inbox_candidate", "register_by_reference")

    with pytest.raises(ResearchKBError) as raced:
        service.select(
        candidate_handle=candidate_handle,
            job_id=job["job_id"],
            paper_id=None,
            asset_role="main_pdf",
            actor="cli",
            min_stable_age_seconds=0,
        )

    assert raced.value.diagnostic.code == "RKBC-017"
    assert not layout.source_assets_path.exists()


@pytest.mark.parametrize(
    ("phase", "expected_message", "expected_result"),
    [
        ("staged", "copy partial remains", "copied"),
        ("receipted", "manifestation is stale, unavailable", "recovered"),
        ("published", "not yet associated with a Registry paper", "no_change"),
    ],
)
def test_copy_crash_artifacts_are_guardian_visible_and_same_job_resumes(
    tmp_path: Path,
    phase: str,
    expected_message: str,
    expected_result: str,
) -> None:
    class Crash(BaseException):
        pass

    layout = make_runtime_workspace(
        tmp_path,
        local_inbox="./sources/inbox",
        create_local_inbox=True,
    )
    job = _job(layout, "copy_into_local_inbox")
    source = tmp_path / "crash-source.pdf"
    source.write_bytes(PDF)

    def crash(current: str) -> None:
        if current == phase:
            raise Crash()

    service = LocalSourceIntakeService(layout, clock=lambda: NOW, operation_hook=crash)
    with pytest.raises(Crash):
        service.copy(
            source=source,
            job_id=job["job_id"],
            paper_id=None,
            asset_role="main_pdf",
            actor="user",
        )

    findings = GuardianService(layout).check().report["findings"]
    assert any(expected_message in item["message"] for item in findings)

    resumed = LocalSourceIntakeService(layout, clock=lambda: NOW).copy(
        source=source,
        job_id=job["job_id"],
        paper_id=None,
        asset_role="main_pdf",
        actor="user",
    )

    assert resumed["result"] == expected_result
    assert (layout.local_inbox / f"{job['job_id']}.pdf").read_bytes() == PDF
    assert not list(layout.local_inbox.glob(".research-kb-copy-*.part.pdf"))
    assert len(read_jsonl(layout.source_assets_path, record_kind="source-asset-state")) == 1
