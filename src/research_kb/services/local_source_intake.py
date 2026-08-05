from __future__ import annotations

import hashlib
import json
import os
import stat
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO

from research_kb.acquisition_paths import local_inbox_destination
from research_kb.bundle import load_workspace_entries, records_of_kind, validate_workspace_entries
from research_kb.discovery.acquisition import FileIdentity
from research_kb.errors import (
    GROUNDING_MISMATCH,
    INPUT_TOO_LARGE,
    INVALID_AUTHORITY,
    PATH_ESCAPE,
    SCHEMA_VALIDATION_FAILED,
    WRITE_CONFLICT,
    Diagnostic,
    ResearchKBError,
)
from research_kb.services._pipeline_authority import require_job_authority
from research_kb.services.source_asset import SourceAssetService
from research_kb.source_assets import current_source_asset_heads
from research_kb.storage.json_io import file_sha256
from research_kb.workspace import WorkspaceLayout


Clock = Callable[[], datetime]
OperationHook = Callable[[str], None]
MAX_PDF_BYTES = 64 * 1024 * 1024
MAX_SCAN_ENTRIES = 1000
PDF_SIGNATURE = bytes((37, 80, 68, 70, 45))
_IS_WINDOWS = os.name == "nt"
_WINDOWS_UNSUPPORTED_HARDLINK_ERRORS = frozenset({1, 50})


class LocalSourceIntakeService:
    def __init__(
        self,
        layout: WorkspaceLayout,
        *,
        clock: Clock | None = None,
        operation_hook: OperationHook | None = None,
    ):
        self.layout = layout
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.operation_hook = operation_hook

    def copy(
        self,
        *,
        source: Path,
        job_id: str,
        paper_id: str | None,
        asset_role: str,
        actor: str,
        fixture_origin: str | None = None,
    ) -> dict[str, Any]:
        source = Path(source)
        source_identity = _inspect_source(source)
        with source.open("rb") as stream:
            return self.copy_stream(
                stream=stream,
                job_id=job_id,
                paper_id=paper_id,
                asset_role=asset_role,
                actor=actor,
                expected_sha256=source_identity.sha256,
                expected_size=source_identity.size,
                validate_input=lambda: _verify_unchanged_source(source, source_identity),
                fixture_origin=fixture_origin,
            )

    def copy_stream(
        self,
        *,
        stream: BinaryIO,
        job_id: str,
        paper_id: str | None,
        asset_role: str,
        actor: str,
        expected_sha256: str | None = None,
        expected_size: int | None = None,
        validate_input: Callable[[], None] | None = None,
        fixture_origin: str | None = None,
    ) -> dict[str, Any]:
        require_job_authority(self.layout, job_id, "copy_into_local_inbox")
        if actor != "user":
            raise ResearchKBError(
                Diagnostic(INVALID_AUTHORITY, "local-source-intake", job_id, "/actor", "inbox copy requires exact user authority")
            )
        destination = local_inbox_destination(self.layout, f"{job_id}.pdf")
        source_ref = destination.source_ref.to_dict()
        existing = self._matching_copy_state(
            job_id=job_id,
            paper_id=paper_id,
            asset_role=asset_role,
            source_ref=source_ref,
        )
        receipt_hash = None if existing is None else existing["source_fingerprint"]["value"]
        if expected_sha256 is not None and receipt_hash is not None and expected_sha256 != receipt_hash:
            raise _write_conflict(job_id, "copy retry input differs from the existing receipt")
        if os.path.lexists(destination.final_path):
            if existing is not None and _safe_pdf_digest(destination.final_path) == receipt_hash:
                if validate_input is not None:
                    validate_input()
                return _result(existing, result="no_change", persistent_writes=0, event_id=None)
            raise _write_conflict(job_id, "inbox copy target already exists without the matching receipt")

        temporary = destination.inbox / f".research-kb-copy-{job_id}.part.pdf"
        staged = _resume_or_stage_stream(
            stream,
            temporary,
            expected_sha256=expected_sha256,
            expected_size=expected_size,
            job_id=job_id,
        )
        mutation = None
        try:
            if validate_input is not None:
                validate_input()
            if receipt_hash is not None and staged.sha256 != receipt_hash:
                raise _write_conflict(job_id, "staged copy differs from the existing receipt")
            if self.operation_hook is not None:
                self.operation_hook("staged")
            if validate_input is not None:
                validate_input()
            mutation = SourceAssetService(self.layout).register_staged_inbox(
                job_id=job_id,
                paper_id=paper_id,
                asset_role=asset_role,
                source_hash=staged.sha256,
                actor=actor,
                fixture_origin=fixture_origin,
                validate_staged_source=lambda: _verify_staged_source(temporary, staged),
            )
            if self.operation_hook is not None:
                self.operation_hook("receipted")
            if validate_input is not None:
                validate_input()
            _publish_owned(temporary, destination.final_path, staged, job_id)
            if self.operation_hook is not None:
                self.operation_hook("published")
        except Exception:
            if mutation is None:
                _safe_unlink_owned(temporary, source_identity=staged)
            raise
        assert mutation is not None
        return _result(
            mutation.state,
            result="copied" if mutation.transaction is not None else "recovered",
            persistent_writes=1 + (1 if mutation.transaction is not None else 0),
            event_id=None if mutation.transaction is None else mutation.transaction.event_id,
        )

    def recover_copy(self, *, job_id: str, actor: str) -> dict[str, Any]:
        require_job_authority(self.layout, job_id, "copy_into_local_inbox")
        if actor != "user":
            raise ResearchKBError(
                Diagnostic(
                    INVALID_AUTHORITY,
                    "local-source-intake",
                    job_id,
                    "/actor",
                    "inbox copy recovery requires exact user authority",
                )
            )
        destination = local_inbox_destination(self.layout, f"{job_id}.pdf")
        source_state = self._copy_state_for_job(job_id)
        if source_state["source_ref"] != destination.source_ref.to_dict():
            raise _write_conflict(job_id, "copy receipt does not match its owned inbox destination")
        receipt_hash = source_state["source_fingerprint"]["value"]
        if os.path.lexists(destination.final_path):
            if _safe_pdf_digest(destination.final_path) == receipt_hash:
                return _result(
                    source_state,
                    result="no_change",
                    persistent_writes=0,
                    event_id=None,
                )
            raise _write_conflict(job_id, "inbox copy target exists without the matching receipt")

        temporary = destination.inbox / f".research-kb-copy-{job_id}.part.pdf"
        staged = _inspect_staged_source(temporary)
        if staged.sha256 != receipt_hash:
            raise _write_conflict(job_id, "operation-owned inbox copy partial differs from its receipt")
        _publish_owned(temporary, destination.final_path, staged, job_id)
        return _result(
            source_state,
            result="recovered",
            persistent_writes=0,
            event_id=None,
        )

    def scan(
        self,
        *,
        max_entries: int = 100,
        min_stable_age_seconds: int = 5,
    ) -> dict[str, Any]:
        if not 1 <= max_entries <= MAX_SCAN_ENTRIES:
            raise ResearchKBError(
                Diagnostic(SCHEMA_VALIDATION_FAILED, "inbox-scan", None, "/max_entries", "max_entries is outside the bounded scan range")
            )
        candidates = self._scan_candidates(min_stable_age_seconds=min_stable_age_seconds)
        return {
            "status": "success",
            "interface_version": "1.0",
            "candidates": candidates[:max_entries],
            "persistent_writes": 0,
        }

    def select(
        self,
        *,
        candidate_handle: str,
        job_id: str,
        paper_id: str | None,
        asset_role: str,
        actor: str,
        min_stable_age_seconds: int = 5,
    ) -> dict[str, Any]:
        require_job_authority(self.layout, job_id, "select_inbox_candidate")
        candidates = self._scan_candidates(min_stable_age_seconds=min_stable_age_seconds)
        candidate = next((item for item in candidates if item["candidate_token"] == candidate_handle), None)
        if candidate is None:
            replay_candidates = self._scan_candidates(
                min_stable_age_seconds=min_stable_age_seconds,
                include_registered=True,
            )
            replay = next(
                (item for item in replay_candidates if item["candidate_token"] == candidate_handle),
                None,
            )
            if replay is not None and self._matching_reference_state(
                job_id=job_id,
                paper_id=paper_id,
                asset_role=asset_role,
                source_ref=replay["source_ref"],
            ) is not None:
                candidate = replay
        if candidate is None:
            raise _write_conflict(job_id, "inbox candidate changed or is no longer eligible")
        source_ref = candidate["source_ref"]
        mutation = SourceAssetService(self.layout).register_reference(
            job_id=job_id,
            paper_id=paper_id,
            asset_role=asset_role,
            root_id=source_ref["root_id"],
            relative_path=source_ref["relative_path"],
            actor=actor,
        )
        return _result(
            mutation.state,
            result="selected",
            persistent_writes=0 if mutation.transaction is None else 1,
            event_id=None if mutation.transaction is None else mutation.transaction.event_id,
        )

    def _scan_candidates(
        self,
        *,
        min_stable_age_seconds: int,
        include_registered: bool = False,
    ) -> list[dict[str, Any]]:
        if (
            isinstance(min_stable_age_seconds, bool)
            or not isinstance(min_stable_age_seconds, int)
            or min_stable_age_seconds < 0
        ):
            raise ResearchKBError(
                Diagnostic(SCHEMA_VALIDATION_FAILED, "inbox-scan", None, "/min_stable_age_seconds", "stability window cannot be negative")
            )
        binding = local_inbox_destination(self.layout, "scan-placeholder.pdf")
        entries = load_workspace_entries(self.layout)
        validate_workspace_entries(entries)
        known_refs = {
            (record["source_ref"]["root_id"], record["source_ref"]["relative_path"])
            for kind in ("registry-paper", "source-asset-state")
            for record in records_of_kind(entries, kind)
        }
        candidates: list[dict[str, Any]] = []
        for path in _bounded_inbox_entries(binding.inbox):
            observed = _scan_candidate(
                path,
                root_id=binding.root_id,
                root=self.layout.source_roots[binding.root_id],
                workspace_id=self.layout.workspace_id,
                now=self.clock(),
                min_stable_age_seconds=min_stable_age_seconds,
            )
            if observed is None:
                continue
            source_ref = observed["source_ref"]
            if (
                not include_registered
                and (source_ref["root_id"], source_ref["relative_path"]) in known_refs
            ):
                continue
            candidates.append(observed)
        return candidates

    def _matching_copy_state(
        self,
        *,
        job_id: str,
        paper_id: str | None,
        asset_role: str,
        source_ref: dict[str, str],
    ) -> dict[str, Any] | None:
        return self._matching_root_state(
            job_id=job_id,
            paper_id=paper_id,
            asset_role=asset_role,
            source_ref=source_ref,
            reason="copied_into_local_inbox",
        )

    def _copy_state_for_job(self, job_id: str) -> dict[str, Any]:
        entries = load_workspace_entries(self.layout)
        validate_workspace_entries(entries)
        states = records_of_kind(entries, "source-asset-state")
        roots = [
            item
            for item in states
            if item["revision"] == 1
            and item["job_id"] == job_id
            and item["asset_role"] == "main_pdf"
            and item["reason"] == "copied_into_local_inbox"
        ]
        if len(roots) != 1:
            raise _write_conflict(job_id, "copy recovery requires exactly one owned source receipt")
        heads = {
            item["source_asset_id"]: item for item in current_source_asset_heads(states)
        }
        return heads[roots[0]["source_asset_id"]]

    def _matching_reference_state(
        self,
        *,
        job_id: str,
        paper_id: str | None,
        asset_role: str,
        source_ref: dict[str, str],
    ) -> dict[str, Any] | None:
        return self._matching_root_state(
            job_id=job_id,
            paper_id=paper_id,
            asset_role=asset_role,
            source_ref=source_ref,
            reason="reference_registered",
        )

    def _matching_root_state(
        self,
        *,
        job_id: str,
        paper_id: str | None,
        asset_role: str,
        source_ref: dict[str, str],
        reason: str,
    ) -> dict[str, Any] | None:
        entries = load_workspace_entries(self.layout)
        validate_workspace_entries(entries)
        states = records_of_kind(entries, "source-asset-state")
        heads = {
            item["source_asset_id"]: item for item in current_source_asset_heads(states)
        }
        for root in states:
            if (
                root["revision"] == 1
                and root["job_id"] == job_id
                and root["paper_id"] == paper_id
                and root["asset_role"] == asset_role
                and root["source_ref"] == source_ref
                and root["reason"] == reason
            ):
                return heads[root["source_asset_id"]]
        return None


def _inspect_source(path: Path) -> FileIdentity:
    if not path.is_absolute():
        raise ResearchKBError(
            Diagnostic(PATH_ESCAPE, "local-source-intake", None, "/source", "copy source must be an absolute path")
        )
    _reject_unsafe_components(path)
    try:
        metadata = os.lstat(path)
    except OSError as error:
        raise ResearchKBError(
            Diagnostic(SCHEMA_VALIDATION_FAILED, "local-source-intake", None, "/source", "copy source is missing or inaccessible")
        ) from error
    if not stat.S_ISREG(metadata.st_mode) or _is_unsafe_link(path) or getattr(metadata, "st_nlink", 1) != 1:
        raise ResearchKBError(
            Diagnostic(PATH_ESCAPE, "local-source-intake", None, "/source", "copy source must be one unlinked regular file")
        )
    if path.suffix.casefold() != ".pdf" or not 1 <= metadata.st_size <= MAX_PDF_BYTES:
        raise ResearchKBError(
            Diagnostic(SCHEMA_VALIDATION_FAILED, "local-source-intake", None, "/source", "copy source must be a bounded PDF")
        )
    digest = file_sha256(path)
    if digest is None:
        raise ResearchKBError(
            Diagnostic(SCHEMA_VALIDATION_FAILED, "local-source-intake", None, "/source", "copy source could not be hashed")
        )
    with path.open("rb") as stream:
        if stream.read(len(PDF_SIGNATURE)) != PDF_SIGNATURE:
            raise ResearchKBError(
                Diagnostic(SCHEMA_VALIDATION_FAILED, "local-source-intake", None, "/source", "copy source extension and content do not match")
            )
    return FileIdentity(metadata.st_dev, metadata.st_ino, metadata.st_size, digest)


def _bounded_inbox_entries(inbox: Path) -> list[Path]:
    entries: list[Path] = []
    try:
        with os.scandir(inbox) as iterator:
            for entry in iterator:
                if len(entries) >= MAX_SCAN_ENTRIES:
                    raise ResearchKBError(
                        Diagnostic(
                            INPUT_TOO_LARGE,
                            "inbox-scan",
                            None,
                            "/local_inbox",
                            "local_inbox contains more entries than one bounded scan permits",
                        )
                    )
                entries.append(Path(entry.path))
    except ResearchKBError:
        raise
    except OSError as error:
        raise ResearchKBError(
            Diagnostic(SCHEMA_VALIDATION_FAILED, "inbox-scan", None, "/local_inbox", "local_inbox could not be scanned")
        ) from error
    return sorted(entries, key=lambda item: item.name.casefold())


def _resume_or_stage_stream(
    stream: BinaryIO,
    temporary: Path,
    *,
    expected_sha256: str | None,
    expected_size: int | None,
    job_id: str,
) -> FileIdentity:
    if os.path.lexists(temporary):
        if expected_sha256 is None:
            raise _write_conflict(job_id, "copy partial recovery requires the original expected digest")
        existing = _inspect_staged_source(temporary)
        if expected_sha256 is not None and existing.sha256 != expected_sha256:
            raise _write_conflict(job_id, "operation-owned inbox copy partial differs from retry input")
        if expected_size is not None and existing.size != expected_size:
            raise _write_conflict(job_id, "operation-owned inbox copy partial size differs from retry input")
        return existing
    return _copy_bounded_stream(
        stream,
        temporary,
        expected_sha256=expected_sha256,
        expected_size=expected_size,
    )


def _copy_bounded_stream(
    stream: BinaryIO,
    temporary: Path,
    *,
    expected_sha256: str | None,
    expected_size: int | None,
) -> FileIdentity:
    hasher = hashlib.sha256()
    size = 0
    prefix = bytearray()
    partial_identity: FileIdentity | None = None
    try:
        with temporary.open("xb") as writer:
            try:
                while chunk := stream.read(1024 * 1024):
                    if not isinstance(chunk, bytes):
                        raise ResearchKBError(
                            Diagnostic(SCHEMA_VALIDATION_FAILED, "local-source-intake", None, "/source", "copy stream returned non-byte content")
                        )
                    size += len(chunk)
                    if size > MAX_PDF_BYTES:
                        raise ResearchKBError(
                            Diagnostic(SCHEMA_VALIDATION_FAILED, "local-source-intake", None, "/source", "copy source exceeds byte budget")
                        )
                    if len(prefix) < len(PDF_SIGNATURE):
                        prefix.extend(chunk[: len(PDF_SIGNATURE) - len(prefix)])
                    hasher.update(chunk)
                    writer.write(chunk)
                writer.flush()
                os.fsync(writer.fileno())
            finally:
                metadata = os.fstat(writer.fileno())
                partial_identity = FileIdentity(
                    metadata.st_dev,
                    metadata.st_ino,
                    metadata.st_size,
                    hasher.hexdigest(),
                )
    except Exception:
        _safe_unlink_owned(temporary, source_identity=partial_identity)
        raise
    metadata = os.lstat(temporary)
    identity = FileIdentity(metadata.st_dev, metadata.st_ino, size, hasher.hexdigest())
    if bytes(prefix) != PDF_SIGNATURE:
        _safe_unlink_owned(temporary, source_identity=identity)
        raise ResearchKBError(
            Diagnostic(SCHEMA_VALIDATION_FAILED, "local-source-intake", None, "/source", "copy stream is not a PDF")
        )
    if expected_size is not None and size != expected_size:
        _safe_unlink_owned(temporary, source_identity=identity)
        raise ResearchKBError(
            Diagnostic(GROUNDING_MISMATCH, "local-source-intake", None, "/source", "copy stream size differs from its declared identity")
        )
    if expected_sha256 is not None and identity.sha256 != expected_sha256:
        _safe_unlink_owned(temporary, source_identity=identity)
        raise ResearchKBError(
            Diagnostic(GROUNDING_MISMATCH, "local-source-intake", None, "/source", "copy stream digest differs from its declared identity")
        )
    return identity


def _inspect_staged_source(path: Path) -> FileIdentity:
    try:
        metadata = os.lstat(path)
    except OSError as error:
        raise ResearchKBError(
            Diagnostic(SCHEMA_VALIDATION_FAILED, "local-source-intake", None, "/source", "operation-owned copy partial is inaccessible")
        ) from error
    if not stat.S_ISREG(metadata.st_mode) or _is_unsafe_link(path) or getattr(metadata, "st_nlink", 1) != 1:
        raise ResearchKBError(
            Diagnostic(PATH_ESCAPE, "local-source-intake", None, "/source", "operation-owned copy partial is unsafe or ambiguous")
        )
    if not 1 <= metadata.st_size <= MAX_PDF_BYTES:
        raise ResearchKBError(
            Diagnostic(SCHEMA_VALIDATION_FAILED, "local-source-intake", None, "/source", "operation-owned copy partial is outside the byte budget")
        )
    digest = file_sha256(path)
    with path.open("rb") as stream:
        signature = stream.read(len(PDF_SIGNATURE))
    if digest is None or signature != PDF_SIGNATURE:
        raise ResearchKBError(
            Diagnostic(SCHEMA_VALIDATION_FAILED, "local-source-intake", None, "/source", "operation-owned copy partial is not a valid PDF input")
        )
    return FileIdentity(metadata.st_dev, metadata.st_ino, metadata.st_size, digest)


def _verify_staged_source(path: Path, identity: FileIdentity) -> None:
    if not _identity_matches_path(identity, path) or file_sha256(path) != identity.sha256:
        raise ResearchKBError(
            Diagnostic(GROUNDING_MISMATCH, "local-source-intake", None, "/source", "staged copy changed during receipt creation")
        )


def _safe_pdf_digest(path: Path) -> str | None:
    try:
        identity = _inspect_staged_source(path)
    except ResearchKBError:
        return None
    return identity.sha256


def _publish_owned(temporary: Path, final: Path, identity: FileIdentity, job_id: str) -> FileIdentity:
    _verify_staged_source(temporary, identity)
    if temporary.parent != final.parent:
        raise _write_conflict(job_id, "inbox copy publication paths are not in the same directory")
    if os.path.lexists(final):
        raise _write_conflict(job_id, "inbox copy target appeared before publication")
    try:
        os.link(temporary, final)
    except OSError as error:
        if not (
            _IS_WINDOWS
            and getattr(error, "winerror", None) in _WINDOWS_UNSUPPORTED_HARDLINK_ERRORS
        ):
            raise _write_conflict(job_id, "inbox copy could not be published create-only") from error
        if os.path.lexists(final):
            raise _write_conflict(job_id, "inbox copy target appeared before fallback publication")
        try:
            os.rename(temporary, final)
        except OSError as rename_error:
            raise _write_conflict(job_id, "inbox copy fallback could not be published create-only") from rename_error
        return _verify_renamed_publication(temporary, final, identity, job_id)
    published = os.lstat(final)
    result = FileIdentity(published.st_dev, published.st_ino, published.st_size, identity.sha256)
    if (result.device, result.inode, result.size) != (identity.device, identity.inode, identity.size):
        raise _write_conflict(job_id, "published inbox copy does not match operation-owned partial")
    _safe_unlink_owned(temporary, source_identity=identity, expected_link_count=2)
    if os.path.lexists(temporary):
        raise _write_conflict(job_id, "operation-owned inbox copy partial could not be removed")
    if not _identity_matches_path(identity, final) or file_sha256(final) != identity.sha256:
        raise _write_conflict(job_id, "published inbox copy changed before publication completed")
    return result


def _verify_renamed_publication(
    temporary: Path,
    final: Path,
    identity: FileIdentity,
    job_id: str,
) -> FileIdentity:
    if os.path.lexists(temporary):
        raise _write_conflict(job_id, "operation-owned inbox copy partial remains after fallback publication")
    try:
        published = os.lstat(final)
    except OSError as error:
        raise _write_conflict(job_id, "fallback-published inbox copy is inaccessible") from error
    result = FileIdentity(published.st_dev, published.st_ino, published.st_size, identity.sha256)
    if (result.device, result.inode, result.size) != (identity.device, identity.inode, identity.size):
        raise _write_conflict(job_id, "fallback-published inbox copy does not match operation-owned partial")
    if not _identity_matches_path(identity, final) or file_sha256(final) != identity.sha256:
        raise _write_conflict(job_id, "fallback-published inbox copy changed before publication completed")
    return result


def _scan_candidate(
    path: Path,
    *,
    root_id: str,
    root: Path,
    workspace_id: str,
    now: datetime,
    min_stable_age_seconds: int,
) -> dict[str, Any] | None:
    if path.name.startswith(".") or path.suffix.casefold() != ".pdf" or _is_unsafe_link(path):
        return None
    try:
        before = os.lstat(path)
    except OSError:
        return None
    if not stat.S_ISREG(before.st_mode) or getattr(before, "st_nlink", 1) != 1:
        return None
    if not 1 <= before.st_size <= MAX_PDF_BYTES:
        return None
    observed_now = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
    if observed_now.timestamp() - before.st_mtime < min_stable_age_seconds:
        return None
    digest = file_sha256(path)
    try:
        after = os.lstat(path)
        with path.open("rb") as stream:
            signature = stream.read(len(PDF_SIGNATURE))
    except OSError:
        return None
    identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    if identity != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) or digest is None or signature != PDF_SIGNATURE:
        return None
    source_ref = {
        "root_id": root_id,
        "relative_path": path.relative_to(root).as_posix(),
    }
    token_payload = {
        "workspace_id": workspace_id,
        "source_ref": source_ref,
        "device": before.st_dev,
        "inode": before.st_ino,
        "size": before.st_size,
        "mtime_ns": before.st_mtime_ns,
        "sha256": digest,
    }
    candidate_handle = hashlib.sha256(
        json.dumps(token_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "candidate_token": f"inbox-v1:{candidate_handle}",
        "name": path.name,
        "source_ref": source_ref,
        "size_bytes": before.st_size,
    }


def _verify_unchanged_source(path: Path, identity: FileIdentity) -> None:
    if not _identity_matches_path(identity, path) or file_sha256(path) != identity.sha256:
        raise ResearchKBError(
            Diagnostic(GROUNDING_MISMATCH, "local-source-intake", None, "/source", "copy source changed before publication")
        )


def _identity_matches_path(
    identity: FileIdentity,
    path: Path,
    *,
    expected_link_count: int = 1,
) -> bool:
    try:
        metadata = os.lstat(path)
    except OSError:
        return False
    return (
        not _is_unsafe_link(path)
        and stat.S_ISREG(metadata.st_mode)
        and getattr(metadata, "st_nlink", 1) == expected_link_count
        and (metadata.st_dev, metadata.st_ino, metadata.st_size)
        == (identity.device, identity.inode, identity.size)
    )


def _safe_unlink_owned(
    path: Path,
    *,
    source_identity: FileIdentity | None,
    expected_link_count: int = 1,
) -> None:
    if not os.path.lexists(path):
        return
    if source_identity is None:
        return
    if (
        _identity_matches_path(
            source_identity,
            path,
            expected_link_count=expected_link_count,
        )
        and file_sha256(path) == source_identity.sha256
    ):
        path.unlink(missing_ok=True)


def _reject_unsafe_components(path: Path) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        if os.path.lexists(current) and _is_unsafe_link(current):
            raise ResearchKBError(
                Diagnostic(PATH_ESCAPE, "local-source-intake", None, "/source", "copy source traverses an unsafe link")
            )


def _is_unsafe_link(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    if callable(is_junction) and is_junction():
        return True
    try:
        attributes = getattr(os.lstat(path), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _result(
    state: dict[str, Any],
    *,
    result: str,
    persistent_writes: int,
    event_id: str | None,
) -> dict[str, Any]:
    return {
        "status": "success",
        "interface_version": "1.0",
        "result": result,
        "source_ref": dict(state["source_ref"]),
        "source_asset_id": state["source_asset_id"],
        "source_asset_state_id": state["source_asset_state_id"],
        "persistent_writes": persistent_writes,
        "event_id": event_id,
    }


def _write_conflict(record_id: str, message: str) -> ResearchKBError:
    return ResearchKBError(
        Diagnostic(WRITE_CONFLICT, "local-source-intake", record_id, "", message)
    )


__all__ = ["LocalSourceIntakeService", "MAX_PDF_BYTES", "MAX_SCAN_ENTRIES"]
