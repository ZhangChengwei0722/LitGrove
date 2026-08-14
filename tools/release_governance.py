"""Deterministic release-candidate and publication identity checks.

This module deliberately uses only the Python standard library.  It creates
operation artifacts, verifies their byte identities, and validates a future
publication tuple; it never performs a release or publication operation.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import csv
import hashlib
import io
import json
import os
import platform
import re
import site
import subprocess
import sys
import tarfile
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from email.parser import Parser
from pathlib import Path
from typing import Any


ARTIFACT_SCHEMA_VERSION = "ppwb.g1.artifact_manifest.v1"
INSTALLED_SCHEMA_VERSION = "ppwb.g1.installed_manifest.v1"
MEMBER_SCHEMA_VERSION = "ppwb.g1.archive_member_manifest.v1"
PUBLICATION_SCHEMA_VERSION = "ppwb.g1.publication_activation.v1"
PUBLICATION_AUTHORITY_SCHEMA_VERSION = "ppwb.g1.publication_authority.v1"
OPERATION_SCHEMA_VERSION = "ppwb.g1.operation_manifest.v1"
PROVENANCE_SCHEMA_VERSION = "ppwb.g1.provenance_inputs.v1"
MAX_SAFE_INTEGER = (2**53) - 1
HISTORY_EXPECTATION_SCHEMA_VERSION = "ppwb.g1.history_expectations.v1"
HISTORY_FINDING_TYPES = {"historical_boundary", "credential", "private_path", "pdf", "binary"}
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
GIT_OBJECT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
RUN_ID_PATTERN = re.compile(r"^[1-9][0-9]*$")
VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:\.dev[0-9]+)?$")
TAG_PATTERN = re.compile(r"^v[0-9]+\.[0-9]+\.[0-9]+$")


class GovernanceInputError(ValueError):
    """Raised when a manifest cannot be materialized from valid inputs."""


@dataclass(frozen=True)
class Finding:
    code: str
    path: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "path": self.path, "message": self.message}


@dataclass(frozen=True)
class VerificationResult:
    ok: bool
    errors: tuple[Finding, ...] = ()
    evidence: Mapping[str, Any] | None = None

    @property
    def codes(self) -> tuple[str, ...]:
        return tuple(finding.code for finding in self.errors)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "errors": [finding.to_dict() for finding in self.errors],
            "evidence": dict(self.evidence or {}),
        }

    def __bool__(self) -> bool:
        return self.ok


def _finding(errors: list[Finding], code: str, path: str, message: str) -> None:
    errors.append(Finding(code, path, message))


def _finish(errors: list[Finding], evidence: Mapping[str, Any] | None = None) -> VerificationResult:
    unique = {(finding.code, finding.path, finding.message): finding for finding in errors}
    ordered = tuple(unique[key] for key in sorted(unique))
    return VerificationResult(not ordered, ordered, evidence or {})


def _validate_unicode(value: str, path: str) -> str:
    try:
        value.encode("utf-16-be")
    except UnicodeEncodeError as error:
        raise TypeError(f"unpaired surrogate at {path}") from error
    return value


def _canonical_value(value: Any, path: str = "$") -> Any:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        if not -MAX_SAFE_INTEGER <= value <= MAX_SAFE_INTEGER:
            raise TypeError(f"integer outside the safe domain at {path}")
        return value
    if isinstance(value, float):
        raise TypeError(f"floats are not permitted at {path}")
    if isinstance(value, str):
        return _validate_unicode(value, path)
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"non-string object key at {path}")
            _validate_unicode(key, f"{path}.<key>")
            result[key] = _canonical_value(item, f"{path}.{key!r}")
        return result
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item, f"{path}[{index}]") for index, item in enumerate(value)]
    raise TypeError(f"unsupported JSON value at {path}: {type(value).__name__}")


def _utf16_sort_key(value: str) -> bytes:
    return value.encode("utf-16-be")


def _escape_json_string(value: str) -> str:
    escapes = {
        "\b": "\\b",
        "\f": "\\f",
        "\n": "\\n",
        "\r": "\\r",
        "\t": "\\t",
        '"': '\\"',
        "\\": "\\\\",
    }
    output = ['"']
    for character in value:
        escaped = escapes.get(character)
        if escaped is not None:
            output.append(escaped)
        elif ord(character) < 0x20:
            output.append(f"\\u{ord(character):04x}")
        else:
            output.append(character)
    output.append('"')
    return "".join(output)


def _render_canonical_value(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return _escape_json_string(value)
    if isinstance(value, list):
        return "[" + ",".join(_render_canonical_value(item) for item in value) + "]"
    if isinstance(value, Mapping):
        items = sorted(value.items(), key=lambda item: _utf16_sort_key(item[0]))
        return "{" + ",".join(
            _escape_json_string(key) + ":" + _render_canonical_value(item)
            for key, item in items
        ) + "}"
    raise TypeError(f"unsupported normalized JSON value: {type(value).__name__}")


def canonical_json(value: Any) -> str:
    """Return restricted-domain RFC 8785-compatible canonical JSON."""

    return _render_canonical_value(_canonical_value(value))


def canonical_json_bytes(value: Any) -> bytes:
    return canonical_json(value).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_digest(value: Any) -> str:
    return sha256_bytes(canonical_json_bytes(value))


def _reject_json_constant(value: str) -> None:
    raise GovernanceInputError(f"non-finite JSON constant is forbidden: {value}")


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise GovernanceInputError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _load_json_bytes(raw: bytes) -> Any:
    return json.loads(
        raw.decode("utf-8"),
        object_pairs_hook=_strict_json_object,
        parse_constant=_reject_json_constant,
    )


def _check_canonical_input(value: Any, path: str, errors: list[Finding]) -> bool:
    try:
        _canonical_value(value, path)
    except (TypeError, ValueError) as error:
        _finding(errors, "invalid_canonical_value", path, str(error))
        return False
    return True


def verify_canonical_value(value: Any) -> VerificationResult:
    errors: list[Finding] = []
    if _check_canonical_input(value, "$", errors):
        return _finish(errors, {"canonical_json": canonical_json(value)})
    return _finish(errors)


def _require_mapping(value: Any, path: str, errors: list[Finding]) -> Mapping[str, Any] | None:
    if not isinstance(value, Mapping):
        _finding(errors, "invalid_type", path, "expected a JSON object")
        return None
    return value


def _require_sha256(value: Any, path: str, errors: list[Finding]) -> bool:
    if not isinstance(value, str) or not SHA256_PATTERN.fullmatch(value):
        _finding(errors, "invalid_sha256", path, "expected a lowercase 64-character SHA-256 digest")
        return False
    return True


def _require_commit(value: Any, path: str, errors: list[Finding]) -> bool:
    if not isinstance(value, str) or not COMMIT_PATTERN.fullmatch(value):
        _finding(errors, "invalid_commit", path, "expected a lowercase full Git commit SHA")
        return False
    return True


def _require_run_id(value: Any, path: str, errors: list[Finding]) -> bool:
    normalized = str(value) if isinstance(value, int) and not isinstance(value, bool) else value
    if not isinstance(normalized, str) or not RUN_ID_PATTERN.fullmatch(normalized):
        _finding(errors, "invalid_run_id", path, "expected a positive decimal workflow run id")
        return False
    return True


def _require_version(value: Any, path: str, errors: list[Finding]) -> bool:
    if not isinstance(value, str) or not VERSION_PATTERN.fullmatch(value):
        _finding(errors, "invalid_version", path, "expected a deterministic package version")
        return False
    return True


def _require_safe_relative(value: Any, path: str, errors: list[Finding]) -> bool:
    if not isinstance(value, str) or not value or "\\" in value or value.startswith("/"):
        _finding(errors, "unsafe_relative_path", path, "expected a relative POSIX path")
        return False
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts) or ":" in parts[0]:
        _finding(errors, "unsafe_relative_path", path, "path traversal or an absolute path is not allowed")
        return False
    return True


def _as_run_id(value: Any) -> str:
    return str(value) if isinstance(value, int) and not isinstance(value, bool) else value


def _require_run_attempt(value: Any, path: str, errors: list[Finding]) -> bool:
    normalized = _as_run_id(value)
    if not isinstance(normalized, str) or not RUN_ID_PATTERN.fullmatch(normalized):
        _finding(errors, "invalid_run_attempt", path, "expected a positive decimal workflow run attempt")
        return False
    return True


def _candidate_from(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    candidate = value.get("candidate")
    return candidate if isinstance(candidate, Mapping) else value


def _expected_artifact_digests(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    sources: list[Mapping[str, Any]] = [value]
    for field in ("publication", "activation", "authority"):
        nested = value.get(field)
        if isinstance(nested, Mapping):
            sources.append(nested)
    for source in sources:
        direct = source.get("artifact_digests") or source.get("accepted_artifact_digests")
        if isinstance(direct, Mapping):
            return {str(key): item for key, item in direct.items() if isinstance(item, str)}
        artifacts = source.get("artifacts")
        if not isinstance(artifacts, Sequence) or isinstance(artifacts, (str, bytes)):
            continue
        result: dict[str, str] = {}
        for item in artifacts:
            if isinstance(item, Mapping) and isinstance(item.get("filename"), str) and isinstance(item.get("sha256"), str):
                result[item["filename"]] = item["sha256"]
        if result:
            return result
    return {}


def _compare_candidate(
    actual: Mapping[str, Any], expected: Any, errors: list[Finding], path: str = "candidate"
) -> None:
    expected_candidate = _candidate_from(expected)
    for field, code in (
        ("source_commit", "wrong_commit"),
        ("workflow_run_id", "wrong_run"),
        ("workflow_run_attempt", "wrong_run_attempt"),
        ("version", "version_mismatch"),
        ("repository", "wrong_repository"),
        ("artifact_name", "wrong_artifact_name"),
    ):
        if field not in expected_candidate:
            continue
        if field in {"workflow_run_id", "workflow_run_attempt"}:
            actual_value = _as_run_id(actual.get(field))
            expected_value = _as_run_id(expected_candidate.get(field))
        else:
            actual_value = actual.get(field)
            expected_value = expected_candidate.get(field)
        if actual_value != expected_value:
            _finding(errors, code, f"{path}.{field}", "candidate identity does not match the accepted identity")


def _member_manifest_digest(members: Sequence[Mapping[str, Any]]) -> str:
    return canonical_digest({"members": list(members)})


def _member_manifest_from_bytes(data: bytes, filename: str) -> dict[str, Any]:
    members: list[dict[str, Any]] = []
    seen: set[str] = set()
    zip_stream = io.BytesIO(data)
    if zipfile.is_zipfile(zip_stream):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                for info in archive.infolist():
                    if info.is_dir():
                        continue
                    name = info.filename
                    if not _safe_member_path(name):
                        raise GovernanceInputError(f"unsafe archive member: {name}")
                    if name in seen:
                        raise GovernanceInputError(f"duplicate archive member: {name}")
                    seen.add(name)
                    content = archive.read(info)
                    members.append({"path": name, "size": len(content), "sha256": sha256_bytes(content)})
        except (OSError, zipfile.BadZipFile) as error:
            raise GovernanceInputError(f"invalid zip archive {filename}: {error}") from error
    else:
        try:
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as archive:
                for info in archive.getmembers():
                    if info.isdir():
                        continue
                    if not info.isfile():
                        raise GovernanceInputError(f"non-regular tar member: {info.name}")
                    name = info.name
                    if not _safe_member_path(name):
                        raise GovernanceInputError(f"unsafe archive member: {name}")
                    if name in seen:
                        raise GovernanceInputError(f"duplicate archive member: {name}")
                    source = archive.extractfile(info)
                    if source is None:
                        raise GovernanceInputError(f"unreadable tar member: {name}")
                    content = source.read()
                    seen.add(name)
                    members.append({"path": name, "size": len(content), "sha256": sha256_bytes(content)})
        except (OSError, tarfile.TarError) as error:
            raise GovernanceInputError(f"invalid tar archive {filename}: {error}") from error
    members.sort(key=lambda item: item["path"])
    return {
        "schema_version": MEMBER_SCHEMA_VERSION,
        "members": members,
        "sha256": _member_manifest_digest(members),
    }


def _safe_member_path(value: Any) -> bool:
    if not isinstance(value, str) or not value or "\\" in value or value.startswith("/"):
        return False
    parts = value.split("/")
    return not any(part in {"", ".", ".."} for part in parts) and ":" not in parts[0]


def archive_member_manifest(archive: Path) -> dict[str, Any]:
    return _member_manifest_from_bytes(archive.read_bytes(), archive.name)


def _validate_member_manifest(value: Any, path: str, errors: list[Finding]) -> list[dict[str, Any]]:
    manifest = _require_mapping(value, path, errors)
    if manifest is None:
        return []
    if manifest.get("schema_version") != MEMBER_SCHEMA_VERSION:
        _finding(errors, "unsupported_schema", f"{path}.schema_version", "archive member manifest schema is unsupported")
    members_value = manifest.get("members")
    if not isinstance(members_value, list):
        _finding(errors, "invalid_type", f"{path}.members", "expected an array of archive members")
        return []
    members: list[dict[str, Any]] = []
    paths: list[str] = []
    for index, item in enumerate(members_value):
        item_path = f"{path}.members[{index}]"
        mapping = _require_mapping(item, item_path, errors)
        if mapping is None:
            continue
        if set(mapping) != {"path", "size", "sha256"}:
            _finding(errors, "noncanonical_fields", item_path, "member fields are not canonical")
        member = {"path": mapping.get("path"), "size": mapping.get("size"), "sha256": mapping.get("sha256")}
        if _require_safe_relative(member["path"], f"{item_path}.path", errors):
            paths.append(member["path"])
        if not isinstance(member["size"], int) or isinstance(member["size"], bool) or member["size"] < 0:
            _finding(errors, "invalid_size", f"{item_path}.size", "member size must be a non-negative integer")
        _require_sha256(member["sha256"], f"{item_path}.sha256", errors)
        members.append(member)
    if paths != sorted(paths):
        _finding(errors, "noncanonical_order", f"{path}.members", "archive members must be sorted by path")
    if len(paths) != len(set(paths)):
        _finding(errors, "duplicate_member", f"{path}.members", "archive members must be unique")
    if _require_sha256(manifest.get("sha256"), f"{path}.sha256", errors):
        if manifest.get("sha256") != _member_manifest_digest(members):
            _finding(errors, "member_manifest_digest_mismatch", f"{path}.sha256", "member digest does not match canonical members")
    return members


def verify_artifact_manifest(
    manifest: Mapping[str, Any],
    *,
    expected: Mapping[str, Any] | None = None,
    artifact_paths: Path | Mapping[str, Path] | None = None,
) -> VerificationResult:
    errors: list[Finding] = []
    manifest_canonical = _check_canonical_input(manifest, "manifest", errors)
    expected_canonical = True
    if expected is not None:
        expected_canonical = _check_canonical_input(expected, "expected", errors)
    if not manifest_canonical or not expected_canonical:
        return _finish(errors)
    root = _require_mapping(manifest, "manifest", errors)
    if root is None:
        return _finish(errors)
    if root.get("schema_version") != ARTIFACT_SCHEMA_VERSION:
        _finding(errors, "unsupported_schema", "manifest.schema_version", "artifact manifest schema is unsupported")
    candidate = _require_mapping(root.get("candidate"), "manifest.candidate", errors)
    if candidate is None:
        candidate = {}
    for field in ("repository", "artifact_name"):
        if not isinstance(candidate.get(field), str) or not candidate.get(field):
            _finding(errors, "missing_identity", f"manifest.candidate.{field}", "required candidate identity is missing")
    _require_commit(candidate.get("source_commit"), "manifest.candidate.source_commit", errors)
    _require_run_id(candidate.get("workflow_run_id"), "manifest.candidate.workflow_run_id", errors)
    _require_run_attempt(candidate.get("workflow_run_attempt"), "manifest.candidate.workflow_run_attempt", errors)
    _require_version(candidate.get("version"), "manifest.candidate.version", errors)
    expected_artifact_name = (
        f"release-candidate-{_as_run_id(candidate.get('workflow_run_id'))}-"
        f"{_as_run_id(candidate.get('workflow_run_attempt'))}"
    )
    if candidate.get("artifact_name") != expected_artifact_name:
        _finding(errors, "invalid_artifact_name", "manifest.candidate.artifact_name", "artifact name must bind run id and run attempt")
    _compare_candidate(candidate, expected, errors, "manifest.candidate")

    build = _require_mapping(root.get("build_once"), "manifest.build_once", errors)
    if build is not None:
        if build.get("attempts") != 1 or build.get("rebuild") is not False:
            _finding(errors, "rebuild_attempt", "manifest.build_once", "a candidate must be built exactly once")
        if build.get("source_commit") != candidate.get("source_commit"):
            _finding(errors, "wrong_commit", "manifest.build_once.source_commit", "build source commit differs from candidate commit")
    cache = _require_mapping(root.get("cache_identity"), "manifest.cache_identity", errors)
    if cache is not None:
        expected_key = ":".join(
            (
                str(candidate.get("repository", "")),
                str(candidate.get("source_commit", "")),
                str(_as_run_id(candidate.get("workflow_run_id", ""))),
                str(_as_run_id(candidate.get("workflow_run_attempt", ""))),
                str(candidate.get("version", "")),
            )
        )
        if cache.get("candidate_key") != expected_key:
            _finding(errors, "cross_candidate_cache", "manifest.cache_identity.candidate_key", "cache identity is not bound to this candidate")
        if cache.get("source_commit") != candidate.get("source_commit"):
            _finding(errors, "cross_candidate_cache", "manifest.cache_identity.source_commit", "cache source commit differs from candidate")
        if _as_run_id(cache.get("workflow_run_id")) != _as_run_id(candidate.get("workflow_run_id")):
            _finding(errors, "cross_candidate_cache", "manifest.cache_identity.workflow_run_id", "cache run differs from candidate")
        if _as_run_id(cache.get("workflow_run_attempt")) != _as_run_id(candidate.get("workflow_run_attempt")):
            _finding(errors, "cross_candidate_cache", "manifest.cache_identity.workflow_run_attempt", "cache run attempt differs from candidate")
        expected_input_digest = canonical_digest(
            {
                "repository": candidate.get("repository"),
                "source_commit": candidate.get("source_commit"),
                "workflow_run_id": _as_run_id(candidate.get("workflow_run_id")),
                "workflow_run_attempt": _as_run_id(candidate.get("workflow_run_attempt")),
                "version": candidate.get("version"),
            }
        )
        if cache.get("input_digest") != expected_input_digest:
            _finding(errors, "cross_candidate_cache", "manifest.cache_identity.input_digest", "cache input digest is not candidate-bound")
        if cache.get("scope") != "candidate-scoped":
            _finding(errors, "unsafe_cache_scope", "manifest.cache_identity.scope", "cache must be candidate-scoped")

    artifacts_value = root.get("artifacts")
    if not isinstance(artifacts_value, list):
        _finding(errors, "invalid_type", "manifest.artifacts", "expected an artifact array")
        artifacts_value = []
    artifacts: list[Mapping[str, Any]] = []
    filenames: list[str] = []
    kinds: set[str] = set()
    kind_counts: dict[str, int] = {"wheel": 0, "sdist": 0}
    for index, item in enumerate(artifacts_value):
        item_path = f"manifest.artifacts[{index}]"
        artifact = _require_mapping(item, item_path, errors)
        if artifact is None:
            continue
        artifacts.append(artifact)
        filename = artifact.get("filename")
        if _require_safe_relative(filename, f"{item_path}.filename", errors):
            filenames.append(filename)
        kind = artifact.get("kind")
        if kind not in {"wheel", "sdist"}:
            _finding(errors, "invalid_artifact_kind", f"{item_path}.kind", "artifact kind must be wheel or sdist")
        else:
            kinds.add(kind)
            kind_counts[kind] += 1
        if artifact.get("version") != candidate.get("version"):
            _finding(errors, "version_mismatch", f"{item_path}.version", "artifact version differs from candidate version")
        _require_sha256(artifact.get("sha256"), f"{item_path}.sha256", errors)
        if not isinstance(artifact.get("size"), int) or isinstance(artifact.get("size"), bool) or artifact.get("size", -1) < 0:
            _finding(errors, "invalid_size", f"{item_path}.size", "artifact size must be a non-negative integer")
        _validate_member_manifest(artifact.get("member_manifest"), f"{item_path}.member_manifest", errors)
    if filenames != sorted(filenames):
        _finding(errors, "noncanonical_order", "manifest.artifacts", "artifacts must be sorted by filename")
    if len(filenames) != len(set(filenames)):
        _finding(errors, "duplicate_artifact", "manifest.artifacts", "artifact filenames must be unique")
    if kinds != {"wheel", "sdist"} or any(count != 1 for count in kind_counts.values()):
        _finding(errors, "incomplete_artifacts", "manifest.artifacts", "release candidate must contain one wheel and one sdist")

    expected_digests = _expected_artifact_digests(expected)
    actual_digests = {str(item.get("filename")): item.get("sha256") for item in artifacts}
    if expected_digests and set(actual_digests) != set(expected_digests):
        _finding(errors, "artifact_set_mismatch", "manifest.artifacts", "artifact digest keys do not match the accepted key set")
    for artifact in artifacts:
        filename = artifact.get("filename")
        if filename in expected_digests and artifact.get("sha256") != expected_digests[filename]:
            _finding(errors, "wrong_artifact_digest", f"manifest.artifacts[{filename}].sha256", "artifact digest differs from accepted bytes")
            if artifact.get("version") == _candidate_from(expected).get("version"):
                _finding(errors, "same_version_substituted_bytes", f"manifest.artifacts[{filename}]", "same-version artifact bytes were substituted")

    if artifact_paths is not None:
        if isinstance(artifact_paths, Path) and not artifact_paths.is_dir():
            _finding(errors, "artifact_directory_missing", "artifact_dir", "artifact directory is missing")
        elif isinstance(artifact_paths, Path):
            expected_filenames = set(filenames)
            for entry in artifact_paths.iterdir():
                if entry.is_symlink() or entry.name not in expected_filenames:
                    _finding(errors, "unexpected_artifact_file", f"artifact_dir.{entry.name}", "artifact directory contains an unexpected file or symlink")
        for artifact in artifacts:
            filename = artifact.get("filename")
            if isinstance(artifact_paths, Mapping):
                path = artifact_paths.get(filename)
            else:
                path = artifact_paths / filename if isinstance(filename, str) else None
            if not isinstance(path, Path) or not path.is_file() or path.is_symlink():
                _finding(errors, "artifact_missing", f"artifacts.{filename}", "artifact file is missing or not a regular file")
                continue
            actual_digest = sha256_file(path)
            if actual_digest != artifact.get("sha256"):
                _finding(errors, "artifact_bytes_mismatch", f"artifacts.{filename}.sha256", "artifact bytes do not match manifest")
            if path.stat().st_size != artifact.get("size"):
                _finding(errors, "artifact_size_mismatch", f"artifacts.{filename}.size", "artifact size does not match manifest")
            try:
                actual_members = archive_member_manifest(path)
            except (OSError, GovernanceInputError) as error:
                _finding(errors, "invalid_artifact_archive", f"artifacts.{filename}", str(error))
            else:
                if actual_members != artifact.get("member_manifest"):
                    _finding(errors, "artifact_member_mismatch", f"artifacts.{filename}.member_manifest", "archive members do not match manifest")
    evidence = {
        "candidate": dict(candidate),
        "artifact_digests": {str(item.get("filename")): item.get("sha256") for item in artifacts},
        "artifact_count": len(artifacts),
        "build_attempts": build.get("attempts") if isinstance(build, Mapping) else None,
    }
    return _finish(errors, evidence)


def build_artifact_manifest(
    artifact_dir: Path,
    *,
    repository: str,
    source_commit: str,
    workflow_run_id: str | int,
    workflow_run_attempt: str | int,
    version: str,
    artifact_name: str | None = None,
) -> dict[str, Any]:
    files = sorted(path for path in artifact_dir.iterdir() if path.is_file() and not path.is_symlink())
    artifacts: list[dict[str, Any]] = []
    for path in files:
        if path.suffix == ".whl":
            kind = "wheel"
        elif path.name.endswith(".tar.gz"):
            kind = "sdist"
        else:
            continue
        artifacts.append(
            {
                "kind": kind,
                "filename": path.name,
                "version": version,
                "size": path.stat().st_size,
                "sha256": sha256_file(path),
                "member_manifest": archive_member_manifest(path),
            }
        )
    normalized_run = _as_run_id(workflow_run_id)
    normalized_attempt = _as_run_id(workflow_run_attempt)
    candidate = {
        "repository": repository,
        "source_commit": source_commit,
        "workflow_run_id": normalized_run,
        "workflow_run_attempt": normalized_attempt,
        "version": version,
        "artifact_name": artifact_name or f"release-candidate-{normalized_run}-{normalized_attempt}",
    }
    cache_input = {
        "repository": repository,
        "source_commit": source_commit,
        "workflow_run_id": normalized_run,
        "workflow_run_attempt": normalized_attempt,
        "version": version,
    }
    return {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "candidate": candidate,
        "build_once": {"attempts": 1, "rebuild": False, "source_commit": source_commit},
        "cache_identity": {
            "scope": "candidate-scoped",
            "candidate_key": f"{repository}:{source_commit}:{normalized_run}:{normalized_attempt}:{version}",
            "source_commit": source_commit,
            "workflow_run_id": normalized_run,
            "workflow_run_attempt": normalized_attempt,
            "input_digest": canonical_digest(cache_input),
        },
        "artifacts": sorted(artifacts, key=lambda item: item["filename"]),
    }


def _actual_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*"):
        if path.is_symlink():
            raise GovernanceInputError(f"symlink in installed payload: {path}")
        if path.is_file():
            files.append(path)
    return files


def _relative_path(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _record_entries(root: Path, record_path: Path) -> list[dict[str, Any]]:
    record_relative = _relative_path(root, record_path)
    result: list[dict[str, Any]] = []
    with record_path.open("r", encoding="utf-8", newline="") as stream:
        rows = list(csv.reader(stream))
    seen: set[str] = set()
    for row in rows:
        if len(row) != 3:
            raise GovernanceInputError(f"invalid RECORD row in {record_relative}")
        relative, digest, size = row
        if not _safe_member_path(relative) or relative in seen:
            raise GovernanceInputError(f"invalid or duplicate RECORD path: {relative}")
        seen.add(relative)
        target = root.joinpath(*relative.split("/"))
        if not target.is_file() or target.is_symlink():
            raise GovernanceInputError(f"RECORD points to a missing or non-regular file: {relative}")
        actual_size = target.stat().st_size
        if size == "":
            if relative != record_relative:
                raise GovernanceInputError(f"only RECORD itself may omit a size: {relative}")
        elif size != str(actual_size):
            raise GovernanceInputError(f"RECORD size mismatch: {relative}")
        actual_digest = sha256_file(target)
        if digest:
            if not digest.startswith("sha256="):
                raise GovernanceInputError(f"RECORD uses an unsupported hash: {relative}")
            encoded = digest.removeprefix("sha256=")
            try:
                decoded = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
            except (ValueError, binascii.Error) as error:
                raise GovernanceInputError(f"invalid RECORD digest: {relative}") from error
            if decoded.hex() != actual_digest:
                raise GovernanceInputError(f"RECORD digest mismatch: {relative}")
        elif relative != record_relative:
            raise GovernanceInputError(f"only RECORD itself may omit a digest: {relative}")
        result.append({"path": relative, "size": actual_size, "sha256": actual_digest})
    result.sort(key=lambda item: item["path"])
    return result


def _capability_identity(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "interface_version": value.get("interface_version"),
        "core": value.get("core"),
    }


def _default_runtime_identity(user_site_disabled: bool, isolated_interpreter: bool) -> dict[str, Any]:
    return {
        "interpreter_path": str(Path(sys.executable).resolve()),
        "implementation": platform.python_implementation(),
        "cpython_version": platform.python_version(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "isolated_interpreter": isolated_interpreter,
        "user_site_disabled": user_site_disabled,
        "user_site_evidence": {
            "python_no_user_site": user_site_disabled,
            "site_enable_user_site": user_site_disabled,
        },
        "site_packages_class": "install-root" if isolated_interpreter else "unknown",
    }


def build_installed_manifest(
    installed_root: Path,
    *,
    candidate: Mapping[str, Any],
    capability_json: Path,
    artifact: Path | None = None,
    source_tree_import: bool = False,
    module_path: Path | None = None,
    user_site_disabled: bool = True,
    isolated_interpreter: bool = True,
    runtime_identity: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    root = installed_root.resolve()
    dist_infos = [path for path in root.rglob("*.dist-info") if path.is_dir() and not path.is_symlink()]
    if len(dist_infos) != 1:
        raise GovernanceInputError("installed root must contain exactly one regular .dist-info directory")
    dist_info = dist_infos[0]
    metadata_path = dist_info / "METADATA"
    record_path = dist_info / "RECORD"
    if not metadata_path.is_file() or not record_path.is_file():
        raise GovernanceInputError("installed distribution must contain METADATA and RECORD")
    headers = Parser().parsestr(metadata_path.read_text(encoding="utf-8"))
    name = headers.get("Name")
    version = headers.get("Version")
    if not name or not version:
        raise GovernanceInputError("installed METADATA lacks Name or Version")
    if version != candidate.get("version"):
        raise GovernanceInputError("installed distribution version differs from candidate")
    if module_path is not None:
        try:
            module_path.resolve().relative_to(root)
        except ValueError:
            source_tree_import = True
    entries = _record_entries(root, record_path)
    actual_files = sorted(_relative_path(root, path) for path in _actual_files(root))
    record_files = sorted(item["path"] for item in entries)
    if actual_files != record_files:
        raise GovernanceInputError("installed files and RECORD entries are not identical")
    dist_prefix = f"{dist_info.name}/"
    payload_files = [item for item in entries if not item["path"].startswith(dist_prefix)]
    requires_dist = sorted(headers.get_all("Requires-Dist") or [])
    requires_dist_digest = canonical_digest({"requires_dist": requires_dist})
    measured_runtime = dict(
        runtime_identity
        if runtime_identity is not None
        else _default_runtime_identity(user_site_disabled, isolated_interpreter)
    )
    _canonical_value(measured_runtime, "runtime")
    measured_runtime["requires_dist_sha256"] = requires_dist_digest
    measured_runtime["source_tree_import"] = source_tree_import
    capability_raw = capability_json.read_bytes()
    capability_value = _load_json_bytes(capability_raw)
    if not isinstance(capability_value, Mapping):
        raise GovernanceInputError("capability output must be a JSON object")
    result: dict[str, Any] = {
        "schema_version": INSTALLED_SCHEMA_VERSION,
        "candidate": dict(candidate),
        "distribution": {
            "name": name,
            "version": version,
            "dist_info_dir": dist_info.name,
            "metadata_path": _relative_path(root, metadata_path),
            "metadata_sha256": sha256_file(metadata_path),
            "record_path": _relative_path(root, record_path),
            "record_sha256": sha256_file(record_path),
            "record_entries": entries,
            "requires_dist": requires_dist,
            "requires_dist_sha256": requires_dist_digest,
        },
        "payload": {
            "files": payload_files,
            "sha256": canonical_digest({"files": payload_files}),
        },
        "capability": {
            "path": capability_json.name,
            "sha256": sha256_bytes(capability_raw),
            "canonical_sha256": canonical_digest(capability_value),
            "identity": _capability_identity(capability_value),
        },
        "runtime": measured_runtime,
    }
    if artifact is not None:
        result["artifact"] = {
            "filename": artifact.name,
            "sha256": sha256_file(artifact),
            "size": artifact.stat().st_size,
            "kind": "wheel" if artifact.suffix == ".whl" else "sdist",
        }
    return result


def _validate_file_entries(value: Any, path: str, errors: list[Finding]) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        _finding(errors, "invalid_type", path, "expected sorted file entries")
        return []
    result: list[dict[str, Any]] = []
    paths: list[str] = []
    for index, item in enumerate(value):
        item_path = f"{path}[{index}]"
        mapping = _require_mapping(item, item_path, errors)
        if mapping is None:
            continue
        entry = {"path": mapping.get("path"), "size": mapping.get("size"), "sha256": mapping.get("sha256")}
        if _require_safe_relative(entry["path"], f"{item_path}.path", errors):
            paths.append(entry["path"])
        if not isinstance(entry["size"], int) or isinstance(entry["size"], bool) or entry["size"] < 0:
            _finding(errors, "invalid_size", f"{item_path}.size", "file size must be a non-negative integer")
        _require_sha256(entry["sha256"], f"{item_path}.sha256", errors)
        result.append(entry)
    if paths != sorted(paths):
        _finding(errors, "noncanonical_order", path, "file entries must be sorted by path")
    if len(paths) != len(set(paths)):
        _finding(errors, "duplicate_file", path, "file entries must be unique")
    return result


def _compare_installed_artifact(
    installed: Mapping[str, Any], expected: Any, errors: list[Finding]
) -> None:
    artifact = installed.get("artifact")
    if not isinstance(artifact, Mapping):
        return
    expected_digests = _expected_artifact_digests(expected)
    filename = artifact.get("filename")
    expected_digest = expected_digests.get(filename)
    if expected_digest is not None and artifact.get("sha256") != expected_digest:
        _finding(errors, "same_version_substituted_bytes", "manifest.artifact.sha256", "installed artifact bytes differ from accepted same-version bytes")


def verify_installed_manifest(
    manifest: Mapping[str, Any],
    *,
    expected: Mapping[str, Any] | None = None,
    installed_root: Path | None = None,
    capability_output: Path | None = None,
    runtime_identity: Mapping[str, Any] | None = None,
) -> VerificationResult:
    errors: list[Finding] = []
    manifest_canonical = _check_canonical_input(manifest, "manifest", errors)
    expected_canonical = True
    if expected is not None:
        expected_canonical = _check_canonical_input(expected, "expected", errors)
    runtime_canonical = True
    if runtime_identity is not None:
        runtime_canonical = _check_canonical_input(runtime_identity, "runtime_identity", errors)
    if not manifest_canonical or not expected_canonical or not runtime_canonical:
        return _finish(errors)
    root = _require_mapping(manifest, "manifest", errors)
    if root is None:
        return _finish(errors)
    if root.get("schema_version") != INSTALLED_SCHEMA_VERSION:
        _finding(errors, "unsupported_schema", "manifest.schema_version", "installed manifest schema is unsupported")
    candidate = _require_mapping(root.get("candidate"), "manifest.candidate", errors)
    if candidate is None:
        candidate = {}
    _require_commit(candidate.get("source_commit"), "manifest.candidate.source_commit", errors)
    _require_run_id(candidate.get("workflow_run_id"), "manifest.candidate.workflow_run_id", errors)
    _require_run_attempt(candidate.get("workflow_run_attempt"), "manifest.candidate.workflow_run_attempt", errors)
    _require_version(candidate.get("version"), "manifest.candidate.version", errors)
    _compare_candidate(candidate, expected, errors, "manifest.candidate")
    _compare_installed_artifact(root, expected, errors)

    distribution = _require_mapping(root.get("distribution"), "manifest.distribution", errors)
    if distribution is None:
        distribution = {}
    for field in ("name", "version", "dist_info_dir", "metadata_path", "record_path"):
        if not isinstance(distribution.get(field), str) or not distribution.get(field):
            _finding(errors, "missing_identity", f"manifest.distribution.{field}", "required installed identity is missing")
    for field in ("dist_info_dir", "metadata_path", "record_path"):
        if isinstance(distribution.get(field), str):
            _require_safe_relative(distribution[field], f"manifest.distribution.{field}", errors)
    if distribution.get("version") != candidate.get("version"):
        _finding(errors, "version_mismatch", "manifest.distribution.version", "installed version differs from candidate")
    for field in ("metadata_sha256", "record_sha256"):
        _require_sha256(distribution.get(field), f"manifest.distribution.{field}", errors)
    requires_dist = distribution.get("requires_dist")
    if not isinstance(requires_dist, list) or any(not isinstance(item, str) for item in requires_dist):
        _finding(errors, "invalid_requires_dist", "manifest.distribution.requires_dist", "Requires-Dist must be a string array")
        requires_dist = []
    if requires_dist != sorted(requires_dist):
        _finding(errors, "noncanonical_order", "manifest.distribution.requires_dist", "Requires-Dist entries must be sorted")
    _require_sha256(distribution.get("requires_dist_sha256"), "manifest.distribution.requires_dist_sha256", errors)
    if distribution.get("requires_dist_sha256") != canonical_digest({"requires_dist": requires_dist}):
        _finding(errors, "requires_dist_digest_mismatch", "manifest.distribution.requires_dist_sha256", "Requires-Dist digest does not match metadata identities")
    record_entries = _validate_file_entries(distribution.get("record_entries"), "manifest.distribution.record_entries", errors)
    payload = _require_mapping(root.get("payload"), "manifest.payload", errors)
    if payload is None:
        payload = {}
    payload_files = _validate_file_entries(payload.get("files"), "manifest.payload.files", errors)
    if _require_sha256(payload.get("sha256"), "manifest.payload.sha256", errors):
        if payload.get("sha256") != canonical_digest({"files": payload_files}):
            _finding(errors, "payload_digest_mismatch", "manifest.payload.sha256", "payload digest does not match canonical file list")
    capability = _require_mapping(root.get("capability"), "manifest.capability", errors)
    if capability is None:
        capability = {}
    _require_sha256(capability.get("sha256"), "manifest.capability.sha256", errors)
    if _require_sha256(capability.get("canonical_sha256"), "manifest.capability.canonical_sha256", errors) is False:
        pass
    if not isinstance(capability.get("identity"), Mapping):
        _finding(errors, "missing_identity", "manifest.capability.identity", "capability identity is required")
    runtime = _require_mapping(root.get("runtime"), "manifest.runtime", errors)
    if runtime is None:
        runtime = {}
    if runtime.get("isolated_interpreter") is not True:
        _finding(errors, "nonisolated_interpreter", "manifest.runtime.isolated_interpreter", "installed smoke must use an isolated interpreter")
    if runtime.get("user_site_disabled") is not True:
        _finding(errors, "user_site_enabled", "manifest.runtime.user_site_disabled", "user site must be disabled")
    if runtime.get("source_tree_import") is not False:
        _finding(errors, "source_tree_import", "manifest.runtime.source_tree_import", "source-tree imports invalidate installed smoke")
    for field in ("interpreter_path", "implementation", "cpython_version", "platform", "machine", "site_packages_class"):
        if not isinstance(runtime.get(field), str) or not runtime.get(field):
            _finding(errors, "missing_runtime_identity", f"manifest.runtime.{field}", "measured runtime identity is required")
    if runtime.get("implementation") != "CPython":
        _finding(errors, "wrong_runtime_implementation", "manifest.runtime.implementation", "installed smoke must use CPython")
    if runtime.get("site_packages_class") != "install-root":
        _finding(errors, "unsafe_install_location", "manifest.runtime.site_packages_class", "installed smoke must use the isolated install-root class")
    user_site_evidence = runtime.get("user_site_evidence")
    if not isinstance(user_site_evidence, Mapping):
        _finding(errors, "missing_user_site_evidence", "manifest.runtime.user_site_evidence", "measured user-site evidence is required")
    elif user_site_evidence.get("python_no_user_site") is not True or user_site_evidence.get("site_enable_user_site") is not True:
        _finding(errors, "user_site_enabled", "manifest.runtime.user_site_evidence", "measured user-site evidence must be disabled")
    _require_sha256(runtime.get("requires_dist_sha256"), "manifest.runtime.requires_dist_sha256", errors)
    if runtime.get("requires_dist_sha256") != distribution.get("requires_dist_sha256"):
        _finding(errors, "requires_dist_digest_mismatch", "manifest.runtime.requires_dist_sha256", "runtime Requires-Dist digest differs from distribution metadata")
    if runtime_identity is not None:
        for field in (
            "interpreter_path",
            "implementation",
            "cpython_version",
            "platform",
            "machine",
            "isolated_interpreter",
            "user_site_disabled",
            "user_site_evidence",
            "site_packages_class",
        ):
            if field in runtime_identity and runtime.get(field) != runtime_identity.get(field):
                _finding(errors, "runtime_identity_mismatch", f"manifest.runtime.{field}", "manifest runtime differs from measured runtime identity")
    safe_distribution_paths = all(
        isinstance(distribution.get(field), str) and _safe_member_path(distribution.get(field))
        for field in ("dist_info_dir", "metadata_path", "record_path")
    )
    if not safe_distribution_paths:
        _finding(errors, "unsafe_installed_identity", "manifest.distribution", "installed identity paths cannot be inspected safely")

    expected_installed = expected.get("installed") if isinstance(expected, Mapping) else None
    if not isinstance(expected_installed, Mapping) and isinstance(expected, Mapping) and "distribution" in expected:
        expected_installed = expected
    if isinstance(expected_installed, Mapping):
        expected_distribution = expected_installed.get("distribution")
        if isinstance(expected_distribution, Mapping):
            for field in ("metadata_sha256", "record_sha256"):
                if field in expected_distribution and distribution.get(field) != expected_distribution.get(field):
                    _finding(errors, "installed_identity_mismatch", f"manifest.distribution.{field}", "installed distribution digest differs from accepted identity")
            if "record_entries" in expected_distribution and record_entries != expected_distribution.get("record_entries"):
                _finding(errors, "installed_identity_mismatch", "manifest.distribution.record_entries", "installed RECORD entries differ from accepted identity")
            for field in ("requires_dist", "requires_dist_sha256"):
                if field in expected_distribution and distribution.get(field) != expected_distribution.get(field):
                    _finding(errors, "installed_identity_mismatch", f"manifest.distribution.{field}", "installed Requires-Dist identity differs from accepted identity")
        expected_payload = expected_installed.get("payload")
        if isinstance(expected_payload, Mapping) and payload.get("sha256") != expected_payload.get("sha256"):
            _finding(errors, "installed_payload_mismatch", "manifest.payload.sha256", "installed payload differs from accepted identity")
        expected_capability = expected_installed.get("capability")
        if isinstance(expected_capability, Mapping) and capability.get("canonical_sha256") != expected_capability.get("canonical_sha256"):
            _finding(errors, "capability_digest_mismatch", "manifest.capability.canonical_sha256", "capability output differs from accepted identity")
        expected_runtime = expected_installed.get("runtime")
        if isinstance(expected_runtime, Mapping):
            for field in ("interpreter_path", "implementation", "cpython_version", "platform", "machine", "site_packages_class", "requires_dist_sha256"):
                if field in expected_runtime and runtime.get(field) != expected_runtime.get(field):
                    _finding(errors, "installed_runtime_mismatch", f"manifest.runtime.{field}", "installed runtime identity differs from accepted identity")

    if installed_root is not None and safe_distribution_paths:
        root_path = installed_root.resolve()
        if not root_path.is_dir():
            _finding(errors, "installed_root_missing", "installed_root", "installed root is not a directory")
        else:
            try:
                actual_paths = sorted(_relative_path(root_path, path) for path in _actual_files(root_path))
            except GovernanceInputError as error:
                _finding(errors, "unexpected_installed_file", "installed_root", str(error))
                actual_paths = []
            manifest_paths = sorted(item["path"] for item in record_entries if isinstance(item.get("path"), str))
            for path in sorted(set(actual_paths) - set(manifest_paths)):
                _finding(errors, "unexpected_installed_file", path, "installed file is absent from the accepted RECORD identity")
            for path in sorted(set(manifest_paths) - set(actual_paths)):
                _finding(errors, "installed_file_missing", path, "accepted RECORD file is missing from the installed root")
            for entry in record_entries:
                path = entry.get("path")
                if not isinstance(path, str) or path not in actual_paths:
                    continue
                file_path = root_path.joinpath(*path.split("/"))
                actual = {"size": file_path.stat().st_size, "sha256": sha256_file(file_path)}
                if actual != {"size": entry.get("size"), "sha256": entry.get("sha256")}:
                    _finding(errors, "installed_record_mismatch", path, "installed bytes do not match RECORD identity")
            try:
                dist_info = root_path / str(distribution.get("dist_info_dir"))
                record_path = root_path / str(distribution.get("record_path"))
                metadata_path = root_path / str(distribution.get("metadata_path"))
                if record_path.is_file():
                    parsed_entries = _record_entries(root_path, record_path)
                    if parsed_entries != record_entries:
                        _finding(errors, "record_content_mismatch", distribution.get("record_path", ""), "RECORD content differs from manifest")
                    if sha256_file(record_path) != distribution.get("record_sha256"):
                        _finding(errors, "record_digest_mismatch", distribution.get("record_path", ""), "RECORD digest differs from manifest")
                if metadata_path.is_file() and sha256_file(metadata_path) != distribution.get("metadata_sha256"):
                    _finding(errors, "metadata_digest_mismatch", distribution.get("metadata_path", ""), "METADATA digest differs from manifest")
                if metadata_path.is_file():
                    metadata = Parser().parsestr(metadata_path.read_text(encoding="utf-8"))
                    measured_requires_dist = sorted(metadata.get_all("Requires-Dist") or [])
                    if canonical_digest({"requires_dist": measured_requires_dist}) != distribution.get("requires_dist_sha256"):
                        _finding(errors, "requires_dist_digest_mismatch", distribution.get("requires_dist_sha256", ""), "METADATA Requires-Dist differs from manifest")
                if not dist_info.name.endswith(".dist-info"):
                    _finding(errors, "invalid_dist_info", "manifest.distribution.dist_info_dir", "distribution metadata directory is invalid")
            except (OSError, GovernanceInputError) as error:
                _finding(errors, "installed_identity_unreadable", "manifest.distribution", str(error))

    if capability_output is not None:
        try:
            raw = capability_output.read_bytes()
            value = _load_json_bytes(raw)
            if not isinstance(value, Mapping):
                raise GovernanceInputError("capability output must be a JSON object")
            if sha256_bytes(raw) != capability.get("sha256"):
                _finding(errors, "capability_raw_digest_mismatch", "manifest.capability.sha256", "capability bytes differ from manifest")
            if canonical_digest(value) != capability.get("canonical_sha256"):
                _finding(errors, "capability_digest_mismatch", "manifest.capability.canonical_sha256", "capability canonical digest differs from manifest")
            if _capability_identity(value) != capability.get("identity"):
                _finding(errors, "capability_identity_mismatch", "manifest.capability.identity", "capability identity differs from manifest")
        except (OSError, UnicodeError, json.JSONDecodeError, GovernanceInputError) as error:
            _finding(errors, "invalid_capability_output", "capability_output", str(error))
    evidence = {
        "candidate": dict(candidate),
        "distribution": {
            "name": distribution.get("name"),
            "version": distribution.get("version"),
            "record_sha256": distribution.get("record_sha256"),
        },
        "payload_sha256": payload.get("sha256"),
        "capability_canonical_sha256": capability.get("canonical_sha256"),
        "record_file_count": len(record_entries),
    }
    return _finish(errors, evidence)


verify_installed_distribution = verify_installed_manifest


def _publication_authority_parts(
    expected: Mapping[str, Any] | None,
    errors: list[Finding],
    *,
    canonical_valid: bool = True,
) -> tuple[Mapping[str, Any], Mapping[str, Any], bool]:
    if expected is None:
        _finding(errors, "missing_expected_authority", "expected", "active publication requires an external expected authority manifest")
        return {}, {}, False
    if not isinstance(expected, Mapping):
        _finding(errors, "invalid_expected_authority", "expected", "expected authority must be a JSON object")
        return {}, {}, False
    if expected.get("schema_version") != PUBLICATION_AUTHORITY_SCHEMA_VERSION:
        _finding(errors, "invalid_expected_authority", "expected.schema_version", "expected must be an immutable publication authority manifest")
    if expected.get("immutable") is not True:
        _finding(errors, "invalid_expected_authority", "expected.immutable", "expected authority must be immutable")
    candidate = _require_mapping(expected.get("candidate"), "expected.candidate", errors)
    publication = _require_mapping(expected.get("publication"), "expected.publication", errors)
    if candidate is None or publication is None:
        return candidate or {}, publication or {}, False
    valid = (
        canonical_valid
        and expected.get("schema_version") == PUBLICATION_AUTHORITY_SCHEMA_VERSION
        and expected.get("immutable") is True
    )
    for field in ("repository", "source_commit", "workflow_run_id", "workflow_run_attempt", "version", "artifact_name"):
        if field == "source_commit":
            valid = _require_commit(candidate.get(field), f"expected.candidate.{field}", errors) and valid
        elif field == "workflow_run_id":
            valid = _require_run_id(candidate.get(field), f"expected.candidate.{field}", errors) and valid
        elif field == "workflow_run_attempt":
            valid = _require_run_attempt(candidate.get(field), f"expected.candidate.{field}", errors) and valid
        elif field == "version":
            valid = _require_version(candidate.get(field), f"expected.candidate.{field}", errors) and valid
        elif not isinstance(candidate.get(field), str) or not candidate.get(field):
            _finding(errors, "invalid_expected_authority", f"expected.candidate.{field}", "authority candidate identity is required")
            valid = False
    for field in (
        "accepted_run_id",
        "accepted_run_attempt",
        "accepted_commit",
        "accepted_artifact_name",
        "accepted_version",
        "tag",
        "environment",
        "trusted_publisher",
        "accepted_artifact_digests",
    ):
        if field not in publication:
            _finding(errors, "invalid_expected_authority", f"expected.publication.{field}", "authority publication identity is required")
            valid = False
    valid = _require_run_id(publication.get("accepted_run_id"), "expected.publication.accepted_run_id", errors) and valid
    valid = _require_run_attempt(
        publication.get("accepted_run_attempt"), "expected.publication.accepted_run_attempt", errors
    ) and valid
    valid = _require_commit(publication.get("accepted_commit"), "expected.publication.accepted_commit", errors) and valid
    valid = _require_version(publication.get("accepted_version"), "expected.publication.accepted_version", errors) and valid
    if not isinstance(publication.get("accepted_artifact_name"), str) or not publication.get("accepted_artifact_name"):
        _finding(errors, "invalid_expected_authority", "expected.publication.accepted_artifact_name", "authority artifact name is required")
        valid = False
    if not isinstance(publication.get("tag"), str) or not TAG_PATTERN.fullmatch(publication.get("tag", "")):
        _finding(errors, "invalid_expected_authority", "expected.publication.tag", "authority tag must be a final semantic version tag")
        valid = False
    if publication.get("environment") != "pypi":
        _finding(errors, "invalid_expected_authority", "expected.publication.environment", "authority environment must be pypi")
        valid = False
    trusted = publication.get("trusted_publisher")
    trusted_fields = {"owner", "repository", "workflow", "environment"}
    if not isinstance(trusted, Mapping) or set(trusted) != trusted_fields:
        _finding(errors, "invalid_expected_authority", "expected.publication.trusted_publisher", "authority Trusted Publisher tuple is incomplete or has extra fields")
        valid = False
    else:
        for field in sorted(trusted_fields):
            if not isinstance(trusted.get(field), str) or not trusted.get(field):
                _finding(errors, "invalid_expected_authority", f"expected.publication.trusted_publisher.{field}", "authority Trusted Publisher field is required")
                valid = False
        if trusted.get("workflow") != ".github/workflows/publish-accepted-release.yml":
            _finding(errors, "invalid_expected_authority", "expected.publication.trusted_publisher.workflow", "authority workflow is not the reviewed publisher")
            valid = False
        if trusted.get("environment") != "pypi":
            _finding(errors, "invalid_expected_authority", "expected.publication.trusted_publisher.environment", "authority Trusted Publisher environment must be pypi")
            valid = False
    accepted_digests = publication.get("accepted_artifact_digests")
    if not isinstance(accepted_digests, Mapping) or not accepted_digests:
        _finding(errors, "invalid_expected_authority", "expected.publication.accepted_artifact_digests", "authority artifact digests are required")
        valid = False
    else:
        wheel_count = 0
        sdist_count = 0
        for filename, digest in accepted_digests.items():
            if not isinstance(filename, str) or not _require_safe_relative(filename, "expected.publication.accepted_artifact_digests", errors):
                _finding(errors, "invalid_expected_authority", "expected.publication.accepted_artifact_digests", "authority artifact names must be safe relative paths")
                valid = False
            else:
                wheel_count += filename.endswith(".whl")
                sdist_count += filename.endswith(".tar.gz")
            if not _require_sha256(digest, f"expected.publication.accepted_artifact_digests.{filename}", errors):
                valid = False
        if len(accepted_digests) != 2 or wheel_count != 1 or sdist_count != 1:
            _finding(errors, "invalid_expected_authority", "expected.publication.accepted_artifact_digests", "authority must name exactly one wheel and one sdist")
            valid = False
    if valid:
        if _as_run_id(publication.get("accepted_run_id")) != _as_run_id(candidate.get("workflow_run_id")):
            _finding(errors, "invalid_expected_authority", "expected.publication.accepted_run_id", "authority run differs from candidate")
            valid = False
        if _as_run_id(publication.get("accepted_run_attempt")) != _as_run_id(candidate.get("workflow_run_attempt")):
            _finding(errors, "invalid_expected_authority", "expected.publication.accepted_run_attempt", "authority attempt differs from candidate")
            valid = False
        if publication.get("accepted_commit") != candidate.get("source_commit"):
            _finding(errors, "invalid_expected_authority", "expected.publication.accepted_commit", "authority commit differs from candidate")
            valid = False
        if publication.get("accepted_artifact_name") != candidate.get("artifact_name"):
            _finding(errors, "invalid_expected_authority", "expected.publication.accepted_artifact_name", "authority artifact name differs from candidate")
            valid = False
        if publication.get("accepted_version") != candidate.get("version"):
            _finding(errors, "invalid_expected_authority", "expected.publication.accepted_version", "authority version differs from candidate")
            valid = False
        if ".dev" in candidate.get("version", "") or publication.get("tag") != f"v{candidate.get('version')}":
            _finding(errors, "invalid_expected_authority", "expected.publication.tag", "authority tag must match the final candidate version")
            valid = False
        expected_artifact_name = (
            f"release-candidate-{_as_run_id(candidate.get('workflow_run_id'))}-"
            f"{_as_run_id(candidate.get('workflow_run_attempt'))}"
        )
        if candidate.get("artifact_name") != expected_artifact_name:
            _finding(errors, "invalid_expected_authority", "expected.candidate.artifact_name", "authority artifact name must bind run id and run attempt")
            valid = False
    return candidate, publication, valid


def verify_publication_activation(
    activation: Mapping[str, Any],
    *,
    expected: Mapping[str, Any] | None = None,
    downloaded_manifest: Mapping[str, Any] | None = None,
    downloaded_artifact_dir: Path | None = None,
) -> VerificationResult:
    errors: list[Finding] = []
    activation_canonical = _check_canonical_input(activation, "activation", errors)
    expected_canonical = True
    if expected is not None:
        expected_canonical = _check_canonical_input(expected, "expected", errors)
    if not activation_canonical or not expected_canonical:
        return _finish(errors)
    expected_candidate, expected_publication, authority_verified = _publication_authority_parts(
        expected, errors, canonical_valid=expected_canonical
    )
    root = _require_mapping(activation, "activation", errors)
    if root is None:
        return _finish(errors)
    if root.get("schema_version") != PUBLICATION_SCHEMA_VERSION:
        _finding(errors, "unsupported_schema", "activation.schema_version", "publication activation schema is unsupported")
    body = _require_mapping(root.get("activation"), "activation.activation", errors)
    if body is None:
        body = {}
    if body.get("enabled") is not True or body.get("publication_authorized") is not True:
        _finding(errors, "publication_not_authorized", "activation.activation", "publication requires a future explicit R1-B authority")
    if body.get("mode") != "r1_b":
        _finding(errors, "publication_mode_invalid", "activation.activation.mode", "only the future r1_b mode may activate publication")
    if body.get("build_once") is not True or body.get("rebuild") is not False or body.get("source_artifact_only") is not True:
        _finding(errors, "rebuild_attempt", "activation.activation", "publication must use accepted bytes without rebuilding")
    authority_digest = body.get("authority_manifest_sha256")
    _require_sha256(authority_digest, "activation.activation.authority_manifest_sha256", errors)
    if authority_verified and authority_digest != canonical_digest(expected):
        _finding(errors, "wrong_authority_digest", "activation.activation.authority_manifest_sha256", "activation does not name the external expected authority manifest")
        authority_verified = False
    _require_run_id(body.get("accepted_run_id"), "activation.activation.accepted_run_id", errors)
    _require_run_attempt(body.get("accepted_run_attempt"), "activation.activation.accepted_run_attempt", errors)
    _require_commit(body.get("accepted_commit"), "activation.activation.accepted_commit", errors)
    if not isinstance(body.get("accepted_artifact_name"), str) or not body.get("accepted_artifact_name"):
        _finding(errors, "missing_identity", "activation.activation.accepted_artifact_name", "accepted artifact name is required")
    if not isinstance(body.get("accepted_version"), str) or not body.get("accepted_version"):
        _finding(errors, "missing_identity", "activation.activation.accepted_version", "accepted package version is required")
    if not isinstance(body.get("tag"), str) or not TAG_PATTERN.fullmatch(body.get("tag", "")):
        _finding(errors, "mutable_tag", "activation.activation.tag", "publication tag must be an immutable semantic version tag")
    if body.get("environment") != "pypi":
        _finding(errors, "wrong_environment", "activation.activation.environment", "publication must use the protected pypi environment")
    trusted = _require_mapping(body.get("trusted_publisher"), "activation.activation.trusted_publisher", errors)
    if trusted is None:
        trusted = {}
    for field in ("owner", "repository", "workflow", "environment"):
        if not isinstance(trusted.get(field), str) or not trusted.get(field):
            _finding(errors, "missing_trusted_publisher", f"activation.activation.trusted_publisher.{field}", "trusted publisher identity is required")
    if trusted.get("workflow") != ".github/workflows/publish-accepted-release.yml":
        _finding(errors, "wrong_trusted_publisher", "activation.activation.trusted_publisher.workflow", "trusted publisher workflow is not the reviewed workflow")
    if trusted.get("environment") != body.get("environment"):
        _finding(errors, "wrong_trusted_publisher", "activation.activation.trusted_publisher.environment", "trusted publisher environment does not match activation")
    if body.get("long_lived_token") not in (None, False):
        _finding(errors, "long_lived_token_forbidden", "activation.activation.long_lived_token", "long-lived publication tokens are forbidden")
    if "token" in body or "password" in body:
        _finding(errors, "long_lived_token_forbidden", "activation.activation", "publication activation must not contain token material")
    digests = body.get("accepted_artifact_digests")
    if not isinstance(digests, Mapping) or not digests:
        _finding(errors, "missing_artifact_digests", "activation.activation.accepted_artifact_digests", "accepted artifact digests are required")
        digests = {}
    else:
        for filename, digest in digests.items():
            _require_safe_relative(filename, "activation.activation.accepted_artifact_digests", errors)
            _require_sha256(digest, f"activation.activation.accepted_artifact_digests.{filename}", errors)

    expected_run_id = expected_candidate.get("workflow_run_id", expected_publication.get("accepted_run_id"))
    expected_run_attempt = expected_candidate.get("workflow_run_attempt", expected_publication.get("accepted_run_attempt"))
    expected_commit = expected_candidate.get("source_commit", expected_publication.get("accepted_commit"))
    for field, code in (
        ("accepted_run_id", "wrong_run"),
        ("accepted_run_attempt", "wrong_run_attempt"),
        ("accepted_commit", "wrong_commit"),
        ("accepted_artifact_name", "wrong_artifact_name"),
        ("accepted_version", "version_mismatch"),
        ("tag", "wrong_tag"),
        ("environment", "wrong_environment"),
    ):
        if field == "accepted_run_id":
            expected_value = expected_run_id
        elif field == "accepted_run_attempt":
            expected_value = expected_run_attempt
        elif field == "accepted_commit":
            expected_value = expected_commit
        else:
            expected_value = expected_publication.get(field)
        if expected_value is not None:
            actual_value = body.get(field)
            if field in {"accepted_run_id", "accepted_run_attempt"}:
                actual_value, expected_value = _as_run_id(actual_value), _as_run_id(expected_value)
            if actual_value != expected_value:
                _finding(errors, code, f"activation.activation.{field}", "publication identity does not match accepted identity")
    expected_digests = _expected_artifact_digests(expected)
    if expected_digests and set(digests) != set(expected_digests):
        _finding(errors, "artifact_set_mismatch", "activation.activation.accepted_artifact_digests", "publication digest keys do not match external authority")
    if expected_digests and dict(digests) != expected_digests:
        _finding(errors, "wrong_artifact_digest", "activation.activation.accepted_artifact_digests", "publication bytes do not match accepted artifact digests")
    expected_trusted = expected_publication.get("trusted_publisher")
    if isinstance(expected_trusted, Mapping) and dict(trusted) != dict(expected_trusted):
        _finding(errors, "wrong_trusted_publisher", "activation.activation.trusted_publisher", "Trusted Publisher tuple differs from the accepted tuple")
    expected_version = expected_candidate.get("version")
    if isinstance(expected_version, str) and ".dev" in expected_version:
        _finding(errors, "dev_version_for_publication", "expected.candidate.version", "development versions cannot activate a public tag")
    if isinstance(expected_version, str) and body.get("tag") != f"v{expected_version}":
        _finding(errors, "tag_version_mismatch", "activation.activation.tag", "publication tag must match the package version")
    if downloaded_manifest is not None:
        downloaded_expected_candidate: dict[str, Any] = {
            "source_commit": body.get("accepted_commit"),
            "workflow_run_id": body.get("accepted_run_id"),
            "workflow_run_attempt": body.get("accepted_run_attempt"),
            "artifact_name": body.get("accepted_artifact_name"),
            "version": body.get("accepted_version"),
        }
        if expected_candidate.get("repository") is not None:
            downloaded_expected_candidate["repository"] = expected_candidate.get("repository")
        downloaded_result = verify_artifact_manifest(
            downloaded_manifest,
            expected={
                "candidate": downloaded_expected_candidate,
                "artifact_digests": dict(digests),
            },
            artifact_paths=downloaded_artifact_dir,
        )
        for finding in downloaded_result.errors:
            errors.append(finding)
        if downloaded_artifact_dir is None:
            _finding(errors, "downloaded_artifact_dir_missing", "activation.downloaded_artifact_dir", "active publication must verify downloaded artifact bytes")
    else:
        _finding(errors, "accepted_bytes_missing", "activation.downloaded_manifest", "active publication requires downloaded accepted bytes")
    if downloaded_manifest is not None and downloaded_artifact_dir is None:
        authority_verified = False
    write_authority_checked = authority_verified and not errors
    evidence = {
        "mode": body.get("mode"),
        "accepted_run_id": _as_run_id(body.get("accepted_run_id")),
        "accepted_run_attempt": _as_run_id(body.get("accepted_run_attempt")),
        "accepted_commit": body.get("accepted_commit"),
        "tag": body.get("tag"),
        "environment": body.get("environment"),
        "artifact_digests": dict(digests),
        "write_authority_checked": write_authority_checked,
    }
    return _finish(errors, evidence)


def verify_release_contract(
    artifact_manifest: Mapping[str, Any],
    installed_manifest: Mapping[str, Any],
    *,
    expected: Mapping[str, Any] | None = None,
    publication_activation: Mapping[str, Any] | None = None,
    downloaded_artifact_dir: Path | None = None,
) -> VerificationResult:
    artifact_result = verify_artifact_manifest(artifact_manifest, expected=expected)
    installed_expected: dict[str, Any] = dict(artifact_manifest)
    if isinstance(expected, Mapping):
        installed_expected.update(expected)
    installed_result = verify_installed_manifest(installed_manifest, expected=installed_expected)
    errors = list(artifact_result.errors) + list(installed_result.errors)
    evidence: dict[str, Any] = {
        "artifact": artifact_result.evidence,
        "installed": installed_result.evidence,
    }
    if publication_activation is not None:
        publication_result = verify_publication_activation(
            publication_activation,
            expected=expected,
            downloaded_manifest=artifact_manifest,
            downloaded_artifact_dir=downloaded_artifact_dir,
        )
        errors.extend(publication_result.errors)
        evidence["publication"] = publication_result.evidence
    return _finish(errors, evidence)


def build_operation_manifest(
    artifact_manifest_path: Path,
    installed_manifest_path: Path,
    sbom_path: Path,
    provenance_inputs_path: Path,
    *,
    audit_lock: str = "requirements/locks/linux_x86_64/py312/audit.txt",
) -> dict[str, Any]:
    artifact = _read_json(artifact_manifest_path)
    installed = _read_json(installed_manifest_path)
    artifact_result = verify_artifact_manifest(artifact)
    installed_result = verify_installed_manifest(installed, expected=artifact)
    if not artifact_result.ok:
        raise GovernanceInputError(f"artifact manifest is invalid: {artifact_result.codes}")
    if not installed_result.ok:
        raise GovernanceInputError(f"installed manifest is invalid: {installed_result.codes}")
    for path, label in ((sbom_path, "CycloneDX SBOM"),):
        if not path.is_file() or path.is_symlink():
            raise GovernanceInputError(f"{label} is missing or not a regular file")

    artifact_digest = canonical_digest(artifact)
    installed_digest = canonical_digest(installed)
    sbom_digest = sha256_file(sbom_path)
    provenance = {
        "schema_version": PROVENANCE_SCHEMA_VERSION,
        "candidate": artifact["candidate"],
        "artifact_manifest_sha256": artifact_digest,
        "installed_manifest_sha256": installed_digest,
        "sbom_sha256": sbom_digest,
        "build_once": artifact["build_once"],
        "audit": {"tool": "pip-audit", "lock": audit_lock, "format": "cyclonedx-json"},
    }
    _write_json(provenance_inputs_path, provenance)
    provenance_digest = sha256_file(provenance_inputs_path)
    return {
        "schema_version": OPERATION_SCHEMA_VERSION,
        "immutable": True,
        "candidate": artifact["candidate"],
        "build_once": artifact["build_once"],
        "artifact_manifest_sha256": artifact_digest,
        "installed_manifest_sha256": installed_digest,
        "sbom_sha256": sbom_digest,
        "provenance_inputs_sha256": provenance_digest,
        "audit": {"tool": "pip-audit", "lock": audit_lock, "format": "cyclonedx-json"},
    }


def verify_operation_manifest(
    manifest: Mapping[str, Any],
    *,
    expected: Mapping[str, Any] | None = None,
    artifact_manifest: Mapping[str, Any] | None = None,
    installed_manifest: Mapping[str, Any] | None = None,
    sbom_path: Path | None = None,
    provenance_inputs_path: Path | None = None,
) -> VerificationResult:
    errors: list[Finding] = []
    manifest_canonical = _check_canonical_input(manifest, "manifest", errors)
    expected_canonical = True
    if expected is not None:
        expected_canonical = _check_canonical_input(expected, "expected", errors)
    if not manifest_canonical or not expected_canonical:
        return _finish(errors)
    root = _require_mapping(manifest, "manifest", errors)
    if root is None:
        return _finish(errors)
    if root.get("schema_version") != OPERATION_SCHEMA_VERSION:
        _finding(errors, "unsupported_schema", "manifest.schema_version", "operation manifest schema is unsupported")
    if root.get("immutable") is not True:
        _finding(errors, "mutable_operation_manifest", "manifest.immutable", "operation manifest must be immutable")
    candidate = _require_mapping(root.get("candidate"), "manifest.candidate", errors)
    if candidate is None:
        candidate = {}
    _require_commit(candidate.get("source_commit"), "manifest.candidate.source_commit", errors)
    _require_run_id(candidate.get("workflow_run_id"), "manifest.candidate.workflow_run_id", errors)
    _require_run_attempt(candidate.get("workflow_run_attempt"), "manifest.candidate.workflow_run_attempt", errors)
    _require_version(candidate.get("version"), "manifest.candidate.version", errors)
    _compare_candidate(candidate, expected, errors, "manifest.candidate")
    build = _require_mapping(root.get("build_once"), "manifest.build_once", errors)
    if build is not None and (build.get("attempts") != 1 or build.get("rebuild") is not False):
        _finding(errors, "rebuild_attempt", "manifest.build_once", "operation must describe one non-rebuilt candidate")
    for field in (
        "artifact_manifest_sha256",
        "installed_manifest_sha256",
        "sbom_sha256",
        "provenance_inputs_sha256",
    ):
        _require_sha256(root.get(field), f"manifest.{field}", errors)
    audit = _require_mapping(root.get("audit"), "manifest.audit", errors)
    if audit is not None:
        if audit.get("tool") != "pip-audit" or audit.get("format") != "cyclonedx-json":
            _finding(errors, "wrong_audit_tool", "manifest.audit", "operation must identify the exact CycloneDX audit tool")
        if not isinstance(audit.get("lock"), str) or not audit.get("lock", "").endswith("/audit.txt"):
            _finding(errors, "wrong_audit_lock", "manifest.audit.lock", "operation must identify a native audit lock")
    if artifact_manifest is not None and root.get("artifact_manifest_sha256") != canonical_digest(artifact_manifest):
        _finding(errors, "artifact_manifest_digest_mismatch", "manifest.artifact_manifest_sha256", "artifact manifest digest differs")
    if installed_manifest is not None and root.get("installed_manifest_sha256") != canonical_digest(installed_manifest):
        _finding(errors, "installed_manifest_digest_mismatch", "manifest.installed_manifest_sha256", "installed manifest digest differs")
    if sbom_path is not None:
        if not sbom_path.is_file() or sbom_path.is_symlink():
            _finding(errors, "sbom_missing", "manifest.sbom_sha256", "CycloneDX SBOM is missing")
        elif root.get("sbom_sha256") != sha256_file(sbom_path):
            _finding(errors, "sbom_digest_mismatch", "manifest.sbom_sha256", "CycloneDX SBOM bytes differ")
    if provenance_inputs_path is not None:
        if not provenance_inputs_path.is_file() or provenance_inputs_path.is_symlink():
            _finding(errors, "provenance_inputs_missing", "manifest.provenance_inputs_sha256", "provenance inputs are missing")
        elif root.get("provenance_inputs_sha256") != sha256_file(provenance_inputs_path):
            _finding(errors, "provenance_inputs_digest_mismatch", "manifest.provenance_inputs_sha256", "provenance input bytes differ")
    return _finish(
        errors,
        {
            "candidate": dict(candidate),
            "artifact_manifest_sha256": root.get("artifact_manifest_sha256"),
            "installed_manifest_sha256": root.get("installed_manifest_sha256"),
            "sbom_sha256": root.get("sbom_sha256"),
            "provenance_inputs_sha256": root.get("provenance_inputs_sha256"),
        },
    )


def _read_json(path: Path) -> Any:
    return _load_json_bytes(path.read_bytes())


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical_json(value), encoding="utf-8", newline="\n")


def _git_readonly(repo: Path, *arguments: str) -> bytes:
    if not repo.is_dir() or repo.is_symlink():
        raise GovernanceInputError("history repository is missing or not a regular directory")
    environment = os.environ.copy()
    environment.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_OPTIONAL_LOCKS": "0", "GIT_PAGER": "cat"})
    completed = subprocess.run(
        ["git", "-C", str(repo), *arguments],
        check=False,
        capture_output=True,
        env=environment,
    )
    if completed.returncode != 0:
        raise GovernanceInputError(f"git read failed for: {' '.join(arguments)}")
    return completed.stdout


def _history_finding_types(path: str, content: bytes) -> list[str]:
    findings: set[str] = set()
    is_pdf = path.lower().endswith(".pdf") or (bytes((37,)) + b"PDF-") in content
    if is_pdf:
        findings.add("pdf")
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        text = None
    if text is None or (b"\x00" in content and not is_pdf):
        if not is_pdf:
            findings.add("binary")
        return sorted(findings)
    question_marker = "Q" + "001"
    marker_pattern = rf"\b{re.escape(question_marker)}\b"
    if re.search(marker_pattern, text, re.IGNORECASE) and (
        re.search(
            rf"(?:{marker_pattern}.{{0,200}}(?:access was absent|access absent|not authorized|not available|not accessed|without access|inaccessible|unavailable))|(?:"
            rf"(?:access was absent|access absent|not authorized|not available|not accessed|without access|inaccessible|unavailable).{{0,200}}{marker_pattern})",
            text,
            re.IGNORECASE | re.DOTALL,
        )
    ):
        findings.add("historical_boundary")
    if re.search(
        r"(?:-----BEGIN [A-Z ]*PRIVATE KEY-----|\b(?:api[_-]?key|access[_-]?token|secret[_-]?key|password)\b\s*[:=]\s*[\"']?[A-Za-z0-9_./+=:-]{12,}|\b(?:ghp|github_pat|pypi)-[A-Za-z0-9_-]{12,})",
        text,
        re.IGNORECASE,
    ):
        findings.add("credential")
    if re.search(
        r"(?:[A-Za-z]:[\\/](?:private|users|home|蛋白质降解)[^\s\"']*|/(?:private|users|home)/[^\s\"']+|\\\\[^\\\s]+[\\/](?:private|users|home)[^\s\"']*)",
        text,
        re.IGNORECASE,
    ):
        findings.add("private_path")
    return sorted(findings)


def collect_reachable_history(repo: Path) -> dict[str, Any]:
    ref_lines = _git_readonly(repo, "for-each-ref", "--format=%(refname)\t%(objectname)").splitlines()
    refs: list[dict[str, str]] = []
    for line in ref_lines:
        decoded = line.decode("ascii")
        name, target = decoded.split("\t", 1)
        refs.append({"name": name, "target": target})
    refs.sort(key=lambda item: item["name"])

    commit_lines = _git_readonly(repo, "rev-list", "--all", "--topo-order").splitlines()
    commits = sorted({line.decode("ascii") for line in commit_lines})
    blob_records: set[tuple[str, str]] = set()
    for commit in commits:
        tree_output = _git_readonly(repo, "ls-tree", "-r", "-z", "--full-tree", commit)
        for record in tree_output.split(b"\0"):
            if not record:
                continue
            try:
                header, raw_path = record.split(b"\t", 1)
                mode, object_type, blob = header.decode("ascii").split(" ", 2)
            except (UnicodeDecodeError, ValueError) as error:
                raise GovernanceInputError("history tree output is malformed") from error
            if object_type != "blob" or not GIT_OBJECT_PATTERN.fullmatch(blob):
                continue
            try:
                path = raw_path.decode("utf-8")
            except UnicodeDecodeError as error:
                raise GovernanceInputError("history contains a non-UTF-8 repository path") from error
            blob_records.add((path, blob))

    blobs = [{"path": path, "blob": blob} for path, blob in sorted(blob_records)]
    findings: list[dict[str, str]] = []
    for item in blobs:
        content = _git_readonly(repo, "cat-file", "blob", item["blob"])
        for finding_type in _history_finding_types(item["path"], content):
            findings.append({"path": item["path"], "type": finding_type, "blob": item["blob"]})
    findings.sort(key=lambda item: (item["path"], item["type"], item["blob"]))
    return {
        "schema_version": HISTORY_EXPECTATION_SCHEMA_VERSION,
        "refs": refs,
        "commits": commits,
        "blobs": blobs,
        "findings": findings,
    }


def _expected_history_refs(value: Any, errors: list[Finding]) -> list[dict[str, str]]:
    if not isinstance(value, list):
        _finding(errors, "invalid_history_expectations", "expected.refs", "expected history refs must be an array")
        return []
    result: list[dict[str, str]] = []
    for index, item in enumerate(value):
        mapping = _require_mapping(item, f"expected.refs[{index}]", errors)
        if mapping is None:
            continue
        name, target = mapping.get("name"), mapping.get("target")
        if not isinstance(name, str) or not name or not isinstance(target, str) or not GIT_OBJECT_PATTERN.fullmatch(target):
            _finding(errors, "invalid_history_expectations", f"expected.refs[{index}]", "history ref requires a name and full object SHA")
            continue
        result.append({"name": name, "target": target})
    result.sort(key=lambda item: item["name"])
    if len(result) != len({item["name"] for item in result}):
        _finding(errors, "invalid_history_expectations", "expected.refs", "expected history refs must be unique")
    return result


def _expected_history_findings(value: Any, errors: list[Finding]) -> list[dict[str, str]]:
    if not isinstance(value, list):
        _finding(errors, "invalid_history_expectations", "expected.findings", "expected history findings must be an array")
        return []
    result: list[dict[str, str]] = []
    for index, item in enumerate(value):
        mapping = _require_mapping(item, f"expected.findings[{index}]", errors)
        if mapping is None:
            continue
        path, finding_type, blob = mapping.get("path"), mapping.get("type"), mapping.get("blob")
        if not isinstance(path, str) or not _safe_member_path(path) or not isinstance(finding_type, str) or finding_type not in HISTORY_FINDING_TYPES:
            _finding(errors, "invalid_history_expectations", f"expected.findings[{index}]", "history finding requires a supported type and path")
            continue
        if not isinstance(blob, str) or not GIT_OBJECT_PATTERN.fullmatch(blob):
            _finding(errors, "invalid_history_expectations", f"expected.findings[{index}].blob", "history finding requires a full blob SHA")
            continue
        result.append({"path": path, "type": finding_type, "blob": blob})
    result.sort(key=lambda item: (item["path"], item["type"], item["blob"]))
    if len(result) != len({(item["path"], item["type"], item["blob"]) for item in result}):
        _finding(errors, "invalid_history_expectations", "expected.findings", "expected history findings must be unique")
    return result


def verify_reachable_history(repo: Path, expected: Mapping[str, Any] | None) -> VerificationResult:
    errors: list[Finding] = []
    if expected is None:
        snapshot = collect_reachable_history(repo)
        _finding(errors, "missing_history_expectations", "expected", "reachable-history verification requires an explicit expected manifest")
        return _finish(errors, snapshot)
    if not isinstance(expected, Mapping):
        _finding(errors, "invalid_history_expectations", "expected", "history expectation manifest must be a JSON object")
        return _finish(errors)
    if not _check_canonical_input(expected, "expected", errors):
        return _finish(errors)
    snapshot = collect_reachable_history(repo)
    if expected.get("schema_version") != HISTORY_EXPECTATION_SCHEMA_VERSION:
        _finding(errors, "invalid_history_expectations", "expected.schema_version", "history expectation schema is unsupported")
    expected_refs = _expected_history_refs(expected.get("refs"), errors)
    if expected_refs != snapshot["refs"]:
        _finding(errors, "history_ref_drift", "expected.refs", "reachable history refs differ from the expected boundary")
    if "commits" in expected:
        expected_commits = expected.get("commits")
        if not isinstance(expected_commits, list) or any(not isinstance(item, str) or not COMMIT_PATTERN.fullmatch(item) for item in expected_commits):
            _finding(errors, "invalid_history_expectations", "expected.commits", "expected commits must be full commit SHAs")
        elif sorted(set(expected_commits)) != snapshot["commits"]:
            _finding(errors, "history_ref_drift", "expected.commits", "reachable commit boundary differs from the expected history")
    expected_findings = _expected_history_findings(expected.get("findings"), errors)
    actual_findings = snapshot["findings"]
    expected_set = {(item["path"], item["type"], item["blob"]): item for item in expected_findings}
    actual_set = {(item["path"], item["type"], item["blob"]): item for item in actual_findings}
    for key in sorted(set(expected_set) - set(actual_set)):
        path, finding_type, blob = key
        _finding(errors, "history_expected_finding_missing", path, f"{finding_type}:{blob}")
    for key in sorted(set(actual_set) - set(expected_set)):
        path, finding_type, blob = key
        _finding(errors, "history_unexpected_finding", path, f"{finding_type}:{blob}")
    return _finish(errors, snapshot)


def _result_or_error(function: Any, *args: Any, **kwargs: Any) -> VerificationResult:
    try:
        return function(*args, **kwargs)
    except (OSError, GovernanceInputError, TypeError, ValueError, json.JSONDecodeError) as error:
        return VerificationResult(False, (Finding("invalid_input", "input", str(error)),), {})


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="release_governance")
    commands = parser.add_subparsers(dest="command", required=True)

    artifact = commands.add_parser("artifact-manifest")
    artifact.add_argument("--artifact-dir", required=True, type=Path)
    artifact.add_argument("--repository", required=True)
    artifact.add_argument("--source-commit", required=True)
    artifact.add_argument("--run-id", required=True)
    artifact.add_argument("--run-attempt", required=True)
    artifact.add_argument("--version", required=True)
    artifact.add_argument("--artifact-name")
    artifact.add_argument("--output", required=True, type=Path)

    installed = commands.add_parser("installed-manifest")
    installed.add_argument("--root", required=True, type=Path)
    installed.add_argument("--candidate-manifest", required=True, type=Path)
    installed.add_argument("--capability-json", required=True, type=Path)
    installed.add_argument("--artifact", type=Path)
    installed.add_argument("--module-path", type=Path)
    installed.add_argument("--runtime-json", required=True, type=Path)
    installed.add_argument("--output", required=True, type=Path)

    verify_artifact = commands.add_parser("verify-artifact")
    verify_artifact.add_argument("--manifest", required=True, type=Path)
    verify_artifact.add_argument("--expected", type=Path)
    verify_artifact.add_argument("--artifact-dir", type=Path)

    verify_installed = commands.add_parser("verify-installed")
    verify_installed.add_argument("--manifest", required=True, type=Path)
    verify_installed.add_argument("--expected", type=Path)
    verify_installed.add_argument("--root", type=Path)
    verify_installed.add_argument("--capability-json", type=Path)

    verify_publication = commands.add_parser("verify-publication")
    verify_publication.add_argument("--manifest", required=True, type=Path)
    verify_publication.add_argument("--expected", required=True, type=Path)
    verify_publication.add_argument("--downloaded-manifest", required=True, type=Path)
    verify_publication.add_argument("--downloaded-artifact-dir", required=True, type=Path)

    verify_release = commands.add_parser("verify-release")
    verify_release.add_argument("--artifact-manifest", required=True, type=Path)
    verify_release.add_argument("--installed-manifest", required=True, type=Path)
    verify_release.add_argument("--expected", type=Path)
    verify_release.add_argument("--publication-activation", type=Path)
    verify_release.add_argument("--downloaded-artifact-dir", type=Path)

    operation = commands.add_parser("operation-manifest")
    operation.add_argument("--artifact-manifest", required=True, type=Path)
    operation.add_argument("--installed-manifest", required=True, type=Path)
    operation.add_argument("--sbom", required=True, type=Path)
    operation.add_argument("--provenance-inputs", required=True, type=Path)
    operation.add_argument("--audit-lock", required=True)
    operation.add_argument("--output", required=True, type=Path)

    history = commands.add_parser("scan-history")
    history.add_argument("--repo", required=True, type=Path)
    history.add_argument("--expected", required=True, type=Path)
    history.add_argument("--output", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "artifact-manifest":
            value = build_artifact_manifest(
                args.artifact_dir,
                repository=args.repository,
                source_commit=args.source_commit,
                workflow_run_id=args.run_id,
                workflow_run_attempt=args.run_attempt,
                version=args.version,
                artifact_name=args.artifact_name,
            )
            _write_json(args.output, value)
            result = verify_artifact_manifest(value, artifact_paths=args.artifact_dir)
        elif args.command == "installed-manifest":
            candidate_manifest = _read_json(args.candidate_manifest)
            candidate = candidate_manifest.get("candidate") if isinstance(candidate_manifest, Mapping) else None
            if not isinstance(candidate, Mapping):
                raise GovernanceInputError("candidate manifest lacks candidate identity")
            runtime_identity = _read_json(args.runtime_json)
            value = build_installed_manifest(
                args.root,
                candidate=candidate,
                capability_json=args.capability_json,
                artifact=args.artifact,
                module_path=args.module_path,
                runtime_identity=runtime_identity,
            )
            _write_json(args.output, value)
            result = verify_installed_manifest(
                value,
                installed_root=args.root,
                capability_output=args.capability_json,
                runtime_identity=runtime_identity,
            )
        elif args.command == "verify-artifact":
            manifest = _read_json(args.manifest)
            expected = _read_json(args.expected) if args.expected else None
            result = _result_or_error(
                verify_artifact_manifest,
                manifest,
                expected=expected,
                artifact_paths=args.artifact_dir,
            )
        elif args.command == "verify-installed":
            manifest = _read_json(args.manifest)
            expected = _read_json(args.expected) if args.expected else None
            result = _result_or_error(
                verify_installed_manifest,
                manifest,
                expected=expected,
                installed_root=args.root,
                capability_output=args.capability_json,
            )
        elif args.command == "verify-publication":
            manifest = _read_json(args.manifest)
            expected = _read_json(args.expected)
            downloaded = _read_json(args.downloaded_manifest) if args.downloaded_manifest else None
            result = _result_or_error(
                verify_publication_activation,
                manifest,
                expected=expected,
                downloaded_manifest=downloaded,
                downloaded_artifact_dir=args.downloaded_artifact_dir,
            )
        elif args.command == "verify-release":
            artifact_manifest = _read_json(args.artifact_manifest)
            installed_manifest = _read_json(args.installed_manifest)
            expected = _read_json(args.expected) if args.expected else None
            publication = _read_json(args.publication_activation) if args.publication_activation else None
            result = _result_or_error(
                verify_release_contract,
                artifact_manifest,
                installed_manifest,
                expected=expected,
                publication_activation=publication,
                downloaded_artifact_dir=args.downloaded_artifact_dir,
            )
        elif args.command == "scan-history":
            expected = _read_json(args.expected)
            result = _result_or_error(verify_reachable_history, args.repo, expected)
            if args.output is not None:
                _write_json(args.output, result.to_dict())
        else:
            artifact_path = args.artifact_manifest
            installed_path = args.installed_manifest
            sbom_path = args.sbom
            provenance_path = args.provenance_inputs
            artifact_manifest = _read_json(artifact_path)
            installed_manifest = _read_json(installed_path)
            value = build_operation_manifest(
                artifact_path,
                installed_path,
                sbom_path,
                provenance_path,
                audit_lock=args.audit_lock,
            )
            _write_json(args.output, value)
            result = verify_operation_manifest(
                value,
                artifact_manifest=artifact_manifest,
                installed_manifest=installed_manifest,
                sbom_path=sbom_path,
                provenance_inputs_path=provenance_path,
            )
    except (OSError, GovernanceInputError, TypeError, ValueError, json.JSONDecodeError) as error:
        result = VerificationResult(False, (Finding("invalid_input", "input", str(error)),), {})
    sys.stdout.write(canonical_json(result.to_dict()) + "\n")
    return 0 if result.ok else 1


__all__ = [
    "ARTIFACT_SCHEMA_VERSION",
    "INSTALLED_SCHEMA_VERSION",
    "MEMBER_SCHEMA_VERSION",
    "PUBLICATION_SCHEMA_VERSION",
    "PUBLICATION_AUTHORITY_SCHEMA_VERSION",
    "HISTORY_EXPECTATION_SCHEMA_VERSION",
    "OPERATION_SCHEMA_VERSION",
    "PROVENANCE_SCHEMA_VERSION",
    "Finding",
    "VerificationResult",
    "archive_member_manifest",
    "build_artifact_manifest",
    "build_installed_manifest",
    "canonical_digest",
    "canonical_json",
    "canonical_json_bytes",
    "build_operation_manifest",
    "main",
    "sha256_bytes",
    "sha256_file",
    "verify_artifact_manifest",
    "verify_installed_distribution",
    "verify_installed_manifest",
    "verify_publication_activation",
    "verify_operation_manifest",
    "verify_canonical_value",
    "collect_reachable_history",
    "verify_reachable_history",
    "verify_release_contract",
]


if __name__ == "__main__":
    sys.exit(main())
