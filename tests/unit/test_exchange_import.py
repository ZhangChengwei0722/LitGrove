from __future__ import annotations

import json
import stat
import zipfile
from pathlib import Path

import pytest

from research_kb.errors import ResearchKBError
from research_kb.cli import main
from research_kb.exchange import ExchangeExportService, _write_archive
from research_kb.exchange_import import ExchangeArchiveReader, ExchangeImportService, SafeReaderProfile
from research_kb.guardian import GuardianService
from research_kb.services.pipeline_job import PipelineJobService
from research_kb.services.registry import RegistryService
from research_kb.services.source_asset import SourceAssetService
from research_kb.services.workspace_session import WorkspaceSessionService
from research_kb.storage.json_io import file_sha256, serialize_json, sha256_bytes
from research_kb.storage.locking import workspace_lock
from tests.pdf_helpers import write_synthetic_pdf
from tests.runtime_helpers import make_runtime_workspace


PAPER_ID = "paper_a1111111-1111-4111-8111-111111111111"
EXPORT_ID = "export_a1111111-1111-4111-8111-111111111111"
IMPORT_ID = "import_a1111111-1111-4111-8111-111111111111"
SECOND_IMPORT_ID = "import_b2222222-2222-4222-8222-222222222222"
SECOND_EXPORT_ID = "export_b2222222-2222-4222-8222-222222222222"
CREATED_AT = "2026-08-04T00:00:00Z"


def _source_workspace(tmp_path: Path, domain: str = "alpha"):
    base = tmp_path / "source"
    base.mkdir()
    layout = make_runtime_workspace(base, domain)
    source = write_synthetic_pdf(layout.source_roots[f"{domain}-sources"] / "paper.pdf", ["Synthetic Exchange source."])
    paper, _ = RegistryService(layout, id_allocator=lambda namespace: PAPER_ID).add(
        root_id=f"{domain}-sources",
        relative_path=source.name,
        metadata={
            "bibliography": {
                "title": "Synthetic Exchange Paper",
                "authors": ["Synthetic Author"],
                "year": 2026,
                "doi": "10.0000/synthetic.exchange",
            },
            "fixture_origin": "synthetic_from_scratch",
        },
    )
    return layout, paper, source


def _export_with_source(
    tmp_path: Path,
    *,
    domain: str = "alpha",
    export_id: str = EXPORT_ID,
) -> tuple[Path, object, Path]:
    layout, _, source = _source_workspace(tmp_path, domain)
    service = ExchangeExportService(layout)
    request = {
        "scope": "paper",
        "selector_id": PAPER_ID,
        "include_sources": True,
        "rights_assertion": "user_asserts_redistribution_authorized",
    }
    preview = service.preview(request)
    archive = tmp_path / "source.rkb-exchange.zip"
    service.build(
        {
            **request,
            "expected_basis_digest": preview["basis_digest"],
            "export_id": export_id,
            "created_at": CREATED_AT,
        },
        target=archive,
        actor="user",
    )
    return archive, layout, source


def _apply_request(preview: dict, import_id: str = IMPORT_ID) -> dict:
    return {
        "import_id": import_id,
        "expected_archive_sha256": preview["archive_sha256"],
        "expected_basis_digest": preview["basis_digest"],
        "created_at": CREATED_AT,
    }


def test_source_inclusive_export_requires_rights_and_rechecks_digest(tmp_path: Path) -> None:
    layout, _, source = _source_workspace(tmp_path)
    service = ExchangeExportService(layout)

    with pytest.raises(ResearchKBError) as error:
        service.preview({"scope": "paper", "selector_id": PAPER_ID, "include_sources": True})
    assert error.value.diagnostic.code == "RKBC-006"

    request = {
        "scope": "paper",
        "selector_id": PAPER_ID,
        "include_sources": True,
        "rights_assertion": "user_asserts_redistribution_authorized",
    }
    preview = service.preview(request)
    assert preview["pdf_count"] == 1
    assert preview["rights_status"] == "asserted_by_user"
    source.write_bytes(source.read_bytes() + b"changed")

    with pytest.raises(ResearchKBError) as error:
        service.build(
            {
                **request,
                "expected_basis_digest": preview["basis_digest"],
                "export_id": EXPORT_ID,
                "created_at": CREATED_AT,
            },
            target=tmp_path / "changed.rkb-exchange.zip",
            actor="user",
        )
    assert error.value.diagnostic.code == "RKBC-026"
    assert not (tmp_path / "changed.rkb-exchange.zip").exists()


def test_source_inclusive_export_closes_registered_supplement_assets(tmp_path: Path) -> None:
    layout, paper, _ = _source_workspace(tmp_path)
    supplement = write_synthetic_pdf(
        layout.source_roots["alpha-sources"] / "supplement.pdf",
        ["Synthetic Exchange supplement."],
    )
    job = PipelineJobService(layout).create(
        requested_route="local_source",
        requested_depth="registry_only",
        current_node="source_intake",
        input_refs=[],
        authority_snapshot={
            "actor": "user",
            "granted_operations": ["register_by_reference"],
            "captured_at": CREATED_AT,
        },
        idempotency_key="exchange-supplement-source",
        actor="user",
        fixture_origin="synthetic_from_scratch",
    ).state
    SourceAssetService(layout).register_reference(
        job_id=job["job_id"],
        paper_id=paper["paper_id"],
        asset_role="supplement",
        root_id="alpha-sources",
        relative_path=supplement.name,
        actor="cli",
        fixture_origin="synthetic_from_scratch",
    )
    request = {
        "scope": "paper",
        "selector_id": PAPER_ID,
        "include_sources": True,
        "rights_assertion": "user_asserts_redistribution_authorized",
    }
    service = ExchangeExportService(layout)
    preview = service.preview(request)
    assert preview["source_count"] == 2
    assert preview["pdf_count"] == 2
    archive = tmp_path / "assets.rkb-exchange.zip"
    service.build(
        {
            **request,
            "expected_basis_digest": preview["basis_digest"],
            "export_id": EXPORT_ID,
            "created_at": CREATED_AT,
        },
        target=archive,
        actor="user",
    )
    with zipfile.ZipFile(archive) as bundle:
        sources = [json.loads(line) for line in bundle.read("sources/index.jsonl").splitlines()]
    assert {item["asset_role"] for item in sources} == {"main_pdf", "supplement"}
    assert next(item for item in sources if item["asset_role"] == "supplement")["source_asset_id"] is not None
    assert WorkspaceSessionService({"source": layout.config.path}).open("source").workspace_id == layout.workspace_id


def test_source_inclusive_import_is_immutable_external_origin_and_registry_neutral(tmp_path: Path) -> None:
    archive, _, source = _export_with_source(tmp_path)
    target_root = tmp_path / "target"
    target_root.mkdir()
    target = make_runtime_workspace(target_root, "beta")
    registry_before = file_sha256(target.registry_path)
    service = ExchangeImportService(target, import_id_factory=lambda: IMPORT_ID)

    preview = service.preflight(archive)
    result = service.apply(archive, _apply_request(preview), actor="user")

    assert preview["compatibility"] == "supported"
    assert preview["source_count"] == 1
    assert preview["trust_projection"] == "unsigned_external_claims"
    assert result["result"] == "imported"
    assert file_sha256(target.registry_path) == registry_before
    package = target.exchange_import_path(IMPORT_ID)
    assert package.is_dir()
    assert (package / "manifest.json").is_file()
    imported_source = next((package / "sources" / "sha256").glob("*.pdf"))
    assert imported_source.read_bytes() == source.read_bytes()
    assert service.list_imports()["imports"][0]["origin_workspace_id"].startswith("workspace_")
    assert WorkspaceSessionService({"target": target.config.path}).open("target").workspace_id == target.workspace_id

    repeat = service.preflight(archive)
    assert repeat["existing_import_id"] == IMPORT_ID
    assert service.apply(archive, _apply_request(repeat), actor="user")["result"] == "no_change"

    detail = service.show_import(IMPORT_ID)
    assert detail["import"]["import_id"] == IMPORT_ID
    assert detail["selection"]["scope"] == "paper"
    assert detail["record_kind_counts"]["exchange-paper-identity"] == 1
    assert detail["records_truncated"] is False
    assert detail["records"]
    assert all(item["local_admissibility"] == "external_unreviewed" for item in detail["records"])
    assert all(item["trust_projection"] == "unsigned_external_claims" for item in detail["records"])


def test_show_import_revalidates_package_before_returning_external_records(tmp_path: Path) -> None:
    archive, _, _ = _export_with_source(tmp_path)
    target_root = tmp_path / "target"
    target_root.mkdir()
    target = make_runtime_workspace(target_root, "beta")
    service = ExchangeImportService(target, import_id_factory=lambda: IMPORT_ID)
    preview = service.preflight(archive)
    service.apply(archive, _apply_request(preview), actor="user")
    record_path = target.exchange_import_path(IMPORT_ID) / "records" / "exchange-paper-identity.jsonl"
    record_path.write_text("{}\n", encoding="utf-8")

    with pytest.raises(ResearchKBError):
        service.show_import(IMPORT_ID)


def test_import_rejects_tamper_and_traversal_without_stage_or_package(tmp_path: Path) -> None:
    archive, _, _ = _export_with_source(tmp_path)
    target_root = tmp_path / "target"
    target_root.mkdir()
    target = make_runtime_workspace(target_root, "beta")
    service = ExchangeImportService(target, import_id_factory=lambda: IMPORT_ID)
    tampered = tmp_path / "tampered.rkb-exchange.zip"
    _rewrite_archive(archive, tampered, replace_path="records/exchange-paper-identity.jsonl")

    with pytest.raises(ResearchKBError):
        service.preflight(tampered)

    traversal = tmp_path / "traversal.rkb-exchange.zip"
    with zipfile.ZipFile(traversal, "w") as output:
        output.writestr("../escape.txt", "unsafe")
    with pytest.raises(ResearchKBError) as error:
        ExchangeArchiveReader().inspect(traversal)
    assert error.value.diagnostic.code == "RKBC-007"
    assert not target.exchange_import_path(IMPORT_ID).exists()
    assert not any(target.exchange_imports_root.glob(".*.stage"))


def test_interrupted_import_recovers_without_partial_package(tmp_path: Path) -> None:
    archive, _, _ = _export_with_source(tmp_path)
    target_root = tmp_path / "target"
    target_root.mkdir()
    target = make_runtime_workspace(target_root, "beta")
    service = ExchangeImportService(
        target,
        import_id_factory=lambda: IMPORT_ID,
        phase_hook=lambda phase: (_ for _ in ()).throw(RuntimeError("synthetic interruption"))
        if phase == "staged"
        else None,
    )
    preview = service.preflight(archive)

    with pytest.raises(RuntimeError, match="synthetic interruption"):
        service.apply(archive, _apply_request(preview), actor="user")
    assert not target.exchange_import_path(IMPORT_ID).exists()

    recovered = ExchangeImportService(target).recover(dry_run=False)
    assert recovered["actions"] == [{"import_id": IMPORT_ID, "action": "discard_unpublished_stage"}]
    assert not any(target.exchange_imports_root.glob(".*.stage"))


def test_import_cli_uses_same_preflight_and_apply_contract(tmp_path: Path, capsys) -> None:
    archive, _, _ = _export_with_source(tmp_path)
    target_root = tmp_path / "target"
    target_root.mkdir()
    target = make_runtime_workspace(target_root, "beta")

    assert main([
        "exchange", "import-preview",
        "--workspace", str(target.config.path),
        "--archive", str(archive),
    ]) == 0
    preview = json.loads(capsys.readouterr().out)
    request = _apply_request(preview)
    request_path = tmp_path / "import.json"
    request_path.write_text(json.dumps(request) + "\n", encoding="utf-8")

    assert main([
        "exchange", "import",
        "--workspace", str(target.config.path),
        "--archive", str(archive),
        "--request", str(request_path),
        "--actor", "user",
    ]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["result"] == "imported"
    assert result["canonical_scientific_write"] is False


@pytest.mark.parametrize(
    ("bundle_format", "expected"),
    [
        ("research-kb-exchange-bundle@1.1", "newer_but_safe_read_only"),
        ("research-kb-exchange-bundle@0.9", "migration_required"),
        ("unknown-bundle@9", "unknown_or_incompatible"),
    ],
)
def test_compatibility_branches_are_read_only_or_fail_closed(
    tmp_path: Path,
    bundle_format: str,
    expected: str,
) -> None:
    archive, _, _ = _export_with_source(tmp_path)
    changed = tmp_path / f"{expected}.rkb-exchange.zip"
    _rewrite_manifest_format(archive, changed, bundle_format)
    target_root = tmp_path / "target"
    target_root.mkdir()
    service = ExchangeImportService(make_runtime_workspace(target_root, "beta"))

    preview = service.preflight(changed)
    assert preview["compatibility"] == expected
    assert preview["persistent_writes"] == 0


def test_safe_reader_rejects_ratio_case_collision_and_symlink(tmp_path: Path) -> None:
    ratio = tmp_path / "ratio.zip"
    with zipfile.ZipFile(ratio, "w", compression=zipfile.ZIP_DEFLATED) as output:
        output.writestr("large.txt", b"0" * 10_000)
    reader = ExchangeArchiveReader(SafeReaderProfile(max_compression_ratio=2))
    with pytest.raises(ResearchKBError) as error:
        reader.inspect(ratio)
    assert error.value.diagnostic.code == "RKBC-030"

    collision = tmp_path / "collision.zip"
    with zipfile.ZipFile(collision, "w") as output:
        output.writestr("A.txt", b"a")
        output.writestr("a.txt", b"b")
    with pytest.raises(ResearchKBError) as error:
        ExchangeArchiveReader().inspect(collision)
    assert error.value.diagnostic.code == "RKBC-007"


@pytest.mark.parametrize(
    ("profile", "entries", "expected_code"),
    [
        (SafeReaderProfile(max_archive_bytes=1), {"a.txt": b"a"}, "RKBC-030"),
        (SafeReaderProfile(max_entries=1), {"a.txt": b"a", "b.txt": b"b"}, "RKBC-030"),
        (SafeReaderProfile(max_entry_uncompressed_bytes=1), {"a.txt": b"ab"}, "RKBC-030"),
        (SafeReaderProfile(max_total_uncompressed_bytes=1), {"a.txt": b"a", "b.txt": b"b"}, "RKBC-030"),
        (SafeReaderProfile(max_staging_bytes=1), {"a.txt": b"a", "b.txt": b"b"}, "RKBC-030"),
        (SafeReaderProfile(max_manifest_bytes=1), {"manifest.json": b"{}"}, "RKBC-030"),
        (SafeReaderProfile(max_bundle_path_bytes=4), {"long-name.txt": b"a"}, "RKBC-007"),
    ],
)
def test_safe_reader_enforces_each_frozen_budget(
    tmp_path: Path,
    profile: SafeReaderProfile,
    entries: dict[str, bytes],
    expected_code: str,
) -> None:
    archive = tmp_path / "budget.zip"
    _write_archive(archive, entries)
    with pytest.raises(ResearchKBError) as error:
        ExchangeArchiveReader(profile).inspect(archive)
    assert error.value.diagnostic.code == expected_code


def test_safe_reader_rejects_encrypted_reserved_and_unsupported_entries(tmp_path: Path) -> None:
    encrypted = tmp_path / "encrypted.zip"
    _write_archive(encrypted, {"a.txt": b"a"})
    _set_encrypted_flag(encrypted)
    with pytest.raises(ResearchKBError):
        ExchangeArchiveReader().inspect(encrypted)

    reserved = tmp_path / "reserved.zip"
    _write_archive(reserved, {"CON.txt": b"a"})
    with pytest.raises(ResearchKBError) as error:
        ExchangeArchiveReader().inspect(reserved)
    assert error.value.diagnostic.code == "RKBC-007"

    unsupported = tmp_path / "unsupported.zip"
    with zipfile.ZipFile(unsupported, "w", compression=zipfile.ZIP_BZIP2) as output:
        output.writestr("a.txt", b"a")
    with pytest.raises(ResearchKBError):
        ExchangeArchiveReader().inspect(unsupported)


def test_supported_bundle_rejects_malformed_source_index_count_and_record_budget(tmp_path: Path) -> None:
    archive, _, _ = _export_with_source(tmp_path)

    malformed = tmp_path / "malformed-source-index.zip"
    _rewrite_canonical_bundle(archive, malformed, {"sources/index.jsonl": b"not-json\n"})
    with pytest.raises(ResearchKBError):
        ExchangeImportService(_target_workspace(tmp_path, "malformed-target")).preflight(malformed)

    count_mismatch = tmp_path / "count-mismatch.zip"
    _rewrite_canonical_bundle(archive, count_mismatch, {}, record_count_delta=1)
    with pytest.raises(ResearchKBError):
        ExchangeImportService(_target_workspace(tmp_path, "count-target")).preflight(count_mismatch)

    with pytest.raises(ResearchKBError) as error:
        ExchangeImportService(
            _target_workspace(tmp_path, "budget-target"),
            reader=ExchangeArchiveReader(SafeReaderProfile(max_structured_records=0)),
        ).preflight(archive)
    assert error.value.diagnostic.code == "RKBC-030"


def test_external_verified_claim_remains_unreviewed_and_noncanonical(tmp_path: Path) -> None:
    archive, _, _ = _export_with_source(tmp_path)
    claimed = tmp_path / "claimed-verified.zip"
    with zipfile.ZipFile(archive) as source:
        record_path = "records/exchange-paper-identity.jsonl"
        envelope = json.loads(source.read(record_path))
    envelope["claimed_review_status"] = "verified"
    _rewrite_canonical_bundle(archive, claimed, {record_path: serialize_json(envelope)})
    target = _target_workspace(tmp_path, "verified-target")
    preview = ExchangeImportService(target).preflight(claimed)
    assert preview["trust_projection"] == "unsigned_external_claims"
    assert all(item["local_admissibility"] == "external_unreviewed" for item in preview["conflicts"])


def test_exchange_mutations_require_workspace_writer_lock(tmp_path: Path) -> None:
    archive, source_layout, _ = _export_with_source(tmp_path)
    preview = ExchangeExportService(source_layout).preview(
        {"scope": "workspace", "include_sources": False}
    )
    with workspace_lock(source_layout.lock_path):
        with pytest.raises(ResearchKBError) as export_error:
            ExchangeExportService(source_layout, lock_timeout=0.01).build(
                {
                    "scope": "workspace",
                    "include_sources": False,
                    "expected_basis_digest": preview["basis_digest"],
                    "export_id": SECOND_EXPORT_ID,
                    "created_at": CREATED_AT,
                },
                target=tmp_path / "locked.rkb-exchange.zip",
                actor="user",
            )
    assert export_error.value.diagnostic.code == "RKBC-016"

    target = _target_workspace(tmp_path, "locked-target")
    service = ExchangeImportService(target, import_id_factory=lambda: IMPORT_ID, lock_timeout=0.01)
    import_preview = service.preflight(archive)
    with workspace_lock(target.lock_path):
        with pytest.raises(ResearchKBError) as import_error:
            service.apply(archive, _apply_request(import_preview), actor="user")
    assert import_error.value.diagnostic.code == "RKBC-016"


def test_guardian_detects_import_package_tamper_and_incomplete_journal(tmp_path: Path) -> None:
    archive, _, _ = _export_with_source(tmp_path)
    target = _target_workspace(tmp_path, "guardian-target")
    service = ExchangeImportService(target, import_id_factory=lambda: IMPORT_ID)
    preview = service.preflight(archive)
    service.apply(archive, _apply_request(preview), actor="user")
    assert GuardianService(target).check().report["status"] == "success"

    record_file = target.exchange_import_path(IMPORT_ID) / "records" / "exchange-paper-identity.jsonl"
    record_file.write_bytes(record_file.read_bytes() + b"{}\n")
    findings = GuardianService(target).check().report["findings"]
    assert any("digest" in item["message"] for item in findings)

    interrupted_target = _target_workspace(tmp_path, "interrupted-target")
    interrupted = ExchangeImportService(
        interrupted_target,
        import_id_factory=lambda: SECOND_IMPORT_ID,
        phase_hook=lambda phase: (_ for _ in ()).throw(RuntimeError("synthetic interruption"))
        if phase == "staged"
        else None,
    )
    interrupted_preview = interrupted.preflight(archive)
    with pytest.raises(RuntimeError, match="synthetic interruption"):
        interrupted.apply(
            archive,
            _apply_request(interrupted_preview, SECOND_IMPORT_ID),
            actor="user",
        )
    interrupted_findings = GuardianService(interrupted_target).check().report["findings"]
    assert any("not complete" in item["message"] for item in interrupted_findings)


def test_origin_namespace_keeps_colliding_record_ids_distinct(tmp_path: Path) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    first, _, _ = _export_with_source(first_root, domain="alpha", export_id=EXPORT_ID)
    second, _, _ = _export_with_source(second_root, domain="beta", export_id=SECOND_EXPORT_ID)
    target_root = tmp_path / "target"
    target_root.mkdir()
    target = make_runtime_workspace(target_root, "alpha")

    first_service = ExchangeImportService(target, import_id_factory=lambda: IMPORT_ID)
    first_preview = first_service.preflight(first)
    first_service.apply(first, _apply_request(first_preview, IMPORT_ID), actor="user")
    second_service = ExchangeImportService(target, import_id_factory=lambda: SECOND_IMPORT_ID)
    second_preview = second_service.preflight(second)
    second_service.apply(second, _apply_request(second_preview, SECOND_IMPORT_ID), actor="user")

    envelopes = []
    for import_id in (IMPORT_ID, SECOND_IMPORT_ID):
        record_file = target.exchange_import_path(import_id) / "records" / "exchange-paper-identity.jsonl"
        envelopes.append(json.loads(record_file.read_text(encoding="utf-8").splitlines()[0]))
    assert envelopes[0]["origin_record_id"] == envelopes[1]["origin_record_id"] == PAPER_ID
    assert envelopes[0]["origin_workspace_id"] != envelopes[1]["origin_workspace_id"]

    symlink = tmp_path / "symlink.zip"
    info = zipfile.ZipInfo("link")
    info.create_system = 3
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(symlink, "w") as output:
        output.writestr(info, "target")
    with pytest.raises(ResearchKBError) as error:
        ExchangeArchiveReader().inspect(symlink)
    assert error.value.diagnostic.code == "RKBC-007"


def _rewrite_archive(source: Path, target: Path, *, replace_path: str) -> None:
    with zipfile.ZipFile(source) as original, zipfile.ZipFile(target, "w", compression=zipfile.ZIP_STORED) as output:
        for item in original.infolist():
            content = original.read(item.filename)
            if item.filename == replace_path:
                content += b"{}\n"
            output.writestr(item, content)


def _target_workspace(tmp_path: Path, name: str):
    root = tmp_path / name
    root.mkdir()
    return make_runtime_workspace(root, "beta")


def _rewrite_manifest_format(source: Path, target: Path, bundle_format: str) -> None:
    with zipfile.ZipFile(source) as original, zipfile.ZipFile(target, "w", compression=zipfile.ZIP_STORED) as output:
        for item in original.infolist():
            content = original.read(item.filename)
            if item.filename == "manifest.json":
                manifest = json.loads(content)
                manifest["bundle_format"] = bundle_format
                content = (json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
            output.writestr(item, content)


def _rewrite_canonical_bundle(
    source: Path,
    target: Path,
    replacements: dict[str, bytes],
    *,
    record_count_delta: int = 0,
) -> None:
    with zipfile.ZipFile(source) as archive:
        entries = {item.filename: archive.read(item.filename) for item in archive.infolist()}
    entries.update(replacements)
    manifest = json.loads(entries["manifest.json"])
    for item in manifest["entries"]:
        if item["path"] in replacements:
            content = entries[item["path"]]
            item["sha256"] = sha256_bytes(content)
            item["bytes"] = len(content)
    manifest["record_count"] += record_count_delta
    entries["manifest.json"] = serialize_json(manifest)
    receipt = json.loads(entries["receipt.json"])
    receipt["manifest_sha256"] = sha256_bytes(entries["manifest.json"])
    entries["receipt.json"] = serialize_json(receipt)
    _write_archive(target, entries)


def _set_encrypted_flag(path: Path) -> None:
    content = bytearray(path.read_bytes())
    local = content.index(b"PK\x03\x04")
    central = content.index(b"PK\x01\x02")
    content[local + 6 : local + 8] = (int.from_bytes(content[local + 6 : local + 8], "little") | 1).to_bytes(2, "little")
    content[central + 8 : central + 10] = (int.from_bytes(content[central + 8 : central + 10], "little") | 1).to_bytes(2, "little")
    path.write_bytes(content)
