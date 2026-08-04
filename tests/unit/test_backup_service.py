from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import zipfile

import pytest

import research_kb.backup as backup_module
from research_kb.backup import BackupArchiveReader, BackupService
from research_kb.errors import ResearchKBError
from research_kb.services.registry import RegistryService
from research_kb.storage.json_io import file_sha256
from research_kb.storage.locking import workspace_lock
from research_kb.storage.transactions import TransactionManager
from research_kb.workspace import WorkspaceLayout
from tests.pdf_helpers import write_synthetic_pdf
from tests.runtime_helpers import make_runtime_workspace


BACKUP_ID = "backup_a1111111-1111-4111-8111-111111111111"
RESTORE_ID = "restore_a1111111-1111-4111-8111-111111111111"
CREATED_AT = "2026-08-04T00:00:00Z"
FIXED_TIME = datetime(2026, 8, 4, tzinfo=timezone.utc)


class InjectedCrash(BaseException):
    pass


def _workspace(tmp_path: Path):
    layout = make_runtime_workspace(tmp_path)
    source = write_synthetic_pdf(layout.source_roots["alpha-sources"] / "paper.pdf", ["Synthetic backup source."])
    RegistryService(layout).add(
        root_id="alpha-sources",
        relative_path=source.name,
        metadata={
            "bibliography": {
                "title": "Synthetic Backup Paper",
                "authors": ["Synthetic Author"],
                "year": 2026,
                "doi": "10.0000/synthetic.backup",
            },
            "fixture_origin": "synthetic_from_scratch",
        },
    )
    return layout, source


def _request(preview: dict, *, include_sources: bool = False) -> dict:
    request = {
        "backup_id": BACKUP_ID,
        "include_sources": include_sources,
        "expected_basis_digest": preview["basis_digest"],
        "created_at": CREATED_AT,
    }
    if include_sources:
        request["rights_assertion"] = "user_asserts_backup_authorized"
    return request


def test_source_free_backup_restores_equivalent_workspace_without_copying_pdf(tmp_path: Path) -> None:
    layout, source = _workspace(tmp_path)
    service = BackupService(layout, clock=lambda: FIXED_TIME)
    preview = service.preview(include_sources=False)
    archive = tmp_path / "workspace.rkb-backup.zip"
    result = service.create(_request(preview), target=archive, actor="user")

    inspection = BackupArchiveReader().inspect(archive)
    assert result["archive_sha256"] == inspection["archive_sha256"]
    assert inspection["manifest"]["source_mode"] == "inventory_only"
    assert all(not item.filename.startswith("sources/") for item in zipfile.ZipFile(archive).infolist())
    original_registry = layout.registry_path.read_bytes()
    restored_root = tmp_path / "restored"
    restored = BackupService.restore(
        archive,
        {
            "restore_id": RESTORE_ID,
            "expected_archive_sha256": inspection["archive_sha256"],
            "source_root_mappings": {"alpha-sources": str(layout.source_roots["alpha-sources"])},
            "created_at": CREATED_AT,
        },
        target_root=restored_root,
        actor="user",
    )

    restored_layout = WorkspaceLayout.load(Path(restored["workspace_config_path"]))
    assert restored_layout.registry_path.read_bytes() == original_registry
    assert restored_layout.workspace_id == layout.workspace_id
    assert restored_layout.source_roots["alpha-sources"] == layout.source_roots["alpha-sources"]
    assert source.read_bytes().startswith(b"%PDF")


def test_source_inclusive_backup_requires_authority_and_restores_exact_source(tmp_path: Path) -> None:
    layout, source = _workspace(tmp_path)
    service = BackupService(layout, clock=lambda: FIXED_TIME)
    with pytest.raises(ResearchKBError):
        service.preview(include_sources=True)

    preview = service.preview(
        include_sources=True,
        rights_assertion="user_asserts_backup_authorized",
    )
    archive = tmp_path / "source-inclusive.rkb-backup.zip"
    service.create(_request(preview, include_sources=True), target=archive, actor="user")
    inspection = BackupArchiveReader().inspect(archive)
    restored = BackupService.restore(
        archive,
        {
            "restore_id": RESTORE_ID,
            "expected_archive_sha256": inspection["archive_sha256"],
            "source_root_mappings": {},
            "created_at": CREATED_AT,
        },
        target_root=tmp_path / "restored-inclusive",
        actor="user",
    )
    restored_layout = WorkspaceLayout.load(Path(restored["workspace_config_path"]))
    restored_source = restored_layout.source_roots["alpha-sources"] / source.name
    assert file_sha256(restored_source) == file_sha256(source)


def test_backup_is_lock_bound_create_only_and_interruption_safe(tmp_path: Path) -> None:
    layout, _ = _workspace(tmp_path)
    preview = BackupService(layout).preview(include_sources=False)
    target = tmp_path / "locked.rkb-backup.zip"
    with workspace_lock(layout.lock_path):
        with pytest.raises(ResearchKBError) as caught:
            BackupService(layout, lock_timeout=0.01).create(
                _request(preview), target=target, actor="user"
            )
    assert caught.value.diagnostic.code == "RKBC-016"
    assert not target.exists()

    interrupted = tmp_path / "interrupted.rkb-backup.zip"
    service = BackupService(
        layout,
        phase_hook=lambda phase: (_ for _ in ()).throw(InjectedCrash())
        if phase == "archive_written"
        else None,
    )
    with pytest.raises(InjectedCrash):
        service.create(_request(preview), target=interrupted, actor="user")
    assert not interrupted.exists()
    assert not list(tmp_path.glob(".interrupted.rkb-backup.zip.*.tmp"))


def test_backup_reader_rejects_tamper_and_restore_refuses_existing_target(tmp_path: Path) -> None:
    layout, _ = _workspace(tmp_path)
    preview = BackupService(layout).preview(include_sources=False)
    archive = tmp_path / "workspace.rkb-backup.zip"
    BackupService(layout).create(_request(preview), target=archive, actor="user")
    tampered = tmp_path / "tampered.rkb-backup.zip"
    tampered.write_bytes(archive.read_bytes() + b"tamper")
    with pytest.raises(ResearchKBError):
        BackupArchiveReader().inspect(tampered)

    existing = tmp_path / "existing"
    existing.mkdir()
    inspection = BackupArchiveReader().inspect(archive)
    with pytest.raises(ResearchKBError):
        BackupService.restore(
            archive,
            {
                "restore_id": RESTORE_ID,
                "expected_archive_sha256": inspection["archive_sha256"],
                "source_root_mappings": {"alpha-sources": str(layout.source_roots["alpha-sources"])},
                "created_at": CREATED_AT,
            },
            target_root=existing,
            actor="user",
        )


def test_source_change_after_preview_blocks_source_inclusive_backup(tmp_path: Path) -> None:
    layout, source = _workspace(tmp_path)
    service = BackupService(layout)
    preview = service.preview(
        include_sources=True,
        rights_assertion="user_asserts_backup_authorized",
    )
    source.write_bytes(source.read_bytes() + b"changed")
    target = tmp_path / "changed.rkb-backup.zip"
    with pytest.raises(ResearchKBError):
        service.create(_request(preview, include_sources=True), target=target, actor="user")
    assert not target.exists()


def test_failed_restore_mapping_validation_leaves_no_target(tmp_path: Path) -> None:
    layout, _ = _workspace(tmp_path)
    service = BackupService(layout)
    preview = service.preview(include_sources=False)
    archive = tmp_path / "workspace.rkb-backup.zip"
    service.create(_request(preview), target=archive, actor="user")
    inspection = BackupArchiveReader().inspect(archive)
    target = tmp_path / "unpublished"
    with pytest.raises(ResearchKBError):
        BackupService.restore(
            archive,
            {
                "restore_id": RESTORE_ID,
                "expected_archive_sha256": inspection["archive_sha256"],
                "source_root_mappings": {},
                "created_at": CREATED_AT,
            },
            target_root=target,
            actor="user",
        )
    assert not target.exists()
    assert not list(tmp_path.glob(".unpublished.restore_*.stage"))


def test_reader_rejects_manifest_workspace_and_source_inventory_mismatch(tmp_path: Path) -> None:
    layout, _ = _workspace(tmp_path)
    service = BackupService(layout)
    preview = service.preview(include_sources=False)
    archive = tmp_path / "workspace.rkb-backup.zip"
    service.create(_request(preview), target=archive, actor="user")

    mismatched_workspace = tmp_path / "workspace-mismatch.rkb-backup.zip"
    _rewrite_backup(
        archive,
        mismatched_workspace,
        lambda entries: _replace_manifest_value(
            entries,
            "workspace_id",
            "workspace_b2222222-2222-4222-8222-222222222222",
        ),
    )
    with pytest.raises(ResearchKBError):
        BackupArchiveReader().inspect(mismatched_workspace)

    mismatched_source = tmp_path / "source-mismatch.rkb-backup.zip"
    _rewrite_backup(
        archive,
        mismatched_source,
        lambda entries: _replace_source_inventory_archive_path(entries, "sources/alpha-sources/unlisted.pdf"),
    )
    with pytest.raises(ResearchKBError):
        BackupArchiveReader().inspect(mismatched_source)


def test_backup_preserves_unsettled_journal_but_restore_remains_closed(tmp_path: Path) -> None:
    layout, _ = _workspace(tmp_path)
    with pytest.raises(InjectedCrash):
        TransactionManager(layout).promote_bytes(
            target=layout.review_queue_path,
            content=b"",
            target_store="review_queue",
            operation="synthetic_interrupted_write",
            actor="cli",
            input_refs=[],
            output_refs=[],
            phase_hook=lambda phase: (_ for _ in ()).throw(InjectedCrash())
            if phase == "prepared"
            else None,
        )

    service = BackupService(layout)
    preview = service.preview(include_sources=False)
    assert preview["process_watermark"]["active_journal_count"] >= 1
    archive = tmp_path / "unsettled.rkb-backup.zip"
    service.create(_request(preview), target=archive, actor="user")
    inspection = BackupArchiveReader().inspect(archive)
    target = tmp_path / "blocked-restore"
    with pytest.raises(ResearchKBError):
        BackupService.restore(
            archive,
            {
                "restore_id": RESTORE_ID,
                "expected_archive_sha256": inspection["archive_sha256"],
                "source_root_mappings": {"alpha-sources": str(layout.source_roots["alpha-sources"])},
                "created_at": CREATED_AT,
            },
            target_root=target,
            actor="user",
        )
    assert not target.exists()


def test_restore_validates_one_entry_snapshot_and_reuses_it_for_guardian(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    layout, _ = _workspace(tmp_path)
    service = BackupService(layout)
    preview = service.preview(include_sources=False)
    archive = tmp_path / "workspace.rkb-backup.zip"
    service.create(_request(preview), target=archive, actor="user")
    inspection = BackupArchiveReader().inspect(archive)

    validation_calls = 0
    guardian_calls: list[tuple[bool, int]] = []
    original_validate = backup_module.validate_workspace_entries
    original_guardian_check = backup_module.GuardianService.check

    def counted_validate(*args, **kwargs):
        nonlocal validation_calls
        validation_calls += 1
        return original_validate(*args, **kwargs)

    def captured_guardian_check(self, **kwargs):
        entries = kwargs.get("entries")
        guardian_calls.append((kwargs.get("entries_validated", False), len(entries or [])))
        return original_guardian_check(self, **kwargs)

    monkeypatch.setattr(backup_module, "validate_workspace_entries", counted_validate)
    monkeypatch.setattr(backup_module.GuardianService, "check", captured_guardian_check)

    BackupService.restore(
        archive,
        {
            "restore_id": RESTORE_ID,
            "expected_archive_sha256": inspection["archive_sha256"],
            "source_root_mappings": {
                "alpha-sources": str(layout.source_roots["alpha-sources"])
            },
            "created_at": CREATED_AT,
        },
        target_root=tmp_path / "restored-once",
        actor="user",
    )

    assert validation_calls == 1
    assert len(guardian_calls) == 1
    assert guardian_calls[0][0] is True
    assert guardian_calls[0][1] > 0


def _rewrite_backup(source: Path, target: Path, mutate) -> None:
    with zipfile.ZipFile(source) as archive:
        entries = {item.filename: archive.read(item.filename) for item in archive.infolist()}
    mutate(entries)
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, content in sorted(entries.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 3
            info.external_attr = (0o100600) << 16
            info.flag_bits = 0x800
            archive.writestr(info, content)


def _replace_manifest_value(entries: dict[str, bytes], key: str, value: str) -> None:
    import json

    manifest = json.loads(entries["manifest.json"])
    manifest[key] = value
    from research_kb.storage.json_io import serialize_json

    entries["manifest.json"] = serialize_json(manifest)


def _replace_source_inventory_archive_path(entries: dict[str, bytes], archive_path: str) -> None:
    import json

    manifest = json.loads(entries["manifest.json"])
    manifest["source_inventory"][0]["archive_path"] = archive_path
    from research_kb.storage.json_io import serialize_json

    entries["manifest.json"] = serialize_json(manifest)
