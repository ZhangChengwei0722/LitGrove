from __future__ import annotations

import json
import os
import re
import shutil
import stat
import unicodedata
import zipfile
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from research_kb.bundle import load_workspace_entries, validate_workspace_entries
from research_kb.contracts.validator import validate_record
from research_kb.errors import (
    INPUT_TOO_LARGE,
    INVALID_AUTHORITY,
    PATH_ESCAPE,
    PROTECTED_INPUT_CHANGED,
    SCHEMA_VALIDATION_FAILED,
    UNSUPPORTED_VERSION,
    WRITE_CONFLICT,
    Diagnostic,
    ResearchKBError,
)
from research_kb.exchange import BUNDLE_FORMAT, build_exchange_envelope
from research_kb.identifiers import Namespace, allocate_id, validate_id
from research_kb.process_events import Clock, timestamp, utc_now
from research_kb.storage.json_io import (
    atomic_write_bytes,
    read_json_document,
    serialize_json,
    sha256_bytes,
)
from research_kb.storage.locking import workspace_lock
from research_kb.workspace import WorkspaceLayout


SAFE_READER_PROFILE_ID = "p10-exchange-safe-reader-v1"
TRUST_PROJECTION = "unsigned_external_claims"
_UUID_KIND_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
_RESERVED_WINDOWS_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL", *{f"COM{index}" for index in range(1, 10)}, *{f"LPT{index}" for index in range(1, 10)}}
)
_PAYLOAD_KINDS = frozenset(
    {
        "exchange-paper-identity",
        "paper-card",
        "evidence",
        "review-queue",
        "review-memory",
        "primary-semantic-bundle",
        "review-semantic-bundle",
        "question-mapping",
        "direction-bundle",
        "field-map-bundle",
        "question-revision-bundle",
        "tag-bundle",
        "tag-link-bundle",
        "screening-criteria-bundle",
        "screening-decision-bundle",
        "step7-synthesis",
        "step7-review-angle",
        "step7-insight",
        "step7-cross-view",
    }
)


@dataclass(frozen=True, slots=True)
class SafeReaderProfile:
    profile_id: str = SAFE_READER_PROFILE_ID
    max_archive_bytes: int = 4 * 1024**3
    max_entries: int = 10_000
    max_entry_uncompressed_bytes: int = 512 * 1024**2
    max_total_uncompressed_bytes: int = 8 * 1024**3
    max_compression_ratio: int = 100
    max_staging_bytes: int = 10 * 1024**3
    max_manifest_bytes: int = 32 * 1024**2
    max_structured_records: int = 500_000
    max_bundle_path_bytes: int = 240


@dataclass(frozen=True, slots=True)
class InspectedEntry:
    path: str
    sha256: str
    compressed_bytes: int
    uncompressed_bytes: int
    compress_type: int
    prefix: bytes


@dataclass(frozen=True, slots=True)
class ArchiveInspection:
    archive_sha256: str
    archive_bytes: int
    entries: tuple[InspectedEntry, ...]
    structured_content: dict[str, bytes]
    canonical_serialization: bool

    def entry_map(self) -> dict[str, InspectedEntry]:
        return {item.path: item for item in self.entries}


class ExchangeArchiveReader:
    def __init__(self, profile: SafeReaderProfile | None = None):
        self.profile = profile or SafeReaderProfile()

    def inspect(self, archive_path: Path) -> ArchiveInspection:
        path = _validate_archive_path(Path(archive_path), self.profile)
        archive_sha256 = _hash_file(path)
        structured: dict[str, bytes] = {}
        inspected: list[InspectedEntry] = []
        try:
            with zipfile.ZipFile(path) as archive:
                infos = archive.infolist()
                if not infos or len(infos) > self.profile.max_entries:
                    raise _error(INPUT_TOO_LARGE, "/archive", "Exchange archive entry count is outside the safe-reader budget")
                normalized_names = [_validate_bundle_path(item.filename, self.profile) for item in infos]
                if len(normalized_names) != len(set(normalized_names)) or len({name.casefold() for name in normalized_names}) != len(normalized_names):
                    raise _error(PATH_ESCAPE, "/archive", "Exchange archive contains duplicate or case-colliding paths")
                total = 0
                canonical = normalized_names == sorted(normalized_names)
                for info, name in zip(infos, normalized_names, strict=True):
                    _validate_info(info, self.profile)
                    total += info.file_size
                    if total > min(self.profile.max_total_uncompressed_bytes, self.profile.max_staging_bytes):
                        raise _error(INPUT_TOO_LARGE, f"/{name}", "Exchange expanded size exceeds the safe-reader budget")
                    digest, actual, prefix, content = self._stream_entry(archive, info, name)
                    if name == "manifest.json" and actual > self.profile.max_manifest_bytes:
                        raise _error(INPUT_TOO_LARGE, "/manifest.json", "Exchange manifest exceeds the safe-reader budget")
                    if actual != info.file_size:
                        raise _error(SCHEMA_VALIDATION_FAILED, f"/{name}", "archive entry size disagrees with streamed bytes")
                    if content is not None:
                        structured[name] = content
                    inspected.append(InspectedEntry(name, digest, info.compress_size, actual, info.compress_type, prefix))
                    canonical = canonical and _canonical_info(info)
        except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as error:
            raise _error(SCHEMA_VALIDATION_FAILED, "/archive", "Exchange archive is malformed or unsupported") from error
        return ArchiveInspection(archive_sha256, path.stat().st_size, tuple(inspected), structured, canonical)

    def extract_to(self, archive_path: Path, target: Path) -> ArchiveInspection:
        inspection = self.inspect(archive_path)
        if os.path.lexists(target):
            raise _error(WRITE_CONFLICT, "/stage", "Exchange import stage already exists")
        target.mkdir(mode=0o700, parents=True)
        try:
            entry_map = inspection.entry_map()
            with zipfile.ZipFile(archive_path) as archive:
                for name in sorted(entry_map):
                    destination = target.joinpath(*PurePosixPath(name).parts)
                    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                    if not destination.resolve(strict=False).is_relative_to(target.resolve()):
                        raise _error(PATH_ESCAPE, f"/{name}", "Exchange extraction escaped its stage")
                    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    digest = __import__("hashlib").sha256()
                    written = 0
                    try:
                        with os.fdopen(descriptor, "wb") as output, archive.open(name) as source:
                            while chunk := source.read(1024 * 1024):
                                written += len(chunk)
                                if written > entry_map[name].uncompressed_bytes:
                                    raise _error(INPUT_TOO_LARGE, f"/{name}", "Exchange entry expanded beyond preflight")
                                digest.update(chunk)
                                output.write(chunk)
                            output.flush()
                            os.fsync(output.fileno())
                    except BaseException:
                        destination.unlink(missing_ok=True)
                        raise
                    if written != entry_map[name].uncompressed_bytes or digest.hexdigest() != entry_map[name].sha256:
                        raise _error(PROTECTED_INPUT_CHANGED, f"/{name}", "Exchange archive changed during extraction")
                    if destination.is_symlink() or not destination.is_file():
                        raise _error(PATH_ESCAPE, f"/{name}", "Exchange extraction created an unsafe file")
        except BaseException:
            raise
        return inspection

    def _stream_entry(
        self,
        archive: zipfile.ZipFile,
        info: zipfile.ZipInfo,
        name: str,
    ) -> tuple[str, int, bytes, bytes | None]:
        digest = __import__("hashlib").sha256()
        actual = 0
        prefix = b""
        collect = not name.startswith("sources/sha256/")
        content = bytearray() if collect else None
        with archive.open(info) as source:
            while chunk := source.read(1024 * 1024):
                actual += len(chunk)
                if actual > self.profile.max_entry_uncompressed_bytes:
                    raise _error(INPUT_TOO_LARGE, f"/{name}", "Exchange entry exceeds the safe-reader budget")
                digest.update(chunk)
                if len(prefix) < 8:
                    prefix = (prefix + chunk)[:8]
                if content is not None:
                    content.extend(chunk)
        return digest.hexdigest(), actual, prefix, bytes(content) if content is not None else None


class ExchangeImportService:
    def __init__(
        self,
        layout: WorkspaceLayout,
        *,
        reader: ExchangeArchiveReader | None = None,
        clock: Clock = utc_now,
        import_id_factory: Callable[[], str] | None = None,
        phase_hook: Callable[[str], None] | None = None,
        lock_timeout: float = 30.0,
    ):
        self.layout = layout
        self.reader = reader or ExchangeArchiveReader()
        self.clock = clock
        self.import_id_factory = import_id_factory or (lambda: allocate_id(Namespace.IMPORT))
        self.phase_hook = phase_hook
        self.lock_timeout = lock_timeout

    def limits(self) -> dict[str, Any]:
        return {"safe_reader_profile": asdict(self.reader.profile), "bundle_format": BUNDLE_FORMAT}

    def preflight(self, archive_path: Path) -> dict[str, Any]:
        inspection = self.reader.inspect(archive_path)
        raw_manifest = _load_json(inspection.structured_content.get("manifest.json"), "exchange-bundle-manifest")
        compatibility = _compatibility(raw_manifest.get("bundle_format"))
        if compatibility != "supported":
            return {
                "status": "success",
                "interface_version": "1.0",
                "compatibility": compatibility,
                "archive_sha256": inspection.archive_sha256,
                "archive_bytes": inspection.archive_bytes,
                "canonical_serialization": inspection.canonical_serialization,
                "safe_reader_profile_id": self.reader.profile.profile_id,
                "persistent_writes": 0,
                "canonical_scientific_write": False,
            }
        if not inspection.canonical_serialization:
            raise _error(SCHEMA_VALIDATION_FAILED, "/archive", "supported Exchange import requires canonical serialization")
        manifest, envelopes, sources, manifest_sha256 = _validate_supported_bundle(inspection, self.reader.profile)
        local_entries = load_workspace_entries(self.layout)
        validate_workspace_entries(local_entries)
        conflicts = _project_conflicts(envelopes, local_entries, self.layout.workspace_id)
        existing_import_id = self._find_existing_import(inspection.archive_sha256)
        basis = {
            "archive_sha256": inspection.archive_sha256,
            "local_workspace_id": self.layout.workspace_id,
            "conflicts": conflicts,
            "existing_import_id": existing_import_id,
        }
        return {
            "status": "success",
            "interface_version": "1.0",
            "compatibility": "supported",
            "safe_reader_profile_id": self.reader.profile.profile_id,
            "archive_sha256": inspection.archive_sha256,
            "archive_bytes": inspection.archive_bytes,
            "manifest_sha256": manifest_sha256,
            "basis_digest": sha256_bytes(serialize_json(basis)),
            "import_id": existing_import_id or self.import_id_factory(),
            "existing_import_id": existing_import_id,
            "origin_workspace_id": manifest["origin_workspace_id"],
            "export_id": manifest["export_id"],
            "selection": manifest["selection"],
            "record_count": len(envelopes),
            "record_kind_counts": dict(sorted(Counter(item["record_kind"] for item in envelopes).items())),
            "source_count": len(sources),
            "include_sources": manifest["include_sources"],
            "rights_assertion": manifest["rights_assertion"],
            "trust_projection": TRUST_PROJECTION,
            "conflict_counts": dict(sorted(Counter(item["classification"] for item in conflicts).items())),
            "conflicts": conflicts[:100],
            "conflicts_truncated": len(conflicts) > 100,
            "canonical_serialization": True,
            "persistent_writes": 0,
            "canonical_scientific_write": False,
        }

    def apply(self, archive_path: Path, request: Mapping[str, Any], *, actor: str) -> dict[str, Any]:
        if actor != "user":
            raise _error(INVALID_AUTHORITY, "/actor", "Exchange import requires explicit user authority")
        _validate_import_request(request)
        with workspace_lock(self.layout.lock_path, timeout=self.lock_timeout):
            return self._apply_locked(archive_path, request)

    def _apply_locked(self, archive_path: Path, request: Mapping[str, Any]) -> dict[str, Any]:
        preview = self.preflight(archive_path)
        if preview["compatibility"] != "supported":
            raise _error(UNSUPPORTED_VERSION, "/compatibility", "Exchange bundle is not writable under the current contract")
        if (
            preview["archive_sha256"] != request["expected_archive_sha256"]
            or preview["basis_digest"] != request["expected_basis_digest"]
        ):
            raise _error(PROTECTED_INPUT_CHANGED, "/expected_basis_digest", "Exchange import basis changed after preview")
        if preview["existing_import_id"] is not None:
            return _import_result(preview["existing_import_id"], preview, "no_change", persistent_writes=0)
        import_id = validate_id(request["import_id"], Namespace.IMPORT)
        created_at = _require_timestamp(request["created_at"])
        target = self.layout.ensure_writable_target(self.layout.exchange_import_path(import_id))
        stage = self.layout.ensure_writable_target(self.layout.exchange_imports_root / f".{import_id}.stage")
        journal_path = self.layout.ensure_writable_target(self.layout.exchange_import_journal_path(import_id))
        if os.path.lexists(target) or os.path.lexists(stage) or journal_path.exists():
            raise _error(WRITE_CONFLICT, "/import_id", "Exchange import ID is already in use")
        self.layout.exchange_imports_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        journal = {
            "schema_version": "1.0",
            "import_id": import_id,
            "archive_sha256": preview["archive_sha256"],
            "basis_digest": preview["basis_digest"],
            "stage_name": stage.name,
            "package_relative_path": self.layout.target_relative_path(target),
            "phase": "prepared",
            "result": None,
            "created_at": created_at,
            "updated_at": created_at,
        }
        self._write_journal(journal_path, journal)
        inspection = self.reader.extract_to(archive_path, stage)
        if inspection.archive_sha256 != preview["archive_sha256"]:
            raise _error(PROTECTED_INPUT_CHANGED, "/archive", "Exchange archive changed during import")
        receipt = _build_import_receipt(import_id, self.layout.workspace_id, preview, created_at)
        atomic_write_bytes(stage / "import_receipt.json", serialize_json(receipt), import_id)
        self._set_journal(journal_path, journal, "staged")
        if self.phase_hook is not None:
            self.phase_hook("staged")
        if os.path.lexists(target):
            raise _error(WRITE_CONFLICT, "/import_id", "Exchange import package appeared before publication")
        os.replace(stage, target)
        self._set_journal(journal_path, journal, "published")
        if self.phase_hook is not None:
            self.phase_hook("published")
        validate_import_package(target, preview["archive_sha256"], profile=self.reader.profile)
        self._set_journal(journal_path, journal, "complete", result="success")
        return _import_result(import_id, preview, "imported", persistent_writes=1)

    def list_imports(self) -> dict[str, Any]:
        imports = []
        root = self.layout.exchange_imports_root
        if root.exists():
            for path in sorted(root.iterdir(), key=lambda item: item.name):
                if path.name.startswith(".") or not path.is_dir():
                    continue
                receipt = validate_import_package(path, profile=self.reader.profile)
                imports.append(receipt)
        return {"status": "success", "imports": imports, "persistent_writes": 0, "canonical_scientific_write": False}

    def show_import(self, import_id: str) -> dict[str, Any]:
        resolved_id = validate_id(import_id, Namespace.IMPORT)
        receipt, manifest, envelopes = _read_validated_import_package(
            self.layout.exchange_import_path(resolved_id),
            profile=self.reader.profile,
        )
        record_limit = 100
        records = [
            {
                "origin_workspace_id": envelope["origin_workspace_id"],
                "origin_record_id": envelope["origin_record_id"],
                "record_kind": envelope["record_kind"],
                "revision_digest": envelope["revision_digest"],
                "label": _external_record_label(envelope),
                "local_admissibility": "external_unreviewed",
                "trust_projection": TRUST_PROJECTION,
            }
            for envelope in envelopes[:record_limit]
        ]
        return {
            "status": "success",
            "interface_version": "1.0",
            "import": receipt,
            "selection": manifest["selection"],
            "record_kind_counts": manifest["record_kind_counts"],
            "include_sources": manifest["include_sources"],
            "rights_assertion": manifest["rights_assertion"],
            "records": records,
            "records_truncated": len(envelopes) > record_limit,
            "persistent_writes": 0,
            "canonical_scientific_write": False,
        }

    def recover(self, *, dry_run: bool = True) -> dict[str, Any]:
        with workspace_lock(self.layout.lock_path, timeout=self.lock_timeout):
            return self._recover_locked(dry_run=dry_run)

    def _recover_locked(self, *, dry_run: bool) -> dict[str, Any]:
        actions: list[dict[str, str]] = []
        root = self.layout.exchange_import_transactions_root
        if not root.exists():
            return {"status": "success", "dry_run": dry_run, "actions": actions}
        for journal_path in sorted(root.glob("*.json")):
            journal = read_json_document(journal_path, record_kind="exchange-import-journal")
            diagnostics = validate_record("exchange-import-journal", journal, actor="stored")
            if diagnostics:
                raise ResearchKBError(diagnostics[0])
            if journal["phase"] == "complete":
                continue
            import_id = journal["import_id"]
            stage = self.layout.ensure_writable_target(self.layout.exchange_imports_root / journal["stage_name"])
            target = self.layout.ensure_writable_target(self.layout.knowledge_root / Path(*journal["package_relative_path"].split("/")))
            if target.is_dir():
                try:
                    validate_import_package(
                        target,
                        journal["archive_sha256"],
                        profile=self.reader.profile,
                    )
                except ResearchKBError:
                    action = "manual_resolution_required"
                    if not dry_run:
                        self._set_journal(journal_path, journal, "needs_resolution", result="needs_resolution")
                else:
                    action = "complete_published_import"
                    if not dry_run:
                        self._set_journal(journal_path, journal, "complete", result="success")
            elif stage.is_dir() and journal["phase"] in {"prepared", "staged"}:
                action = "discard_unpublished_stage"
                if not dry_run:
                    shutil.rmtree(stage)
                    self._set_journal(journal_path, journal, "complete", result="failure")
            elif not stage.exists() and not target.exists() and journal["phase"] == "prepared":
                action = "complete_failed_prepare"
                if not dry_run:
                    self._set_journal(journal_path, journal, "complete", result="failure")
            else:
                action = "manual_resolution_required"
                if not dry_run:
                    self._set_journal(journal_path, journal, "needs_resolution", result="needs_resolution")
            actions.append({"import_id": import_id, "action": action})
        status = "needs_resolution" if any(item["action"] == "manual_resolution_required" for item in actions) else "success"
        return {"status": status, "dry_run": dry_run, "actions": actions}

    def _find_existing_import(self, archive_sha256: str) -> str | None:
        for receipt in self.list_imports()["imports"]:
            if receipt["archive_sha256"] == archive_sha256:
                return receipt["import_id"]
        return None

    @staticmethod
    def _write_journal(path: Path, journal: dict[str, Any]) -> None:
        diagnostics = validate_record("exchange-import-journal", journal, actor="stored")
        if diagnostics:
            raise ResearchKBError(diagnostics[0])
        atomic_write_bytes(path, serialize_json(journal), journal["import_id"])

    def _set_journal(
        self,
        path: Path,
        journal: dict[str, Any],
        phase: str,
        *,
        result: str | None = None,
    ) -> None:
        journal["phase"] = phase
        journal["updated_at"] = timestamp(self.clock)
        if result is not None:
            journal["result"] = result
        self._write_journal(path, journal)


def _validate_supported_bundle(
    inspection: ArchiveInspection,
    profile: SafeReaderProfile,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], str]:
    manifest_bytes = inspection.structured_content.get("manifest.json")
    receipt_bytes = inspection.structured_content.get("receipt.json")
    manifest = _load_json(manifest_bytes, "exchange-bundle-manifest")
    receipt = _load_json(receipt_bytes, "exchange-export-receipt")
    for kind, record in (("exchange-bundle-manifest", manifest), ("exchange-export-receipt", receipt)):
        diagnostics = validate_record(kind, record, actor="stored")
        if diagnostics:
            raise ResearchKBError(diagnostics[0])
    manifest_sha256 = sha256_bytes(manifest_bytes or b"")
    if (
        receipt["manifest_sha256"] != manifest_sha256
        or receipt["export_id"] != manifest["export_id"]
        or receipt["selection"] != manifest["selection"]
        or receipt["basis_digest"] != manifest["basis_digest"]
    ):
        raise _error(SCHEMA_VALIDATION_FAILED, "/receipt", "Exchange receipt does not bind the manifest")
    entry_map = inspection.entry_map()
    declared_paths = {item["path"] for item in manifest["entries"]}
    actual_paths = set(entry_map) - {"manifest.json", "receipt.json"}
    if declared_paths != actual_paths or len(declared_paths) != len(manifest["entries"]):
        raise _error(SCHEMA_VALIDATION_FAILED, "/entries", "Exchange manifest entry inventory is incomplete")
    for declared in manifest["entries"]:
        actual = entry_map[declared["path"]]
        if declared["sha256"] != actual.sha256 or declared["bytes"] != actual.uncompressed_bytes:
            raise _error(SCHEMA_VALIDATION_FAILED, f"/{declared['path']}", "Exchange entry digest or size mismatch")
    envelopes: list[dict[str, Any]] = []
    actual_kind_counts: Counter[str] = Counter()
    for path in sorted(name for name in actual_paths if name.startswith("records/")):
        kind = path.removeprefix("records/").removesuffix(".jsonl")
        if kind not in _PAYLOAD_KINDS or not _UUID_KIND_PATTERN.fullmatch(kind):
            raise _error(SCHEMA_VALIDATION_FAILED, f"/{path}", "Exchange record kind is not allowlisted")
        records = _load_jsonl(
            inspection.structured_content.get(path),
            "exchange-record-envelope",
            max_records=profile.max_structured_records,
        )
        for record in records:
            diagnostics = validate_record("exchange-record-envelope", record, actor="stored")
            if diagnostics:
                raise ResearchKBError(diagnostics[0])
            if record["record_kind"] != kind or sha256_bytes(serialize_json(record["record"])) != record["revision_digest"]:
                raise _error(SCHEMA_VALIDATION_FAILED, f"/{path}", "Exchange record envelope binding is invalid")
            diagnostics = validate_record(kind, record["record"], actor="stored")
            if diagnostics:
                raise ResearchKBError(diagnostics[0])
        envelopes.extend(records)
        actual_kind_counts[kind] += len(records)
    sources = _load_jsonl(
        inspection.structured_content.get("sources/index.jsonl", b""),
        "exchange-source-envelope",
        max_records=profile.max_structured_records,
    )
    for source in sources:
        diagnostics = validate_record("exchange-source-envelope", source, actor="stored")
        if diagnostics:
            raise ResearchKBError(diagnostics[0])
        entry = entry_map.get(source["archive_path"])
        if (
            entry is None
            or entry.sha256 != source["source_fingerprint"]["value"]
            or entry.uncompressed_bytes != source["bytes"]
            or not entry.prefix.startswith(bytes((37, 80, 68, 70, 45)))
        ):
            raise _error(SCHEMA_VALIDATION_FAILED, f"/{source['archive_path']}", "Exchange source binding is invalid")
    if (
        len(envelopes) > profile.max_structured_records
        or len(envelopes) != manifest["record_count"]
        or dict(sorted(actual_kind_counts.items())) != manifest["record_kind_counts"]
        or len(sources) != manifest["source_count"]
    ):
        raise _error(SCHEMA_VALIDATION_FAILED, "/manifest", "Exchange manifest counts do not match content")
    for declared in manifest["entries"]:
        path = declared["path"]
        if path.startswith("records/"):
            kind = path.removeprefix("records/").removesuffix(".jsonl")
            expected_kind = "records"
            expected_count = actual_kind_counts[kind]
        elif path == "sources/index.jsonl":
            expected_kind = "source_index"
            expected_count = len(sources)
        elif path.startswith("sources/sha256/"):
            expected_kind = "source"
            expected_count = 0
        else:
            raise _error(SCHEMA_VALIDATION_FAILED, f"/{path}", "Exchange manifest contains an unsupported data path")
        if declared["entry_kind"] != expected_kind or declared["record_count"] != expected_count:
            raise _error(SCHEMA_VALIDATION_FAILED, f"/{path}", "Exchange manifest entry classification is invalid")
    return manifest, envelopes, sources, manifest_sha256


def _project_conflicts(
    envelopes: list[dict[str, Any]],
    local_entries: list[tuple[str, dict[str, Any]]],
    local_workspace_id: str,
) -> list[dict[str, Any]]:
    local: dict[tuple[str, str], dict[str, Any]] = {}
    paper_identities: list[tuple[str, dict[str, Any]]] = []
    for kind, record in local_entries:
        try:
            envelope = build_exchange_envelope(local_workspace_id, kind, record)
        except (KeyError, ResearchKBError):
            continue
        local[(envelope["record_kind"], envelope["origin_record_id"])] = envelope
        if envelope["record_kind"] == "exchange-paper-identity":
            paper_identities.append((record["paper_id"], envelope))
    result = []
    for envelope in envelopes:
        key = (envelope["record_kind"], envelope["origin_record_id"])
        local_match = local.get(key)
        local_record_id = local_match["origin_record_id"] if local_match is not None else None
        if local_match is not None and local_match["revision_digest"] == envelope["revision_digest"]:
            classification = "exact_local_duplicate"
        elif local_match is not None:
            classification = "semantic_conflict"
        elif envelope["record_kind"] == "exchange-paper-identity":
            external_record = envelope["record"]
            fingerprint = external_record["source_fingerprint"]["value"]
            doi = (external_record["bibliography"].get("doi") or "").casefold()
            identity_match = next(
                (
                    (paper_id, item)
                    for paper_id, item in paper_identities
                    if item["record"]["source_fingerprint"]["value"] == fingerprint
                    or (doi and (item["record"]["bibliography"].get("doi") or "").casefold() == doi)
                ),
                None,
            )
            if identity_match is not None:
                local_record_id = identity_match[0]
                classification = "paper_identity_match"
            else:
                classification = "new_external_identity"
        else:
            classification = "new_external_revision"
        result.append(
            {
                "origin_workspace_id": envelope["origin_workspace_id"],
                "origin_record_id": envelope["origin_record_id"],
                "record_kind": envelope["record_kind"],
                "revision_digest": envelope["revision_digest"],
                "classification": classification,
                "local_record_id": local_record_id,
                "local_admissibility": "external_unreviewed",
            }
        )
    return sorted(result, key=lambda item: (item["record_kind"], item["origin_record_id"], item["revision_digest"]))


def _build_import_receipt(
    import_id: str,
    local_workspace_id: str,
    preview: Mapping[str, Any],
    created_at: str,
) -> dict[str, Any]:
    receipt = {
        "schema_version": "1.0",
        "import_id": import_id,
        "local_workspace_id": local_workspace_id,
        "origin_workspace_id": preview["origin_workspace_id"],
        "export_id": preview["export_id"],
        "archive_sha256": preview["archive_sha256"],
        "manifest_sha256": preview["manifest_sha256"],
        "basis_digest": preview["basis_digest"],
        "compatibility": "supported",
        "trust_projection": TRUST_PROJECTION,
        "record_count": preview["record_count"],
        "source_count": preview["source_count"],
        "conflict_counts": preview["conflict_counts"],
        "local_review_status": "unreviewed",
        "created_at": created_at,
    }
    diagnostics = validate_record("exchange-import-receipt", receipt, actor="stored")
    if diagnostics:
        raise ResearchKBError(diagnostics[0])
    return receipt


def validate_import_package(
    path: Path,
    archive_sha256: str | None = None,
    *,
    profile: SafeReaderProfile | None = None,
) -> dict[str, Any]:
    receipt, _, _ = _read_validated_import_package(
        path,
        archive_sha256,
        profile=profile,
    )
    return receipt


def _read_validated_import_package(
    path: Path,
    archive_sha256: str | None = None,
    *,
    profile: SafeReaderProfile | None = None,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    limits = profile or SafeReaderProfile()
    if not path.is_dir() or path.is_symlink():
        raise _error(PATH_ESCAPE, "/package", "Exchange import package is unavailable or unsafe")
    receipt = read_json_document(path / "import_receipt.json", record_kind="exchange-import-receipt")
    diagnostics = validate_record("exchange-import-receipt", receipt, actor="stored")
    if diagnostics:
        raise ResearchKBError(diagnostics[0])
    if archive_sha256 is not None and receipt["archive_sha256"] != archive_sha256:
        raise _error(PROTECTED_INPUT_CHANGED, "/package", "Exchange import package receipt digest changed")
    try:
        structured_files, inspected_files, directories = _scan_import_package(path, limits)
    except OSError as error:
        raise _error(
            PROTECTED_INPUT_CHANGED,
            "/package",
            "Exchange import package changed or became inaccessible during validation",
        ) from error
    manifest_bytes = structured_files.get("manifest.json")
    manifest = _load_json(manifest_bytes, "exchange-bundle-manifest")
    expected_files = {
        "manifest.json",
        "receipt.json",
        "import_receipt.json",
        *[item["path"] for item in manifest.get("entries", []) if isinstance(item, Mapping)],
    }
    if set(inspected_files) != expected_files:
        raise _error(SCHEMA_VALIDATION_FAILED, "/package", "Exchange import package file inventory changed")
    expected_directories = {
        parent
        for name in expected_files
        for parent in _parent_bundle_paths(name)
    }
    if directories != expected_directories:
        raise _error(SCHEMA_VALIDATION_FAILED, "/package", "Exchange import package directory inventory changed")
    inspected = [
        item
        for name, item in sorted(inspected_files.items())
        if name != "import_receipt.json"
    ]
    structured = {
        name: content
        for name, content in structured_files.items()
        if name != "import_receipt.json"
    }
    inspection = ArchiveInspection(
        archive_sha256=receipt["archive_sha256"],
        archive_bytes=sum(item.uncompressed_bytes for item in inspected_files.values()),
        entries=tuple(inspected),
        structured_content=structured,
        canonical_serialization=True,
    )
    validated_manifest, envelopes, _, manifest_sha256 = _validate_supported_bundle(inspection, limits)
    if (
        receipt["manifest_sha256"] != manifest_sha256
        or receipt["origin_workspace_id"] != validated_manifest["origin_workspace_id"]
        or receipt["export_id"] != validated_manifest["export_id"]
        or receipt["record_count"] != validated_manifest["record_count"]
        or receipt["source_count"] != validated_manifest["source_count"]
    ):
        raise _error(SCHEMA_VALIDATION_FAILED, "/package", "Exchange import receipt no longer binds its package")
    return receipt, validated_manifest, envelopes


def _external_record_label(envelope: Mapping[str, Any]) -> str:
    record = envelope.get("record")
    if not isinstance(record, Mapping):
        return str(envelope["origin_record_id"])
    bibliography = record.get("bibliography")
    if isinstance(bibliography, Mapping) and isinstance(bibliography.get("title"), str):
        value = bibliography["title"]
    else:
        value = next(
            (
                record[field]
                for field in ("title", "question_text", "statement", "content", "claim", "name")
                if isinstance(record.get(field), str) and record[field]
            ),
            envelope["origin_record_id"],
        )
    return value[:500]


def _scan_import_package(
    path: Path,
    profile: SafeReaderProfile,
) -> tuple[dict[str, bytes], dict[str, InspectedEntry], set[str]]:
    root = path.resolve()
    structured: dict[str, bytes] = {}
    inspected: dict[str, InspectedEntry] = {}
    directories: set[str] = set()
    total = 0
    count = 0
    for current, dir_names, file_names in os.walk(path, topdown=True, followlinks=False):
        current_path = Path(current)
        for name in sorted(dir_names):
            child = current_path / name
            relative = child.relative_to(path).as_posix()
            _validate_bundle_path(relative + "/placeholder", profile)
            if child.is_symlink() or _has_unsafe_component(child) or not child.is_dir():
                raise _error(PATH_ESCAPE, f"/{relative}", "Exchange import package contains an unsafe directory")
            directories.add(relative)
        for name in sorted(file_names):
            child = current_path / name
            relative = _validate_bundle_path(child.relative_to(path).as_posix(), profile)
            if child.is_symlink() or _has_unsafe_component(child) or not child.is_file():
                raise _error(PATH_ESCAPE, f"/{relative}", "Exchange import package contains an unsafe file")
            if not child.resolve().is_relative_to(root):
                raise _error(PATH_ESCAPE, f"/{relative}", "Exchange import package escaped its root")
            count += 1
            if count > profile.max_entries + 1:
                raise _error(INPUT_TOO_LARGE, "/package", "Exchange import package entry count exceeds the safe-reader budget")
            size = child.stat().st_size
            if size > profile.max_entry_uncompressed_bytes:
                raise _error(INPUT_TOO_LARGE, f"/{relative}", "Exchange import package entry exceeds the safe-reader budget")
            total += size
            if total > profile.max_staging_bytes:
                raise _error(INPUT_TOO_LARGE, "/package", "Exchange import package exceeds the staging budget")
            digest = __import__("hashlib").sha256()
            actual = 0
            prefix = b""
            collect = not relative.startswith("sources/sha256/")
            content = bytearray() if collect else None
            with child.open("rb") as source:
                while chunk := source.read(1024 * 1024):
                    actual += len(chunk)
                    digest.update(chunk)
                    if len(prefix) < 8:
                        prefix = (prefix + chunk)[:8]
                    if content is not None:
                        content.extend(chunk)
            if actual != size:
                raise _error(PROTECTED_INPUT_CHANGED, f"/{relative}", "Exchange import package changed during validation")
            if relative == "manifest.json" and actual > profile.max_manifest_bytes:
                raise _error(INPUT_TOO_LARGE, "/manifest.json", "Exchange manifest exceeds the safe-reader budget")
            inspected[relative] = InspectedEntry(
                path=relative,
                sha256=digest.hexdigest(),
                compressed_bytes=actual,
                uncompressed_bytes=actual,
                compress_type=zipfile.ZIP_STORED,
                prefix=prefix,
            )
            if content is not None:
                structured[relative] = bytes(content)
    return structured, inspected, directories


def _parent_bundle_paths(path: str) -> set[str]:
    parts = PurePosixPath(path).parts[:-1]
    return {PurePosixPath(*parts[:index]).as_posix() for index in range(1, len(parts) + 1)}


def _import_result(
    import_id: str,
    preview: Mapping[str, Any],
    result: str,
    *,
    persistent_writes: int,
) -> dict[str, Any]:
    return {
        "status": "success",
        "interface_version": "1.0",
        "result": result,
        "import_id": import_id,
        "origin_workspace_id": preview["origin_workspace_id"],
        "archive_sha256": preview["archive_sha256"],
        "record_count": preview["record_count"],
        "source_count": preview["source_count"],
        "trust_projection": TRUST_PROJECTION,
        "persistent_writes": persistent_writes,
        "canonical_scientific_write": False,
    }


def _validate_archive_path(path: Path, profile: SafeReaderProfile) -> Path:
    if not path.is_absolute() or not path.is_file() or _has_unsafe_component(path):
        raise _error(PATH_ESCAPE, "/archive", "Exchange archive must be an absolute regular file")
    size = path.stat().st_size
    if size <= 0 or size > profile.max_archive_bytes:
        raise _error(INPUT_TOO_LARGE, "/archive", "Exchange archive size is outside the safe-reader budget")
    return path


def _validate_bundle_path(value: str, profile: SafeReaderProfile) -> str:
    if (
        not value
        or value != unicodedata.normalize("NFC", value)
        or "\\" in value
        or "\x00" in value
        or value.startswith(("/", "//"))
        or len(value.encode("utf-8")) > profile.max_bundle_path_bytes
    ):
        raise _error(PATH_ESCAPE, "/archive", "Exchange archive contains an unsafe path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts) or value.endswith("/"):
        raise _error(PATH_ESCAPE, "/archive", "Exchange archive contains traversal or directory entries")
    for part in path.parts:
        stem = part.split(".", 1)[0].upper()
        if part.endswith((" ", ".")) or stem in _RESERVED_WINDOWS_NAMES or ":" in part:
            raise _error(PATH_ESCAPE, "/archive", "Exchange archive path is not cross-platform safe")
    return value


def _validate_info(info: zipfile.ZipInfo, profile: SafeReaderProfile) -> None:
    if info.flag_bits & 0x1:
        raise _error(SCHEMA_VALIDATION_FAILED, f"/{info.filename}", "encrypted Exchange entries are unsupported")
    mode = (info.external_attr >> 16) & 0o170000
    if mode not in {0, stat.S_IFREG}:
        raise _error(PATH_ESCAPE, f"/{info.filename}", "Exchange archive contains a link or special file")
    if info.file_size < 0 or info.file_size > profile.max_entry_uncompressed_bytes:
        raise _error(INPUT_TOO_LARGE, f"/{info.filename}", "Exchange entry exceeds the safe-reader budget")
    if info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
        raise _error(SCHEMA_VALIDATION_FAILED, f"/{info.filename}", "Exchange compression method is unsupported")
    ratio = info.file_size / max(info.compress_size, 1)
    if ratio > profile.max_compression_ratio:
        raise _error(INPUT_TOO_LARGE, f"/{info.filename}", "Exchange compression ratio exceeds the safe-reader budget")


def _canonical_info(info: zipfile.ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0o777
    return (
        info.compress_type == zipfile.ZIP_STORED
        and info.date_time == (1980, 1, 1, 0, 0, 0)
        and not info.comment
        and not info.extra
        and mode == 0o600
    )


def _load_json(content: bytes | None, kind: str) -> dict[str, Any]:
    if content is None or content.startswith(b"\xef\xbb\xbf") or b"\r" in content:
        raise _error(SCHEMA_VALIDATION_FAILED, f"/{kind}", "Exchange structured entry is missing or noncanonical")
    try:
        value = json.loads(content.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise _error(SCHEMA_VALIDATION_FAILED, f"/{kind}", "Exchange structured entry is invalid JSON") from error
    if not isinstance(value, dict):
        raise _error(SCHEMA_VALIDATION_FAILED, f"/{kind}", "Exchange JSON root must be an object")
    return value


def _load_jsonl(content: bytes | None, kind: str, *, max_records: int) -> list[dict[str, Any]]:
    if content in {None, b""}:
        return []
    if content.startswith(b"\xef\xbb\xbf") or b"\r" in content or not content.endswith(b"\n"):
        raise _error(SCHEMA_VALIDATION_FAILED, f"/{kind}", "Exchange JSONL is noncanonical")
    records = []
    try:
        for line in content[:-1].split(b"\n"):
            value = json.loads(line.decode("utf-8", errors="strict"))
            if not isinstance(value, dict):
                raise ValueError("record is not an object")
            records.append(value)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise _error(SCHEMA_VALIDATION_FAILED, f"/{kind}", "Exchange JSONL record is invalid") from error
    if len(records) > max_records:
        raise _error(INPUT_TOO_LARGE, f"/{kind}", "Exchange structured record count exceeds the safe-reader budget")
    return records


def _compatibility(bundle_format: Any) -> str:
    if bundle_format == BUNDLE_FORMAT:
        return "supported"
    match = re.fullmatch(r"research-kb-exchange-bundle@(\d+)\.(\d+)", bundle_format or "")
    if match is None:
        return "unknown_or_incompatible"
    major, minor = map(int, match.groups())
    if major == 1 and minor > 0:
        return "newer_but_safe_read_only"
    if major < 1:
        return "migration_required"
    return "unknown_or_incompatible"


def _validate_import_request(request: Mapping[str, Any]) -> None:
    required = {"import_id", "expected_archive_sha256", "expected_basis_digest", "created_at"}
    if not isinstance(request, Mapping) or set(request) != required:
        raise _error(SCHEMA_VALIDATION_FAILED, "", "Exchange import request fields do not match the contract")
    validate_id(request["import_id"], Namespace.IMPORT)
    for field in ("expected_archive_sha256", "expected_basis_digest"):
        value = request[field]
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
            raise _error(SCHEMA_VALIDATION_FAILED, f"/{field}", "Exchange import digest is invalid")
    _require_timestamp(request["created_at"])


def _require_timestamp(value: Any) -> str:
    if not isinstance(value, str) or not value.endswith("Z") or "T" not in value:
        raise _error(SCHEMA_VALIDATION_FAILED, "/created_at", "timestamp must be UTC RFC3339")
    return value


def _hash_file(path: Path) -> str:
    digest = __import__("hashlib").sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _has_unsafe_component(path: Path) -> bool:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        if not os.path.lexists(current):
            continue
        if current.is_symlink():
            return True
        is_junction = getattr(current, "is_junction", None)
        if callable(is_junction) and is_junction():
            return True
        try:
            attributes = getattr(os.lstat(current), "st_file_attributes", 0)
        except OSError:
            continue
        if attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
            return True
    return False


def _error(code: str, path: str, message: str) -> ResearchKBError:
    return ResearchKBError(Diagnostic(code, "exchange-import", None, path, message))


__all__ = [
    "SAFE_READER_PROFILE_ID",
    "ArchiveInspection",
    "ExchangeArchiveReader",
    "ExchangeImportService",
    "SafeReaderProfile",
    "validate_import_package",
]
