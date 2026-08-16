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
import urllib.error
import urllib.parse
import urllib.request
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
PUBLICATION_AUTHORITY_SCHEMA_V2 = "ppwb.g1.publication_authority.v2"
PUBLICATION_AUTHORITY_SCHEMA_V3 = "ppwb.g1.publication_authority.v3"
PUBLICATION_CHECK_EVIDENCE_SCHEMA_VERSION = "ppwb.g1.publication_check_evidence.v1"
DEPENDENCY_SECURITY_WORKFLOW_PATH = ".github/workflows/dependency-security.yml"
EVENT_AWARE_CHECK_POLICY_FIELDS = {
    "dependency_security_workflow_path",
    "originating_pr_base_ref",
    "pr_head_dependency_review_job_name",
    "pr_head_dependency_review_event",
    "pr_head_dependency_review_app",
    "merge_push_dependency_audit_job_name",
    "merge_push_dependency_review_job_name",
    "merge_push_dependency_security_event",
    "merge_push_dependency_security_app",
    "merge_push_required_checks",
}
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
ASSET_BASENAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")


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


def _require_exact_fields(
    value: Mapping[str, Any],
    fields: set[str],
    path: str,
    errors: list[Finding],
) -> bool:
    actual = set(value)
    if actual != fields:
        _finding(
            errors,
            "noncanonical_fields",
            path,
            f"expected fields {sorted(fields)!r}; received {sorted(actual)!r}",
        )
        return False
    return True


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


def _accepted_artifact_name(candidate: Mapping[str, Any]) -> str:
    return (
        f"accepted-release-candidate-{_as_run_id(candidate.get('workflow_run_id'))}-"
        f"{_as_run_id(candidate.get('workflow_run_attempt'))}-{candidate.get('source_commit', '')}"
    )


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
    expected_artifact_name = _accepted_artifact_name(candidate)
    if candidate.get("artifact_name") != expected_artifact_name:
        _finding(
            errors,
            "invalid_artifact_name",
            "manifest.candidate.artifact_name",
            "accepted artifact name must bind run id, run attempt, and source commit",
        )
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
        "artifact_name": artifact_name
        or f"accepted-release-candidate-{normalized_run}-{normalized_attempt}-{source_commit}",
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


def _resolve_record_entry(root: Path, anchor: Path, value: Any) -> tuple[str, Path]:
    if not isinstance(value, str) or not value or "\\" in value or value.startswith("/"):
        raise GovernanceInputError(f"invalid RECORD path: {value}")
    parts = value.split("/")
    if any(part in {"", "."} for part in parts) or ":" in parts[0]:
        raise GovernanceInputError(f"invalid RECORD path: {value}")
    target = anchor.joinpath(*parts).resolve()
    try:
        relative = target.relative_to(root)
    except ValueError as error:
        raise GovernanceInputError(f"RECORD path escapes installed root: {value}") from error
    normalized = relative.as_posix()
    if not _safe_member_path(normalized):
        raise GovernanceInputError(f"invalid normalized RECORD path: {value}")
    return normalized, target


def _record_entries(root: Path, record_path: Path) -> list[dict[str, Any]]:
    record_relative = _relative_path(root, record_path)
    record_anchor = record_path.parent.parent
    result: list[dict[str, Any]] = []
    with record_path.open("r", encoding="utf-8", newline="") as stream:
        rows = list(csv.reader(stream))
    seen: set[str] = set()
    for row in rows:
        if len(row) != 3:
            raise GovernanceInputError(f"invalid RECORD row in {record_relative}")
        recorded_path, digest, size = row
        relative, target = _resolve_record_entry(root, record_anchor, recorded_path)
        if relative in seen:
            raise GovernanceInputError(f"duplicate normalized RECORD path: {recorded_path}")
        seen.add(relative)
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
    dist_prefix = f"{_relative_path(root, dist_info)}/"
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
    exact_root = _require_exact_fields(
        expected,
        {"schema_version", "immutable", "candidate", "publication"},
        "expected",
        errors,
    )
    schema_version = expected.get("schema_version")
    if schema_version not in {PUBLICATION_AUTHORITY_SCHEMA_VERSION, PUBLICATION_AUTHORITY_SCHEMA_V3}:
        _finding(
            errors,
            "unsupported_publication_authority_schema",
            "expected.schema_version",
            "expected must be an immutable publication authority manifest in schema v1 or v3",
        )
    if expected.get("immutable") is not True:
        _finding(errors, "invalid_expected_authority", "expected.immutable", "expected authority must be immutable")
    candidate = _require_mapping(expected.get("candidate"), "expected.candidate", errors)
    publication = _require_mapping(expected.get("publication"), "expected.publication", errors)
    if candidate is None or publication is None:
        return candidate or {}, publication or {}, False
    exact_candidate = _require_exact_fields(
        candidate,
        {"repository", "source_commit", "workflow_run_id", "workflow_run_attempt", "version", "artifact_name"},
        "expected.candidate",
        errors,
    )
    publication_required = {
        "accepted_run_id",
        "accepted_run_attempt",
        "accepted_commit",
        "accepted_artifact_name",
        "accepted_artifact_id",
        "accepted_artifact_service_digest",
        "accepted_version",
        "authorized_actor_id",
        "tag",
        "workflow_ref",
        "environment",
        "trusted_publisher",
        "accepted_artifact_digests",
    }
    if schema_version == PUBLICATION_AUTHORITY_SCHEMA_V3:
        publication_required |= {
            "workflow_execution_commit",
            "workflow_file_sha256",
            "branch_protection_preflight_receipt_sha256",
            "required_checks_policy_digest",
            "observed_branch",
            "observed_at",
            "originating_pr",
            "check_policy",
        }
    exact_publication = _require_exact_fields(
        publication,
        publication_required,
        "expected.publication",
        errors,
    )
    valid = (
        canonical_valid
        and exact_root
        and exact_candidate
        and exact_publication
        and schema_version in {PUBLICATION_AUTHORITY_SCHEMA_VERSION, PUBLICATION_AUTHORITY_SCHEMA_V3}
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
        "accepted_artifact_id",
        "accepted_artifact_service_digest",
        "accepted_version",
        "authorized_actor_id",
        "tag",
        "workflow_ref",
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
    valid = _require_run_id(
        publication.get("accepted_artifact_id"),
        "expected.publication.accepted_artifact_id",
        errors,
    ) and valid
    valid = _require_sha256(
        publication.get("accepted_artifact_service_digest"),
        "expected.publication.accepted_artifact_service_digest",
        errors,
    ) and valid
    valid = _require_run_id(
        publication.get("authorized_actor_id"),
        "expected.publication.authorized_actor_id",
        errors,
    ) and valid
    if not isinstance(publication.get("tag"), str) or not TAG_PATTERN.fullmatch(publication.get("tag", "")):
        _finding(errors, "invalid_expected_authority", "expected.publication.tag", "authority tag must be a final semantic version tag")
        valid = False
    if publication.get("environment") != "pypi":
        _finding(errors, "invalid_expected_authority", "expected.publication.environment", "authority environment must be pypi")
        valid = False
    if schema_version == PUBLICATION_AUTHORITY_SCHEMA_V3:
        required_workflow_ref = "refs/heads/main"
        workflow_ref_message = "authority workflow ref must be refs/heads/main for heads/main dispatch"
    else:
        required_workflow_ref = f"refs/tags/{publication.get('tag')}"
        workflow_ref_message = "authority workflow ref must be the exact immutable release tag"
    if publication.get("workflow_ref") != required_workflow_ref:
        _finding(
            errors,
            "invalid_expected_authority",
            "expected.publication.workflow_ref",
            workflow_ref_message,
        )
        valid = False
    if schema_version == PUBLICATION_AUTHORITY_SCHEMA_V3:
        valid = _require_commit(
            publication.get("workflow_execution_commit"),
            "expected.publication.workflow_execution_commit",
            errors,
        ) and valid
        for digest_field in (
            "workflow_file_sha256",
            "branch_protection_preflight_receipt_sha256",
            "required_checks_policy_digest",
        ):
            valid = _require_sha256(
                publication.get(digest_field),
                f"expected.publication.{digest_field}",
                errors,
            ) and valid
        if publication.get("observed_branch") != "main":
            _finding(errors, "invalid_expected_authority", "expected.publication.observed_branch", "authority observed branch must be main")
            valid = False
        if not isinstance(publication.get("observed_at"), str) or not publication.get("observed_at"):
            _finding(errors, "invalid_expected_authority", "expected.publication.observed_at", "authority observed timestamp is required")
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
        if trusted.get("workflow") != "publish-accepted-release.yml":
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
            if (
                not isinstance(filename, str)
                or not _require_safe_relative(filename, "expected.publication.accepted_artifact_digests", errors)
                or "/" in filename
                or not ASSET_BASENAME_PATTERN.fullmatch(filename)
            ):
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
        expected_artifact_name = _accepted_artifact_name(candidate)
        if candidate.get("artifact_name") != expected_artifact_name:
            _finding(
                errors,
                "invalid_expected_authority",
                "expected.candidate.artifact_name",
                "authority artifact name must bind run id, run attempt, and source commit",
            )
            valid = False
    if schema_version == PUBLICATION_AUTHORITY_SCHEMA_V3:
        originating_pr = _require_mapping(
            publication.get("originating_pr"), "expected.publication.originating_pr", errors
        )
        if originating_pr is None:
            valid = False
        else:
            if not _require_exact_fields(
                originating_pr,
                {"number", "head_sha", "base_sha", "merge_commit_sha", "merge_tree_sha"},
                "expected.publication.originating_pr",
                errors,
            ):
                valid = False
            valid = _require_run_id(
                originating_pr.get("number"), "expected.publication.originating_pr.number", errors
            ) and valid
            for field in ("head_sha", "base_sha", "merge_commit_sha", "merge_tree_sha"):
                valid = _require_commit(
                    originating_pr.get(field), f"expected.publication.originating_pr.{field}", errors
                ) and valid
        check_policy = _require_mapping(
            publication.get("check_policy"), "expected.publication.check_policy", errors
        )
        if check_policy is None:
            valid = False
        else:
            if not _require_exact_fields(
                check_policy,
                {"pr_head_dependency_review", "merge_push_required_checks", "merge_push_dependency_security"},
                "expected.publication.check_policy",
                errors,
            ):
                valid = False
            pr_review = _require_mapping(
                check_policy.get("pr_head_dependency_review"),
                "expected.publication.check_policy.pr_head_dependency_review",
                errors,
            )
            if pr_review is None:
                valid = False
            else:
                if not _require_exact_fields(
                    pr_review,
                    {"run_id", "job_id", "event", "app"},
                    "expected.publication.check_policy.pr_head_dependency_review",
                    errors,
                ):
                    valid = False
                valid = _require_run_id(
                    pr_review.get("run_id"),
                    "expected.publication.check_policy.pr_head_dependency_review.run_id",
                    errors,
                ) and valid
                valid = _require_run_id(
                    pr_review.get("job_id"),
                    "expected.publication.check_policy.pr_head_dependency_review.job_id",
                    errors,
                ) and valid
                for field in ("event", "app"):
                    if not isinstance(pr_review.get(field), str) or not pr_review.get(field):
                        _finding(
                            errors,
                            "invalid_check_policy",
                            f"expected.publication.check_policy.pr_head_dependency_review.{field}",
                            "event-aware policy event/app are required",
                        )
                        valid = False
            merge_security = _require_mapping(
                check_policy.get("merge_push_dependency_security"),
                "expected.publication.check_policy.merge_push_dependency_security",
                errors,
            )
            if merge_security is None:
                valid = False
            else:
                if not _require_exact_fields(
                    merge_security,
                    {"run_id", "event", "app"},
                    "expected.publication.check_policy.merge_push_dependency_security",
                    errors,
                ):
                    valid = False
                valid = _require_run_id(
                    merge_security.get("run_id"),
                    "expected.publication.check_policy.merge_push_dependency_security.run_id",
                    errors,
                ) and valid
                for field in ("event", "app"):
                    if not isinstance(merge_security.get(field), str) or not merge_security.get(field):
                        _finding(
                            errors,
                            "invalid_check_policy",
                            f"expected.publication.check_policy.merge_push_dependency_security.{field}",
                            "event-aware policy event/app are required",
                        )
                        valid = False
            required_checks = check_policy.get("merge_push_required_checks")
            if (
                not isinstance(required_checks, list)
                or not required_checks
                or any(not isinstance(name, str) or not name for name in required_checks)
            ):
                _finding(
                    errors,
                    "invalid_check_policy",
                    "expected.publication.check_policy.merge_push_required_checks",
                    "merge-push required checks must be a non-empty string array",
                )
                valid = False
            elif required_checks != sorted(required_checks) or len(set(required_checks)) != len(required_checks):
                _finding(
                    errors,
                    "invalid_check_policy",
                    "expected.publication.check_policy.merge_push_required_checks",
                    "merge-push required checks must be sorted and unique",
                )
                valid = False
    return candidate, publication, valid


def build_publication_manifests(
    artifact_manifest: Mapping[str, Any],
    *,
    artifact_dir: Path,
    actor_id: str,
    authorized_actor_id: str,
    artifact_id: str,
    artifact_service_digest: str,
    tag: str,
    workflow_ref: str | None = None,
    environment: str,
    trusted_owner: str,
    trusted_repository: str,
    trusted_workflow: str,
    trusted_environment: str,
    workflow_execution_commit: str | None = None,
    workflow_file_sha256: str | None = None,
    branch_protection_preflight_receipt_sha256: str | None = None,
    check_policy_json: str | None = None,
    originating_pr_number: str | int | None = None,
    originating_pr_head_sha: str | None = None,
    originating_pr_base_sha: str | None = None,
    originating_pr_merge_commit_sha: str | None = None,
    originating_pr_merge_tree_sha: str | None = None,
    pr_head_dependency_review_run_id: str | int | None = None,
    pr_head_dependency_review_job_id: str | int | None = None,
    merge_push_dependency_security_run_id: str | int | None = None,
    observed_branch: str | None = None,
    observed_at: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    artifact_result = verify_artifact_manifest(artifact_manifest, artifact_paths=artifact_dir)
    if not artifact_result.ok:
        raise GovernanceInputError(
            f"candidate artifact verification failed: {','.join(artifact_result.codes)}"
        )
    candidate = artifact_manifest.get("candidate")
    if not isinstance(candidate, Mapping):
        raise GovernanceInputError("candidate manifest lacks candidate identity")
    if actor_id != authorized_actor_id or not RUN_ID_PATTERN.fullmatch(actor_id):
        raise GovernanceInputError("authenticated actor does not match the authorized release actor")
    if not RUN_ID_PATTERN.fullmatch(artifact_id):
        raise GovernanceInputError("artifact id must be a positive decimal GitHub artifact id")
    if not SHA256_PATTERN.fullmatch(artifact_service_digest):
        raise GovernanceInputError("artifact service digest must be a SHA-256 value")
    version = candidate.get("version")
    if not isinstance(version, str) or ".dev" in version or tag != f"v{version}":
        raise GovernanceInputError("release tag must match a final candidate version")
    if workflow_ref is None:
        workflow_ref = f"refs/tags/{tag}"
    if workflow_ref not in {f"refs/tags/{tag}", "refs/heads/main"}:
        raise GovernanceInputError("workflow ref must be the exact immutable release tag or refs/heads/main")
    if workflow_ref == "refs/heads/main":
        if not SHA256_PATTERN.fullmatch(workflow_file_sha256 or ""):
            raise GovernanceInputError("workflow file digest is required for heads/main dispatch")
        if not SHA256_PATTERN.fullmatch(branch_protection_preflight_receipt_sha256 or ""):
            raise GovernanceInputError("branch protection preflight receipt digest is required for heads/main dispatch")
        if not COMMIT_PATTERN.fullmatch(workflow_execution_commit or ""):
            raise GovernanceInputError("workflow execution commit must be a full commit sha")
        if observed_branch != "main" or not isinstance(observed_at, str) or not observed_at:
            raise GovernanceInputError("observed branch/timestamp are required for heads/main dispatch")
        policy = parse_check_policy_document(check_policy_json)
        required_checks_policy_digest = sha256_bytes(check_policy_json.encode("utf-8"))
        normalized_pr_number = _as_run_id(originating_pr_number)
        normalized_pr_review_run_id = _as_run_id(pr_head_dependency_review_run_id)
        normalized_pr_review_job_id = _as_run_id(pr_head_dependency_review_job_id)
        normalized_merge_security_run_id = _as_run_id(merge_push_dependency_security_run_id)
        for label, value in (
            ("originating PR number", normalized_pr_number),
            ("PR-head dependency review run id", normalized_pr_review_run_id),
            ("PR-head dependency review job id", normalized_pr_review_job_id),
            ("merge-push dependency security run id", normalized_merge_security_run_id),
        ):
            if not isinstance(value, str) or not RUN_ID_PATTERN.fullmatch(value):
                raise GovernanceInputError(f"{label} must be a positive decimal id")
        for label, value in (
            ("originating PR head sha", originating_pr_head_sha),
            ("originating PR base sha", originating_pr_base_sha),
            ("originating PR merge commit sha", originating_pr_merge_commit_sha),
            ("originating PR merge tree sha", originating_pr_merge_tree_sha),
        ):
            if not COMMIT_PATTERN.fullmatch(value or ""):
                raise GovernanceInputError(f"{label} must be a full commit sha")
        if originating_pr_merge_commit_sha != candidate.get("source_commit"):
            raise GovernanceInputError("originating PR merge commit differs from the candidate source commit")
    if environment != "pypi" or trusted_environment != environment:
        raise GovernanceInputError("publication environment must match the protected pypi environment")
    if trusted_workflow != "publish-accepted-release.yml":
        raise GovernanceInputError("Trusted Publisher workflow filename is not reviewed")
    if candidate.get("repository") != f"{trusted_owner}/{trusted_repository}":
        raise GovernanceInputError("Trusted Publisher owner/repository differs from the candidate repository")

    digests = _expected_artifact_digests(artifact_manifest)
    trusted_publisher = {
        "owner": trusted_owner,
        "repository": trusted_repository,
        "workflow": trusted_workflow,
        "environment": trusted_environment,
    }
    publication = {
        "accepted_run_id": _as_run_id(candidate.get("workflow_run_id")),
        "accepted_run_attempt": _as_run_id(candidate.get("workflow_run_attempt")),
        "accepted_commit": candidate.get("source_commit"),
        "accepted_artifact_name": candidate.get("artifact_name"),
        "accepted_artifact_id": artifact_id,
        "accepted_artifact_service_digest": artifact_service_digest,
        "accepted_version": version,
        "authorized_actor_id": actor_id,
        "tag": tag,
        "workflow_ref": workflow_ref,
        "environment": environment,
        "trusted_publisher": trusted_publisher,
        "accepted_artifact_digests": digests,
    }
    if workflow_ref == "refs/heads/main":
        publication.update(
            {
                "workflow_execution_commit": workflow_execution_commit,
                "workflow_file_sha256": workflow_file_sha256,
                "branch_protection_preflight_receipt_sha256": branch_protection_preflight_receipt_sha256,
                "required_checks_policy_digest": required_checks_policy_digest,
                "observed_branch": observed_branch,
                "observed_at": observed_at,
                "originating_pr": {
                    "number": normalized_pr_number,
                    "head_sha": originating_pr_head_sha,
                    "base_sha": originating_pr_base_sha,
                    "merge_commit_sha": originating_pr_merge_commit_sha,
                    "merge_tree_sha": originating_pr_merge_tree_sha,
                },
                "check_policy": {
                    "pr_head_dependency_review": {
                        "run_id": normalized_pr_review_run_id,
                        "job_id": normalized_pr_review_job_id,
                        "event": policy["pr_head_dependency_review_event"],
                        "app": policy["pr_head_dependency_review_app"],
                    },
                    "merge_push_required_checks": list(policy["merge_push_required_checks"]),
                    "merge_push_dependency_security": {
                        "run_id": normalized_merge_security_run_id,
                        "event": policy["merge_push_dependency_security_event"],
                        "app": policy["merge_push_dependency_security_app"],
                    },
                },
            }
        )
    authority = {
        "schema_version": (
            PUBLICATION_AUTHORITY_SCHEMA_V3
            if workflow_ref == "refs/heads/main"
            else PUBLICATION_AUTHORITY_SCHEMA_VERSION
        ),
        "immutable": True,
        "candidate": dict(candidate),
        "publication": publication,
    }
    activation_body = {
        "mode": "r1_b",
        "enabled": True,
        "publication_authorized": True,
        "authority_manifest_sha256": canonical_digest(authority),
        **publication,
        "build_once": True,
        "rebuild": False,
        "source_artifact_only": True,
        "long_lived_token": False,
    }
    activation = {
        "schema_version": PUBLICATION_SCHEMA_VERSION,
        "activation": activation_body,
    }
    verification = verify_publication_activation(
        activation,
        expected=authority,
        downloaded_manifest=artifact_manifest,
        downloaded_artifact_dir=artifact_dir,
    )
    if not verification.ok:
        raise GovernanceInputError(
            f"generated publication manifests failed verification: {','.join(verification.codes)}"
        )
    return authority, activation


def verify_publication_authority(
    expected: Mapping[str, Any],
    *,
    repository: str,
    actor_id: str,
    accepted_run_id: str,
    accepted_run_attempt: str,
    accepted_commit: str,
    accepted_artifact_name: str,
    accepted_artifact_id: str,
    accepted_artifact_service_digest: str,
    tag: str,
    workflow_ref: str,
    environment: str,
    trusted_owner: str,
    trusted_repository: str,
    trusted_workflow: str,
    trusted_environment: str,
    workflow_execution_commit: str | None = None,
    workflow_file_sha256: str | None = None,
    branch_protection_preflight_receipt_sha256: str | None = None,
    observed_branch: str | None = None,
    observed_at: str | None = None,
    event_evidence: Mapping[str, Any] | None = None,
    check_policy_json: str | None = None,
) -> VerificationResult:
    """Verify a caller-supplied immutable authority before artifact download."""

    errors: list[Finding] = []
    canonical_valid = _check_canonical_input(expected, "expected", errors)
    candidate, publication, authority_valid = _publication_authority_parts(
        expected,
        errors,
        canonical_valid=canonical_valid,
    )
    expected_values = {
        "candidate.repository": (candidate.get("repository"), repository),
        "publication.authorized_actor_id": (publication.get("authorized_actor_id"), actor_id),
        "publication.accepted_run_id": (_as_run_id(publication.get("accepted_run_id")), accepted_run_id),
        "publication.accepted_run_attempt": (
            _as_run_id(publication.get("accepted_run_attempt")),
            accepted_run_attempt,
        ),
        "publication.accepted_commit": (publication.get("accepted_commit"), accepted_commit),
        "publication.accepted_artifact_name": (
            publication.get("accepted_artifact_name"),
            accepted_artifact_name,
        ),
        "publication.accepted_artifact_id": (
            _as_run_id(publication.get("accepted_artifact_id")),
            accepted_artifact_id,
        ),
        "publication.accepted_artifact_service_digest": (
            publication.get("accepted_artifact_service_digest"),
            accepted_artifact_service_digest,
        ),
        "publication.tag": (publication.get("tag"), tag),
        "publication.workflow_ref": (publication.get("workflow_ref"), workflow_ref),
        "publication.environment": (publication.get("environment"), environment),
    }
    if expected.get("schema_version") == PUBLICATION_AUTHORITY_SCHEMA_V3:
        policy_digest = (
            sha256_bytes(check_policy_json.encode("utf-8"))
            if isinstance(check_policy_json, str) and check_policy_json
            else None
        )
        expected_values.update(
            {
                "publication.workflow_execution_commit": (
                    publication.get("workflow_execution_commit"),
                    workflow_execution_commit,
                ),
                "publication.workflow_file_sha256": (
                    publication.get("workflow_file_sha256"),
                    workflow_file_sha256,
                ),
                "publication.branch_protection_preflight_receipt_sha256": (
                    publication.get("branch_protection_preflight_receipt_sha256"),
                    branch_protection_preflight_receipt_sha256,
                ),
                "publication.required_checks_policy_digest": (
                    publication.get("required_checks_policy_digest"),
                    policy_digest,
                ),
                "publication.observed_branch": (publication.get("observed_branch"), observed_branch),
                "publication.observed_at": (publication.get("observed_at"), observed_at),
            }
        )
    trusted = publication.get("trusted_publisher")
    if isinstance(trusted, Mapping):
        expected_values.update(
            {
                "publication.trusted_publisher.owner": (trusted.get("owner"), trusted_owner),
                "publication.trusted_publisher.repository": (
                    trusted.get("repository"),
                    trusted_repository,
                ),
                "publication.trusted_publisher.workflow": (
                    trusted.get("workflow"),
                    trusted_workflow,
                ),
                "publication.trusted_publisher.environment": (
                    trusted.get("environment"),
                    trusted_environment,
                ),
            }
        )
    if expected.get("schema_version") == PUBLICATION_AUTHORITY_SCHEMA_V3:
        if event_evidence is None:
            _finding(
                errors,
                "missing_event_evidence",
                "event_evidence",
                "event-aware v3 verification requires independently collected GitHub evidence",
            )
            authority_valid = False
        elif not isinstance(event_evidence, Mapping):
            _finding(errors, "invalid_event_evidence", "event_evidence", "event-aware evidence must be a JSON object")
            authority_valid = False
        policy = None
        if check_policy_json is None:
            _finding(
                errors,
                "missing_check_policy",
                "check_policy",
                "event-aware v3 verification requires the frozen check policy document",
            )
            authority_valid = False
        else:
            if (
                not isinstance(check_policy_json, str)
                or sha256_bytes(check_policy_json.encode("utf-8")) != publication.get("required_checks_policy_digest")
            ):
                _finding(
                    errors,
                    "check_policy_digest_mismatch",
                    "check_policy",
                    "check policy digest does not match the external authority",
                )
                authority_valid = False
            try:
                policy = parse_check_policy_document(check_policy_json)
            except GovernanceInputError as error:
                _finding(errors, "invalid_check_policy", "check_policy", str(error))
                authority_valid = False
        evidence = None
        if isinstance(event_evidence, Mapping):
            evidence = _validated_publication_check_evidence(event_evidence, errors)
            if evidence is None:
                authority_valid = False
            elif evidence.get("repository") != repository or evidence.get("accepted_commit") != publication.get("accepted_commit"):
                _finding(
                    errors,
                    "event_evidence_context_mismatch",
                    "event_evidence",
                    "evidence repository or accepted commit differs from the authenticated context",
                )
                authority_valid = False
        if policy is not None and evidence is not None:
            check_policy = publication.get("check_policy")
            if isinstance(check_policy, Mapping):
                pr_bound = check_policy.get("pr_head_dependency_review")
                merge_bound = check_policy.get("merge_push_dependency_security")
                if (
                    isinstance(pr_bound, Mapping)
                    and (
                        pr_bound.get("event") != policy.get("pr_head_dependency_review_event")
                        or pr_bound.get("app") != policy.get("pr_head_dependency_review_app")
                    )
                ):
                    _finding(
                        errors,
                        "check_policy_mismatch",
                        "expected.publication.check_policy.pr_head_dependency_review",
                        "authority PR-head event/app differ from the frozen check policy document",
                    )
                    authority_valid = False
                if (
                    isinstance(merge_bound, Mapping)
                    and (
                        merge_bound.get("event") != policy.get("merge_push_dependency_security_event")
                        or merge_bound.get("app") != policy.get("merge_push_dependency_security_app")
                    )
                ):
                    _finding(
                        errors,
                        "check_policy_mismatch",
                        "expected.publication.check_policy.merge_push_dependency_security",
                        "authority merge-push event/app differ from the frozen check policy document",
                    )
                    authority_valid = False
                if check_policy.get("merge_push_required_checks") != policy.get("merge_push_required_checks"):
                    _finding(
                        errors,
                        "check_policy_mismatch",
                        "expected.publication.check_policy.merge_push_required_checks",
                        "authority required checks differ from the frozen check policy document",
                    )
                    authority_valid = False
            _verify_event_aware_checks(publication, policy, evidence, errors)
    for path, (actual, required) in expected_values.items():
        if actual != required:
            _finding(errors, "authority_context_mismatch", f"expected.{path}", "authority differs from authenticated dispatch context")
            authority_valid = False
    return _finish(
        errors,
        {
            "authority_manifest_sha256": canonical_digest(expected),
            "accepted_commit": publication.get("accepted_commit"),
            "accepted_artifact_id": publication.get("accepted_artifact_id"),
            "authority_context_checked": authority_valid and not errors,
        },
    )


def build_publication_activation(
    authority: Mapping[str, Any],
    *,
    artifact_manifest: Mapping[str, Any],
    artifact_dir: Path,
) -> dict[str, Any]:
    """Create activation only after external authority and downloaded bytes agree."""

    errors: list[Finding] = []
    _, publication, authority_valid = _publication_authority_parts(authority, errors)
    if not authority_valid or errors:
        raise GovernanceInputError(
            f"external publication authority is invalid: {','.join(_finish(errors).codes)}"
        )
    activation = {
        "schema_version": PUBLICATION_SCHEMA_VERSION,
        "activation": {
            "mode": "r1_b",
            "enabled": True,
            "publication_authorized": True,
            "authority_manifest_sha256": canonical_digest(authority),
            **dict(publication),
            "build_once": True,
            "rebuild": False,
            "source_artifact_only": True,
            "long_lived_token": False,
        },
    }
    verification = verify_publication_activation(
        activation,
        expected=authority,
        downloaded_manifest=artifact_manifest,
        downloaded_artifact_dir=artifact_dir,
    )
    if not verification.ok:
        raise GovernanceInputError(
            f"publication activation failed verification: {','.join(verification.codes)}"
        )
    return activation


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
    _require_exact_fields(root, {"schema_version", "activation"}, "activation", errors)
    if root.get("schema_version") != PUBLICATION_SCHEMA_VERSION:
        _finding(errors, "unsupported_schema", "activation.schema_version", "publication activation schema is unsupported")
    body = _require_mapping(root.get("activation"), "activation.activation", errors)
    if body is None:
        body = {}
    else:
        activation_required = {
            "mode",
            "enabled",
            "publication_authorized",
            "authority_manifest_sha256",
            "accepted_run_id",
            "accepted_run_attempt",
            "accepted_commit",
            "accepted_artifact_name",
            "accepted_artifact_id",
            "accepted_artifact_service_digest",
            "accepted_version",
            "authorized_actor_id",
            "tag",
            "workflow_ref",
            "environment",
            "trusted_publisher",
            "build_once",
            "rebuild",
            "source_artifact_only",
            "long_lived_token",
            "accepted_artifact_digests",
        }
        if expected is not None and expected.get("schema_version") == PUBLICATION_AUTHORITY_SCHEMA_V3:
            activation_required |= {
                "workflow_execution_commit",
                "workflow_file_sha256",
                "branch_protection_preflight_receipt_sha256",
                "required_checks_policy_digest",
                "observed_branch",
                "observed_at",
                "originating_pr",
                "check_policy",
            }
        _require_exact_fields(
            body,
            activation_required,
            "activation.activation",
            errors,
        )
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
    _require_run_id(body.get("accepted_artifact_id"), "activation.activation.accepted_artifact_id", errors)
    _require_sha256(
        body.get("accepted_artifact_service_digest"),
        "activation.activation.accepted_artifact_service_digest",
        errors,
    )
    _require_run_id(body.get("authorized_actor_id"), "activation.activation.authorized_actor_id", errors)
    if not isinstance(body.get("accepted_version"), str) or not body.get("accepted_version"):
        _finding(errors, "missing_identity", "activation.activation.accepted_version", "accepted package version is required")
    if not isinstance(body.get("tag"), str) or not TAG_PATTERN.fullmatch(body.get("tag", "")):
        _finding(errors, "mutable_tag", "activation.activation.tag", "publication tag must be an immutable semantic version tag")
    if body.get("environment") != "pypi":
        _finding(errors, "wrong_environment", "activation.activation.environment", "publication must use the protected pypi environment")
    expected_authority_schema = expected.get("schema_version") if isinstance(expected, Mapping) else None
    if expected_authority_schema == PUBLICATION_AUTHORITY_SCHEMA_V3:
        required_workflow_ref = "refs/heads/main"
        workflow_ref_message = "publication must run from refs/heads/main"
    else:
        required_workflow_ref = f"refs/tags/{body.get('tag')}"
        workflow_ref_message = "publication must run from the exact release tag"
    if body.get("workflow_ref") != required_workflow_ref:
        _finding(errors, "wrong_workflow_ref", "activation.activation.workflow_ref", workflow_ref_message)
    trusted = _require_mapping(body.get("trusted_publisher"), "activation.activation.trusted_publisher", errors)
    if trusted is None:
        trusted = {}
    for field in ("owner", "repository", "workflow", "environment"):
        if not isinstance(trusted.get(field), str) or not trusted.get(field):
            _finding(errors, "missing_trusted_publisher", f"activation.activation.trusted_publisher.{field}", "trusted publisher identity is required")
    if trusted.get("workflow") != "publish-accepted-release.yml":
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
            if (
                not _require_safe_relative(filename, "activation.activation.accepted_artifact_digests", errors)
                or "/" in filename
                or not ASSET_BASENAME_PATTERN.fullmatch(filename)
            ):
                _finding(
                    errors,
                    "unsafe_artifact_basename",
                    "activation.activation.accepted_artifact_digests",
                    "publication artifact names must be safe basenames",
                )
            _require_sha256(digest, f"activation.activation.accepted_artifact_digests.{filename}", errors)

    expected_run_id = expected_candidate.get("workflow_run_id", expected_publication.get("accepted_run_id"))
    expected_run_attempt = expected_candidate.get("workflow_run_attempt", expected_publication.get("accepted_run_attempt"))
    expected_commit = expected_candidate.get("source_commit", expected_publication.get("accepted_commit"))
    for field, code in (
        ("accepted_run_id", "wrong_run"),
        ("accepted_run_attempt", "wrong_run_attempt"),
        ("accepted_commit", "wrong_commit"),
        ("accepted_artifact_name", "wrong_artifact_name"),
        ("accepted_artifact_id", "wrong_artifact_id"),
        ("accepted_artifact_service_digest", "wrong_artifact_service_digest"),
        ("accepted_version", "version_mismatch"),
        ("authorized_actor_id", "wrong_actor"),
        ("tag", "wrong_tag"),
        ("workflow_ref", "wrong_workflow_ref"),
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
            if field in {"accepted_run_id", "accepted_run_attempt", "accepted_artifact_id"}:
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
    if expected_authority_schema == PUBLICATION_AUTHORITY_SCHEMA_V3:
        for field in ("originating_pr", "check_policy"):
            expected_value = expected_publication.get(field)
            actual_value = body.get(field)
            if isinstance(expected_value, Mapping) and actual_value != expected_value:
                _finding(
                    errors,
                    f"wrong_{field}",
                    f"activation.activation.{field}",
                    "activation does not match the accepted event-aware policy",
                )
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


PUBLICATION_CHECK_EVIDENCE_FIELDS = {
    "schema",
    "repository",
    "accepted_commit",
    "merge_commit_tree_sha",
    "candidate_pull_requests",
    "pr_head_workflow_runs_total",
    "pr_head_workflow_runs",
    "pr_head_workflow_jobs",
    "pr_head_check_runs_total",
    "pr_head_check_runs",
    "merge_push_workflow_runs_total",
    "merge_push_workflow_runs",
    "merge_push_workflow_jobs",
    "merge_push_check_runs_total",
    "merge_push_check_runs",
}
PR_EVIDENCE_FIELDS = {
    "number",
    "state",
    "merged",
    "base_ref",
    "head_sha",
    "base_sha",
    "merge_commit_sha",
    "merged_at",
}
RUN_EVIDENCE_FIELDS = {
    "id",
    "name",
    "path",
    "event",
    "status",
    "conclusion",
    "head_sha",
    "head_branch",
}
JOB_EVIDENCE_FIELDS = {"id", "run_id", "name", "status", "conclusion"}
CHECK_RUN_EVIDENCE_FIELDS = {"id", "name", "status", "conclusion", "head_sha", "app"}


def parse_check_policy_document(policy_json: str) -> dict[str, Any]:
    """Parse and validate the frozen event-aware check policy document."""

    if not isinstance(policy_json, str) or not policy_json:
        raise GovernanceInputError("event-aware check policy document is required")
    try:
        value = _load_json_bytes(policy_json.encode("utf-8"))
    except (UnicodeError, json.JSONDecodeError, GovernanceInputError) as error:
        raise GovernanceInputError(f"event-aware check policy document is malformed: {error}") from error
    if not isinstance(value, Mapping):
        raise GovernanceInputError("event-aware check policy document must be a JSON object")
    if set(value) != EVENT_AWARE_CHECK_POLICY_FIELDS:
        raise GovernanceInputError("event-aware check policy document fields are not canonical")
    string_fields = EVENT_AWARE_CHECK_POLICY_FIELDS - {"merge_push_required_checks"}
    for field in string_fields:
        item = value.get(field)
        if not isinstance(item, str) or not item:
            raise GovernanceInputError(f"event-aware check policy field is invalid: {field}")
    if value.get("dependency_security_workflow_path") != DEPENDENCY_SECURITY_WORKFLOW_PATH:
        raise GovernanceInputError("event-aware check policy binds an unreviewed dependency workflow path")
    if value.get("originating_pr_base_ref") != "main":
        raise GovernanceInputError("event-aware check policy must bind the main base branch")
    if (
        value.get("pr_head_dependency_review_event") != "pull_request"
        or value.get("pr_head_dependency_review_app") != "github-actions"
    ):
        raise GovernanceInputError("PR-head dependency review must bind pull_request and github-actions")
    if (
        value.get("merge_push_dependency_security_event") != "push"
        or value.get("merge_push_dependency_security_app") != "github-actions"
    ):
        raise GovernanceInputError("merge-push dependency security must bind push and github-actions")
    checks = value.get("merge_push_required_checks")
    if not isinstance(checks, list) or not checks or any(not isinstance(name, str) or not name for name in checks):
        raise GovernanceInputError("merge-push required checks must be a non-empty string array")
    if checks != sorted(checks) or len(set(checks)) != len(checks):
        raise GovernanceInputError("merge-push required checks must be sorted and unique")
    return value


def _evidence_run_id(value: Any, path: str) -> str:
    normalized = _as_run_id(value)
    if not isinstance(normalized, str) or not RUN_ID_PATTERN.fullmatch(normalized):
        raise GovernanceInputError(f"GitHub evidence field is not a positive id: {path}")
    return normalized


def _evidence_commit(value: Any, path: str) -> str:
    if not isinstance(value, str) or not COMMIT_PATTERN.fullmatch(value):
        raise GovernanceInputError(f"GitHub evidence field is not a full commit sha: {path}")
    return value


def _evidence_string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value:
        raise GovernanceInputError(f"GitHub evidence field is missing or invalid: {path}")
    return value


def _evidence_optional_string(value: Any, path: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise GovernanceInputError(f"GitHub evidence field is not a string: {path}")
    return value


def _normalize_evidence_pull(item: Any, path: str) -> dict[str, Any]:
    if not isinstance(item, Mapping):
        raise GovernanceInputError(f"GitHub evidence entry is not an object: {path}")
    base = item.get("base")
    head = item.get("head")
    if not isinstance(base, Mapping) or not isinstance(head, Mapping):
        raise GovernanceInputError(f"GitHub pull evidence lacks head/base objects: {path}")
    raw_merged = item.get("merged")
    if isinstance(raw_merged, bool):
        merged = raw_merged
    else:
        # The commits/{sha}/pulls endpoint omits the PR-detail-only `merged`
        # boolean; derive it from the real closed state plus a real merge
        # commit sha.  The verifier still cross-checks merge_commit_sha
        # against the bound accepted commit.
        merged = item.get("state") == "closed" and isinstance(item.get("merge_commit_sha"), str) and bool(item.get("merge_commit_sha"))
    return {
        "number": _evidence_run_id(item.get("number"), f"{path}.number"),
        "state": _evidence_string(item.get("state"), f"{path}.state"),
        "merged": merged,
        "base_ref": _evidence_string(base.get("ref"), f"{path}.base.ref"),
        "head_sha": _evidence_commit(head.get("sha"), f"{path}.head.sha"),
        "base_sha": _evidence_commit(base.get("sha"), f"{path}.base.sha"),
        "merge_commit_sha": _evidence_commit(item.get("merge_commit_sha"), f"{path}.merge_commit_sha"),
        "merged_at": _evidence_string(item.get("merged_at"), f"{path}.merged_at"),
    }


def _normalize_evidence_run(item: Any, path: str) -> dict[str, Any]:
    if not isinstance(item, Mapping):
        raise GovernanceInputError(f"GitHub evidence entry is not an object: {path}")
    return {
        "id": _evidence_run_id(item.get("id"), f"{path}.id"),
        "name": _evidence_string(item.get("name"), f"{path}.name"),
        "path": _evidence_string(item.get("path"), f"{path}.path"),
        "event": _evidence_string(item.get("event"), f"{path}.event"),
        "status": _evidence_string(item.get("status"), f"{path}.status"),
        "conclusion": _evidence_optional_string(item.get("conclusion"), f"{path}.conclusion"),
        "head_sha": _evidence_commit(item.get("head_sha"), f"{path}.head_sha"),
        "head_branch": _evidence_string(item.get("head_branch"), f"{path}.head_branch"),
    }


def _normalize_evidence_job(item: Any, path: str) -> dict[str, Any]:
    if not isinstance(item, Mapping):
        raise GovernanceInputError(f"GitHub evidence entry is not an object: {path}")
    return {
        "id": _evidence_run_id(item.get("id"), f"{path}.id"),
        "run_id": _evidence_run_id(item.get("run_id"), f"{path}.run_id"),
        "name": _evidence_string(item.get("name"), f"{path}.name"),
        "status": _evidence_string(item.get("status"), f"{path}.status"),
        "conclusion": _evidence_optional_string(item.get("conclusion"), f"{path}.conclusion"),
    }


def _normalize_evidence_check_run(item: Any, path: str) -> dict[str, Any]:
    if not isinstance(item, Mapping):
        raise GovernanceInputError(f"GitHub evidence entry is not an object: {path}")
    app = item.get("app")
    app_slug = "" if app is None else app.get("slug") if isinstance(app, Mapping) else None
    if not isinstance(app_slug, str):
        raise GovernanceInputError(f"GitHub check-run evidence lacks an app slug: {path}")
    return {
        "id": _evidence_run_id(item.get("id"), f"{path}.id"),
        "name": _evidence_string(item.get("name"), f"{path}.name"),
        "status": _evidence_string(item.get("status"), f"{path}.status"),
        "conclusion": _evidence_optional_string(item.get("conclusion"), f"{path}.conclusion"),
        "head_sha": _evidence_commit(item.get("head_sha"), f"{path}.head_sha"),
        "app": app_slug,
    }


def _read_evidence_file(root: Path, name: str) -> Any:
    path = root / name
    if not path.is_file() or path.is_symlink():
        raise GovernanceInputError(f"unresolvable GitHub evidence file: {name}")
    try:
        return _load_json_bytes(path.read_bytes())
    except (OSError, UnicodeError, json.JSONDecodeError, GovernanceInputError) as error:
        raise GovernanceInputError(f"malformed GitHub evidence file {name}: {error}") from error


def _get_github_json(url: str, token: str | None) -> Any:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "release-governance",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token is not None:
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read()
    except urllib.error.HTTPError as error:
        raise GovernanceInputError(f"unresolvable GitHub evidence endpoint (HTTP {error.code}): {url}") from error
    except (OSError, TimeoutError) as error:
        raise GovernanceInputError(f"unresolvable GitHub evidence endpoint: {url}") from error
    try:
        return _load_json_bytes(raw)
    except (UnicodeError, json.JSONDecodeError, GovernanceInputError) as error:
        raise GovernanceInputError(f"malformed GitHub evidence response: {url}") from error


def collect_publication_check_evidence(
    repository: str,
    *,
    github_token: str | None,
    accepted_commit: str,
    evidence_root: Path | None = None,
) -> dict[str, Any]:
    """Collect the independent GitHub evidence required by schema v3.

    Live mode queries api.github.com with the caller token.  ``evidence_root``
    switches to an offline reader over raw GitHub API JSON captures with the
    exact same response shapes; it never fabricates or mocks values.
    """

    if not isinstance(repository, str) or re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) is None:
        raise GovernanceInputError("evidence repository identity is invalid")
    if not COMMIT_PATTERN.fullmatch(accepted_commit):
        raise GovernanceInputError("evidence accepted commit must be a full commit sha")
    if github_token is None and evidence_root is None:
        raise GovernanceInputError("live evidence collection requires a GitHub token")

    def load(endpoint: str, offline_name: str, *, key: str | None) -> tuple[list[Any], int]:
        if evidence_root is not None:
            payload = _read_evidence_file(evidence_root, offline_name)
            if key is None:
                if not isinstance(payload, list):
                    raise GovernanceInputError(f"GitHub evidence endpoint returned a non-array: {offline_name}")
                return payload, len(payload)
            if not isinstance(payload, Mapping):
                raise GovernanceInputError(f"GitHub evidence endpoint returned a non-object: {offline_name}")
            items = payload.get(key)
            total = payload.get("total_count")
            if not isinstance(items, list) or not isinstance(total, int) or isinstance(total, bool) or total < 0:
                raise GovernanceInputError(f"GitHub evidence endpoint is malformed: {offline_name}")
            if len(items) != total:
                raise GovernanceInputError(f"GitHub evidence page truncation: {offline_name} {len(items)}/{total}")
            return items, total

        separator = "&" if "?" in endpoint else "?"
        base = f"https://api.github.com/repos/{repository}/{endpoint}{separator}per_page=100"
        if key is None:
            collected: list[Any] = []
            for page in range(1, 101):
                payload = _get_github_json(f"{base}&page={page}", github_token)
                if not isinstance(payload, list):
                    raise GovernanceInputError(f"GitHub evidence endpoint returned a non-array: {offline_name}")
                collected.extend(payload)
                if len(payload) < 100:
                    break
            else:
                raise GovernanceInputError(f"GitHub evidence pagination overflow: {offline_name}")
            return collected, len(collected)
        collected = []
        total: int | None = None
        for page in range(1, 101):
            payload = _get_github_json(f"{base}&page={page}", github_token)
            if not isinstance(payload, Mapping):
                raise GovernanceInputError(f"GitHub evidence endpoint returned a non-object: {offline_name}")
            batch = payload.get(key)
            reported = payload.get("total_count")
            if not isinstance(batch, list) or not isinstance(reported, int) or isinstance(reported, bool) or reported < 0:
                raise GovernanceInputError(f"GitHub evidence endpoint is malformed: {offline_name}")
            if total is not None and reported != total:
                raise GovernanceInputError(f"GitHub evidence total changed between pages: {offline_name}")
            total = reported
            collected.extend(batch)
            if len(collected) >= total or not batch:
                break
        if total is None or len(collected) != total:
            raise GovernanceInputError(f"GitHub evidence page truncation: {offline_name} {len(collected)}/{total}")
        return collected, total

    pulls_raw, _ = load(f"commits/{accepted_commit}/pulls", "commits-pulls.json", key=None)
    pulls = [_normalize_evidence_pull(item, f"pulls[{index}]") for index, item in enumerate(pulls_raw)]

    if evidence_root is not None:
        commit_raw = _read_evidence_file(evidence_root, "commit-merge.json")
    else:
        commit_raw = _get_github_json(
            f"https://api.github.com/repos/{repository}/commits/{accepted_commit}", github_token
        )
    if not isinstance(commit_raw, Mapping) or commit_raw.get("sha") != accepted_commit:
        raise GovernanceInputError("GitHub commit evidence does not name the accepted commit")
    commit_value = commit_raw.get("commit")
    tree = commit_value.get("tree") if isinstance(commit_value, Mapping) else None
    tree_sha = tree.get("sha") if isinstance(tree, Mapping) else None
    if not isinstance(tree_sha, str) or not COMMIT_PATTERN.fullmatch(tree_sha):
        raise GovernanceInputError("GitHub commit evidence lacks the accepted commit tree sha")

    if len(pulls) == 1:
        pr_head = pulls[0]["head_sha"]
        pr_runs_raw, pr_runs_total = load(
            f"actions/runs?head_sha={pr_head}&event=pull_request", "runs-pr-head.json", key="workflow_runs"
        )
        pr_checks_raw, pr_checks_total = load(
            f"commits/{pr_head}/check-runs", "check-runs-pr-head.json", key="check_runs"
        )
    else:
        pr_runs_raw, pr_runs_total = [], 0
        pr_checks_raw, pr_checks_total = [], 0
    pr_runs = [_normalize_evidence_run(item, f"pr-head-runs[{index}]") for index, item in enumerate(pr_runs_raw)]
    pr_checks = [_normalize_evidence_check_run(item, f"pr-head-check-runs[{index}]") for index, item in enumerate(pr_checks_raw)]

    merge_runs_raw, merge_runs_total = load(
        f"actions/runs?head_sha={accepted_commit}&event=push", "runs-merge-push.json", key="workflow_runs"
    )
    merge_checks_raw, merge_checks_total = load(
        f"commits/{accepted_commit}/check-runs", "check-runs-merge.json", key="check_runs"
    )
    merge_runs = [_normalize_evidence_run(item, f"merge-push-runs[{index}]") for index, item in enumerate(merge_runs_raw)]
    merge_checks = [
        _normalize_evidence_check_run(item, f"merge-push-check-runs[{index}]") for index, item in enumerate(merge_checks_raw)
    ]

    pr_jobs: dict[str, list[dict[str, Any]]] = {}
    for run in pr_runs:
        if run["path"] != DEPENDENCY_SECURITY_WORKFLOW_PATH:
            continue
        jobs_raw, _ = load(f"actions/runs/{run['id']}/jobs", f"jobs-{run['id']}.json", key="jobs")
        if run["id"] in pr_jobs:
            raise GovernanceInputError(f"duplicate PR-head dependency-security run evidence: {run['id']}")
        pr_jobs[run["id"]] = [_normalize_evidence_job(item, f"jobs-{run['id']}[{index}]") for index, item in enumerate(jobs_raw)]
    merge_jobs: dict[str, list[dict[str, Any]]] = {}
    for run in merge_runs:
        if run["path"] != DEPENDENCY_SECURITY_WORKFLOW_PATH:
            continue
        jobs_raw, _ = load(f"actions/runs/{run['id']}/jobs", f"jobs-{run['id']}.json", key="jobs")
        if run["id"] in merge_jobs:
            raise GovernanceInputError(f"duplicate merge-push dependency-security run evidence: {run['id']}")
        merge_jobs[run["id"]] = [_normalize_evidence_job(item, f"jobs-{run['id']}[{index}]") for index, item in enumerate(jobs_raw)]

    return {
        "schema": PUBLICATION_CHECK_EVIDENCE_SCHEMA_VERSION,
        "repository": repository,
        "accepted_commit": accepted_commit,
        "merge_commit_tree_sha": tree_sha,
        "candidate_pull_requests": pulls,
        "pr_head_workflow_runs_total": pr_runs_total,
        "pr_head_workflow_runs": pr_runs,
        "pr_head_workflow_jobs": pr_jobs,
        "pr_head_check_runs_total": pr_checks_total,
        "pr_head_check_runs": pr_checks,
        "merge_push_workflow_runs_total": merge_runs_total,
        "merge_push_workflow_runs": merge_runs,
        "merge_push_workflow_jobs": merge_jobs,
        "merge_push_check_runs_total": merge_checks_total,
        "merge_push_check_runs": merge_checks,
    }


def _require_evidence_total(value: Any, expected: int, path: str, errors: list[Finding]) -> bool:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        _finding(errors, "invalid_event_evidence", path, "expected a non-negative evidence total")
        return False
    if value != expected:
        _finding(errors, "event_evidence_truncated", path, f"evidence count {expected} does not match total {value}")
        return False
    return True


def _validated_evidence_entries(
    value: Any, path: str, fields: set[str], errors: list[Finding]
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        _finding(errors, "invalid_event_evidence", path, "expected an evidence array")
        return []
    entries: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        item_path = f"{path}[{index}]"
        mapping = _require_mapping(item, item_path, errors)
        if mapping is None:
            continue
        if not _require_exact_fields(mapping, fields, item_path, errors):
            continue
        entries.append(dict(mapping))
    return entries


def _validated_publication_check_evidence(
    value: Any, errors: list[Finding]
) -> Mapping[str, Any] | None:
    start_errors = len(errors)
    root = _require_mapping(value, "event_evidence", errors)
    if root is None:
        _finding(errors, "invalid_event_evidence", "event_evidence", "event-aware evidence must be a JSON object")
        return None
    structural_ok = _require_exact_fields(root, PUBLICATION_CHECK_EVIDENCE_FIELDS, "event_evidence", errors)
    if root.get("schema") != PUBLICATION_CHECK_EVIDENCE_SCHEMA_VERSION:
        _finding(errors, "unsupported_schema", "event_evidence.schema", "publication check evidence schema is unsupported")
        structural_ok = False
    if not isinstance(root.get("repository"), str) or not root.get("repository"):
        _finding(errors, "invalid_event_evidence", "event_evidence.repository", "evidence repository is required")
        structural_ok = False
    _require_commit(root.get("accepted_commit"), "event_evidence.accepted_commit", errors)
    _require_commit(root.get("merge_commit_tree_sha"), "event_evidence.merge_commit_tree_sha", errors)
    if not structural_ok:
        return None

    pulls = _validated_evidence_entries(
        root.get("candidate_pull_requests"), "event_evidence.candidate_pull_requests", PR_EVIDENCE_FIELDS, errors
    )
    for index, pull in enumerate(pulls):
        path = f"event_evidence.candidate_pull_requests[{index}]"
        _require_run_id(pull.get("number"), f"{path}.number", errors)
        for field in ("head_sha", "base_sha", "merge_commit_sha"):
            _require_commit(pull.get(field), f"{path}.{field}", errors)
        for field in ("state", "base_ref", "merged_at"):
            if not isinstance(pull.get(field), str) or not pull.get(field):
                _finding(errors, "invalid_event_evidence", f"{path}.{field}", "pull identity field is required")
        if pull.get("merged") is not True:
            _finding(errors, "invalid_event_evidence", f"{path}.merged", "pull evidence must be merged")

    pr_runs = _validated_evidence_entries(
        root.get("pr_head_workflow_runs"), "event_evidence.pr_head_workflow_runs", RUN_EVIDENCE_FIELDS, errors
    )
    for index, run in enumerate(pr_runs):
        path = f"event_evidence.pr_head_workflow_runs[{index}]"
        _require_run_id(run.get("id"), f"{path}.id", errors)
        _require_commit(run.get("head_sha"), f"{path}.head_sha", errors)
        for field in ("name", "path", "event", "status", "head_branch"):
            if not isinstance(run.get(field), str) or not run.get(field):
                _finding(errors, "invalid_event_evidence", f"{path}.{field}", "run identity field is required")
        if not isinstance(run.get("conclusion"), str):
            _finding(errors, "invalid_event_evidence", f"{path}.conclusion", "run conclusion must be a string")
    merge_runs = _validated_evidence_entries(
        root.get("merge_push_workflow_runs"), "event_evidence.merge_push_workflow_runs", RUN_EVIDENCE_FIELDS, errors
    )
    for index, run in enumerate(merge_runs):
        path = f"event_evidence.merge_push_workflow_runs[{index}]"
        _require_run_id(run.get("id"), f"{path}.id", errors)
        _require_commit(run.get("head_sha"), f"{path}.head_sha", errors)
        for field in ("name", "path", "event", "status", "head_branch"):
            if not isinstance(run.get(field), str) or not run.get(field):
                _finding(errors, "invalid_event_evidence", f"{path}.{field}", "run identity field is required")
        if not isinstance(run.get("conclusion"), str):
            _finding(errors, "invalid_event_evidence", f"{path}.conclusion", "run conclusion must be a string")

    def validated_jobs(value: Any, path: str) -> dict[str, list[dict[str, Any]]]:
        mapping = _require_mapping(value, path, errors)
        if mapping is None:
            return {}
        result: dict[str, list[dict[str, Any]]] = {}
        for run_id, items in mapping.items():
            if not isinstance(run_id, str) or not RUN_ID_PATTERN.fullmatch(run_id):
                _finding(errors, "invalid_event_evidence", f"{path}.<key>", "job map keys must be run ids")
                continue
            entries = _validated_evidence_entries(items, f"{path}.{run_id}", JOB_EVIDENCE_FIELDS, errors)
            for index, job in enumerate(entries):
                item_path = f"{path}.{run_id}[{index}]"
                _require_run_id(job.get("id"), f"{item_path}.id", errors)
                _require_run_id(job.get("run_id"), f"{item_path}.run_id", errors)
                for field in ("name", "status"):
                    if not isinstance(job.get(field), str) or not job.get(field):
                        _finding(errors, "invalid_event_evidence", f"{item_path}.{field}", "job identity field is required")
                if not isinstance(job.get("conclusion"), str):
                    _finding(errors, "invalid_event_evidence", f"{item_path}.conclusion", "job conclusion must be a string")
            result[run_id] = entries
        return result

    pr_jobs = validated_jobs(root.get("pr_head_workflow_jobs"), "event_evidence.pr_head_workflow_jobs")
    merge_jobs = validated_jobs(root.get("merge_push_workflow_jobs"), "event_evidence.merge_push_workflow_jobs")

    def validated_check_runs(value: Any, path: str) -> list[dict[str, Any]]:
        entries = _validated_evidence_entries(value, path, CHECK_RUN_EVIDENCE_FIELDS, errors)
        for index, check in enumerate(entries):
            item_path = f"{path}[{index}]"
            _require_run_id(check.get("id"), f"{item_path}.id", errors)
            _require_commit(check.get("head_sha"), f"{item_path}.head_sha", errors)
            for field in ("name", "status", "app"):
                if not isinstance(check.get(field), str) or not check.get(field):
                    _finding(errors, "invalid_event_evidence", f"{item_path}.{field}", "check-run identity field is required")
            if not isinstance(check.get("conclusion"), str):
                _finding(errors, "invalid_event_evidence", f"{item_path}.conclusion", "check-run conclusion must be a string")
        return entries

    pr_checks = validated_check_runs(root.get("pr_head_check_runs"), "event_evidence.pr_head_check_runs")
    merge_checks = validated_check_runs(root.get("merge_push_check_runs"), "event_evidence.merge_push_check_runs")

    _require_evidence_total(
        root.get("pr_head_workflow_runs_total"), len(pr_runs), "event_evidence.pr_head_workflow_runs_total", errors
    )
    _require_evidence_total(
        root.get("pr_head_check_runs_total"), len(pr_checks), "event_evidence.pr_head_check_runs_total", errors
    )
    _require_evidence_total(
        root.get("merge_push_workflow_runs_total"), len(merge_runs), "event_evidence.merge_push_workflow_runs_total", errors
    )
    _require_evidence_total(
        root.get("merge_push_check_runs_total"), len(merge_checks), "event_evidence.merge_push_check_runs_total", errors
    )
    if len(errors) > start_errors:
        return None
    return {
        "schema": root.get("schema"),
        "repository": root.get("repository"),
        "accepted_commit": root.get("accepted_commit"),
        "merge_commit_tree_sha": root.get("merge_commit_tree_sha"),
        "candidate_pull_requests": pulls,
        "pr_head_workflow_runs_total": root.get("pr_head_workflow_runs_total"),
        "pr_head_workflow_runs": pr_runs,
        "pr_head_workflow_jobs": pr_jobs,
        "pr_head_check_runs_total": root.get("pr_head_check_runs_total"),
        "pr_head_check_runs": pr_checks,
        "merge_push_workflow_runs_total": root.get("merge_push_workflow_runs_total"),
        "merge_push_workflow_runs": merge_runs,
        "merge_push_workflow_jobs": merge_jobs,
        "merge_push_check_runs_total": root.get("merge_push_check_runs_total"),
        "merge_push_check_runs": merge_checks,
    }


def _verify_event_aware_checks(
    publication: Mapping[str, Any],
    policy: Mapping[str, Any],
    evidence: Mapping[str, Any],
    errors: list[Finding],
) -> None:
    originating = publication.get("originating_pr")
    check_policy = publication.get("check_policy")
    if not isinstance(originating, Mapping) or not isinstance(check_policy, Mapping):
        return
    pr_bound = check_policy.get("pr_head_dependency_review")
    merge_bound = check_policy.get("merge_push_dependency_security")
    if not isinstance(pr_bound, Mapping) or not isinstance(merge_bound, Mapping):
        return
    accepted_commit = publication.get("accepted_commit")

    pulls = evidence.get("candidate_pull_requests") or []
    if not pulls:
        _finding(errors, "candidate_pr_missing", "event_evidence.candidate_pull_requests", "accepted commit must resolve to exactly one originating PR")
    elif len(pulls) > 1:
        _finding(errors, "candidate_pr_multiple", "event_evidence.candidate_pull_requests", "accepted commit resolves to multiple candidate PRs")
    pr = pulls[0] if len(pulls) == 1 else None

    if pr is not None:
        if _as_run_id(pr.get("number")) != _as_run_id(originating.get("number")):
            _finding(errors, "candidate_pr_number_mismatch", "event_evidence.candidate_pull_requests[0].number", "originating PR number differs from accepted identity")
        for field, code in (
            ("head_sha", "candidate_pr_head_mismatch"),
            ("base_sha", "candidate_pr_base_mismatch"),
            ("merge_commit_sha", "candidate_pr_merge_commit_mismatch"),
        ):
            if pr.get(field) != originating.get(field):
                _finding(errors, code, f"event_evidence.candidate_pull_requests[0].{field}", "originating PR identity differs from accepted identity")
        if evidence.get("merge_commit_tree_sha") != originating.get("merge_tree_sha"):
            _finding(errors, "candidate_pr_merge_tree_mismatch", "event_evidence.merge_commit_tree_sha", "merge tree differs from accepted identity")
        if (
            pr.get("state") != "closed"
            or pr.get("merged") is not True
            or pr.get("base_ref") != policy.get("originating_pr_base_ref")
        ):
            _finding(errors, "candidate_pr_not_merged", "event_evidence.candidate_pull_requests[0]", "originating PR is not merged from the bound base branch")

    if pr is not None:
        pr_run_id = _as_run_id(pr_bound.get("run_id"))
        pr_job_id = _as_run_id(pr_bound.get("job_id"))
        runs = evidence.get("pr_head_workflow_runs") or []
        by_run_id = [run for run in runs if _as_run_id(run.get("id")) == pr_run_id]
        if not by_run_id:
            _finding(errors, "pr_head_dependency_review_run_missing", "event_evidence.pr_head_workflow_runs", "bound PR-head dependency review run is missing")
        elif len(by_run_id) > 1:
            _finding(errors, "pr_head_dependency_review_run_duplicate", "event_evidence.pr_head_workflow_runs", "bound PR-head dependency review run is duplicated")
        else:
            run = by_run_id[0]
            if run.get("path") != policy.get("dependency_security_workflow_path"):
                _finding(errors, "pr_head_dependency_review_wrong_workflow", "event_evidence.pr_head_workflow_runs", "bound PR-head run is not the dependency security workflow")
            if run.get("event") != policy.get("pr_head_dependency_review_event"):
                _finding(errors, "pr_head_dependency_review_wrong_event", "event_evidence.pr_head_workflow_runs", "PR-head dependency review event differs from policy")
            if run.get("status") != "completed":
                _finding(errors, "pr_head_dependency_review_not_completed", "event_evidence.pr_head_workflow_runs", "PR-head dependency review did not complete")
            if run.get("conclusion") != "success":
                _finding(errors, "pr_head_dependency_review_wrong_conclusion", "event_evidence.pr_head_workflow_runs", "PR-head dependency review must be success")
            if run.get("head_sha") != pr.get("head_sha"):
                _finding(errors, "pr_head_dependency_review_wrong_head", "event_evidence.pr_head_workflow_runs", "PR-head dependency review head differs from PR head")

        jobs = (evidence.get("pr_head_workflow_jobs") or {}).get(pr_run_id) or []
        by_job_id = [job for job in jobs if _as_run_id(job.get("id")) == pr_job_id]
        if not by_job_id:
            _finding(errors, "pr_head_dependency_review_job_missing", "event_evidence.pr_head_workflow_jobs", "bound PR-head dependency review job is missing")
        elif len(by_job_id) > 1:
            _finding(errors, "pr_head_dependency_review_job_duplicate", "event_evidence.pr_head_workflow_jobs", "bound PR-head dependency review job is duplicated")
        else:
            job = by_job_id[0]
            if _as_run_id(job.get("run_id")) != pr_run_id:
                _finding(errors, "pr_head_dependency_review_job_wrong_run", "event_evidence.pr_head_workflow_jobs", "bound PR-head job belongs to another run")
            if job.get("name") != policy.get("pr_head_dependency_review_job_name"):
                _finding(errors, "pr_head_dependency_review_job_wrong_name", "event_evidence.pr_head_workflow_jobs", "bound PR-head job name differs from policy")
            if job.get("status") != "completed":
                _finding(errors, "pr_head_dependency_review_job_not_completed", "event_evidence.pr_head_workflow_jobs", "bound PR-head job did not complete")
            if job.get("conclusion") != "success":
                _finding(errors, "pr_head_dependency_review_job_wrong_conclusion", "event_evidence.pr_head_workflow_jobs", "bound PR-head job must be success")

        checks = [check for check in (evidence.get("pr_head_check_runs") or []) if check.get("name") == policy.get("pr_head_dependency_review_job_name")]
        if not checks:
            _finding(errors, "pr_head_dependency_review_check_missing", "event_evidence.pr_head_check_runs", "PR-head dependency review check run is missing")
        elif len(checks) > 1:
            _finding(errors, "pr_head_dependency_review_check_duplicate", "event_evidence.pr_head_check_runs", "PR-head dependency review check run is duplicated")
        else:
            check = checks[0]
            if check.get("head_sha") != pr.get("head_sha"):
                _finding(errors, "pr_head_dependency_review_check_wrong_head", "event_evidence.pr_head_check_runs", "PR-head dependency review check head differs from PR head")
            if check.get("status") != "completed":
                _finding(errors, "pr_head_dependency_review_check_not_completed", "event_evidence.pr_head_check_runs", "PR-head dependency review check did not complete")
            if check.get("conclusion") != "success":
                _finding(errors, "pr_head_dependency_review_check_wrong_conclusion", "event_evidence.pr_head_check_runs", "PR-head dependency review check must be success")
            if check.get("app") != policy.get("pr_head_dependency_review_app"):
                _finding(errors, "pr_head_dependency_review_check_wrong_app", "event_evidence.pr_head_check_runs", "PR-head dependency review app differs from policy")

    merge_run_id = _as_run_id(merge_bound.get("run_id"))
    merge_runs = evidence.get("merge_push_workflow_runs") or []
    by_merge_run = [run for run in merge_runs if _as_run_id(run.get("id")) == merge_run_id]
    if not by_merge_run:
        _finding(errors, "merge_push_dependency_security_run_missing", "event_evidence.merge_push_workflow_runs", "bound merge-push dependency security run is missing")
    elif len(by_merge_run) > 1:
        _finding(errors, "merge_push_dependency_security_run_duplicate", "event_evidence.merge_push_workflow_runs", "bound merge-push dependency security run is duplicated")
    else:
        run = by_merge_run[0]
        if run.get("path") != policy.get("dependency_security_workflow_path"):
            _finding(errors, "merge_push_dependency_security_wrong_workflow", "event_evidence.merge_push_workflow_runs", "bound merge-push run is not the dependency security workflow")
        if run.get("event") != policy.get("merge_push_dependency_security_event"):
            _finding(errors, "merge_push_dependency_security_wrong_event", "event_evidence.merge_push_workflow_runs", "merge-push dependency security event differs from policy")
        if run.get("status") != "completed":
            _finding(errors, "merge_push_dependency_security_not_completed", "event_evidence.merge_push_workflow_runs", "merge-push dependency security did not complete")
        if run.get("conclusion") != "success":
            _finding(errors, "merge_push_dependency_security_wrong_conclusion", "event_evidence.merge_push_workflow_runs", "merge-push dependency security must be success")
        if run.get("head_sha") != accepted_commit:
            _finding(errors, "merge_push_dependency_security_wrong_head", "event_evidence.merge_push_workflow_runs", "merge-push dependency security head differs from accepted commit")

    merge_jobs = (evidence.get("merge_push_workflow_jobs") or {}).get(merge_run_id) or []
    audit_jobs = [job for job in merge_jobs if job.get("name") == policy.get("merge_push_dependency_audit_job_name")]
    if not audit_jobs:
        _finding(errors, "merge_push_dependency_audit_job_missing", "event_evidence.merge_push_workflow_jobs", "merge-push dependency audit job is missing")
    elif len(audit_jobs) > 1:
        _finding(errors, "merge_push_dependency_audit_job_duplicate", "event_evidence.merge_push_workflow_jobs", "merge-push dependency audit job is duplicated")
    else:
        job = audit_jobs[0]
        if _as_run_id(job.get("run_id")) != merge_run_id:
            _finding(errors, "merge_push_dependency_audit_job_wrong_run", "event_evidence.merge_push_workflow_jobs", "merge-push audit job belongs to another run")
        if job.get("status") != "completed":
            _finding(errors, "merge_push_dependency_audit_job_not_completed", "event_evidence.merge_push_workflow_jobs", "merge-push audit job did not complete")
        if job.get("conclusion") != "success":
            _finding(errors, "merge_push_dependency_audit_job_wrong_conclusion", "event_evidence.merge_push_workflow_jobs", "merge-push audit job must be success")

    review_jobs = [job for job in merge_jobs if job.get("name") == policy.get("merge_push_dependency_review_job_name")]
    if not review_jobs:
        _finding(errors, "merge_push_dependency_review_job_missing", "event_evidence.merge_push_workflow_jobs", "merge-push dependency review job is missing")
    elif len(review_jobs) > 1:
        _finding(errors, "merge_push_dependency_review_job_duplicate", "event_evidence.merge_push_workflow_jobs", "merge-push dependency review job is duplicated")
    else:
        job = review_jobs[0]
        if _as_run_id(job.get("run_id")) != merge_run_id:
            _finding(errors, "merge_push_dependency_review_job_wrong_run", "event_evidence.merge_push_workflow_jobs", "merge-push review job belongs to another run")
        if job.get("status") != "completed":
            _finding(errors, "merge_push_dependency_review_job_not_completed", "event_evidence.merge_push_workflow_jobs", "merge-push review job did not complete")
        if job.get("conclusion") != "skipped":
            _finding(errors, "merge_push_dependency_review_job_drift", "event_evidence.merge_push_workflow_jobs", "merge-push dependency review job must be the design skip")

    merge_checks = evidence.get("merge_push_check_runs") or []
    audit_checks = [check for check in merge_checks if check.get("name") == policy.get("merge_push_dependency_audit_job_name")]
    if not audit_checks:
        _finding(errors, "merge_push_dependency_audit_check_missing", "event_evidence.merge_push_check_runs", "merge-push dependency audit check run is missing")
    elif len(audit_checks) > 1:
        _finding(errors, "merge_push_dependency_audit_check_duplicate", "event_evidence.merge_push_check_runs", "merge-push dependency audit check run is duplicated")
    else:
        check = audit_checks[0]
        if check.get("head_sha") != accepted_commit:
            _finding(errors, "merge_push_dependency_audit_check_wrong_head", "event_evidence.merge_push_check_runs", "merge-push audit check head differs from accepted commit")
        if check.get("status") != "completed":
            _finding(errors, "merge_push_dependency_audit_check_not_completed", "event_evidence.merge_push_check_runs", "merge-push audit check did not complete")
        if check.get("conclusion") != "success":
            _finding(errors, "merge_push_dependency_audit_check_wrong_conclusion", "event_evidence.merge_push_check_runs", "merge-push audit check must be success")
        if check.get("app") != policy.get("merge_push_dependency_security_app"):
            _finding(errors, "merge_push_dependency_audit_check_wrong_app", "event_evidence.merge_push_check_runs", "merge-push audit check app differs from policy")

    review_checks = [check for check in merge_checks if check.get("name") == policy.get("merge_push_dependency_review_job_name")]
    if not review_checks:
        _finding(errors, "merge_push_dependency_review_check_missing", "event_evidence.merge_push_check_runs", "merge-push dependency review check run is missing")
    elif len(review_checks) > 1:
        _finding(errors, "merge_push_dependency_review_check_duplicate", "event_evidence.merge_push_check_runs", "merge-push dependency review check run is duplicated")
    else:
        check = review_checks[0]
        if check.get("head_sha") != accepted_commit:
            _finding(errors, "merge_push_dependency_review_check_wrong_head", "event_evidence.merge_push_check_runs", "merge-push review check head differs from accepted commit")
        if check.get("status") != "completed":
            _finding(errors, "merge_push_dependency_review_check_not_completed", "event_evidence.merge_push_check_runs", "merge-push review check did not complete")
        if check.get("conclusion") != "skipped":
            _finding(errors, "merge_push_dependency_review_check_drift", "event_evidence.merge_push_check_runs", "merge-push dependency review check must be the design skip")
        if check.get("app") != policy.get("merge_push_dependency_security_app"):
            _finding(errors, "merge_push_dependency_review_check_wrong_app", "event_evidence.merge_push_check_runs", "merge-push review check app differs from policy")

    for name in policy.get("merge_push_required_checks") or []:
        by_name = [check for check in merge_checks if check.get("name") == name]
        if not by_name:
            _finding(errors, "merge_push_required_check_missing", "event_evidence.merge_push_check_runs", f"required merge-push check is missing: {name}")
        elif len(by_name) > 1:
            _finding(errors, "merge_push_required_check_duplicate", "event_evidence.merge_push_check_runs", f"required merge-push check is duplicated: {name}")
        else:
            check = by_name[0]
            if check.get("head_sha") != accepted_commit:
                _finding(errors, "merge_push_required_check_wrong_head", "event_evidence.merge_push_check_runs", f"required merge-push check head differs from accepted commit: {name}")
            if check.get("status") != "completed":
                _finding(errors, "merge_push_required_check_not_completed", "event_evidence.merge_push_check_runs", f"required merge-push check did not complete: {name}")
            if check.get("conclusion") != "success":
                _finding(errors, "merge_push_required_check_wrong_conclusion", "event_evidence.merge_push_check_runs", f"required merge-push check must be success: {name}")


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

    verify_authority = commands.add_parser("verify-publication-authority")
    verify_authority.add_argument("--manifest", required=True, type=Path)
    verify_authority.add_argument("--repository", required=True)
    verify_authority.add_argument("--actor-id", required=True)
    verify_authority.add_argument("--accepted-run-id", required=True)
    verify_authority.add_argument("--accepted-run-attempt", required=True)
    verify_authority.add_argument("--accepted-commit", required=True)
    verify_authority.add_argument("--accepted-artifact-name", required=True)
    verify_authority.add_argument("--accepted-artifact-id", required=True)
    verify_authority.add_argument("--accepted-artifact-service-digest", required=True)
    verify_authority.add_argument("--tag", required=True)
    verify_authority.add_argument("--workflow-ref", required=True)
    verify_authority.add_argument("--environment", required=True)
    verify_authority.add_argument("--trusted-owner", required=True)
    verify_authority.add_argument("--trusted-repository", required=True)
    verify_authority.add_argument("--trusted-workflow", required=True)
    verify_authority.add_argument("--trusted-environment", required=True)
    verify_authority.add_argument("--workflow-execution-commit")
    verify_authority.add_argument("--workflow-file-sha256")
    verify_authority.add_argument("--branch-protection-preflight-receipt-sha256")
    verify_authority.add_argument("--observed-branch")
    verify_authority.add_argument("--observed-at")
    verify_authority.add_argument("--event-evidence", type=Path)
    verify_authority.add_argument("--check-policy-json")

    publication_check_evidence = commands.add_parser("publication-check-evidence")
    publication_check_evidence.add_argument("--repository", required=True)
    publication_check_evidence.add_argument("--accepted-commit", required=True)
    publication_check_evidence.add_argument("--github-token-env", default="GH_TOKEN")
    publication_check_evidence.add_argument("--evidence-root", type=Path)
    publication_check_evidence.add_argument("--output", required=True, type=Path)

    publication_manifests = commands.add_parser("publication-manifests")
    publication_manifests.add_argument("--candidate-manifest", required=True, type=Path)
    publication_manifests.add_argument("--artifact-dir", required=True, type=Path)
    publication_manifests.add_argument("--actor-id", required=True)
    publication_manifests.add_argument("--authorized-actor-id", required=True)
    publication_manifests.add_argument("--artifact-id", required=True)
    publication_manifests.add_argument("--artifact-service-digest", required=True)
    publication_manifests.add_argument("--tag", required=True)
    publication_manifests.add_argument("--workflow-ref", required=True)
    publication_manifests.add_argument("--environment", required=True)
    publication_manifests.add_argument("--trusted-owner", required=True)
    publication_manifests.add_argument("--trusted-repository", required=True)
    publication_manifests.add_argument("--trusted-workflow", required=True)
    publication_manifests.add_argument("--trusted-environment", required=True)
    publication_manifests.add_argument("--workflow-execution-commit")
    publication_manifests.add_argument("--workflow-file-sha256")
    publication_manifests.add_argument("--branch-protection-preflight-receipt-sha256")
    publication_manifests.add_argument("--check-policy-json")
    publication_manifests.add_argument("--originating-pr-number")
    publication_manifests.add_argument("--originating-pr-head-sha")
    publication_manifests.add_argument("--originating-pr-base-sha")
    publication_manifests.add_argument("--originating-pr-merge-commit-sha")
    publication_manifests.add_argument("--originating-pr-merge-tree-sha")
    publication_manifests.add_argument("--pr-head-dependency-review-run-id")
    publication_manifests.add_argument("--pr-head-dependency-review-job-id")
    publication_manifests.add_argument("--merge_push-dependency-security-run-id")
    publication_manifests.add_argument("--observed-branch")
    publication_manifests.add_argument("--observed-at")
    publication_manifests.add_argument("--authority-output", required=True, type=Path)
    publication_manifests.add_argument("--activation-output", required=True, type=Path)

    publication_activation = commands.add_parser("publication-activation")
    publication_activation.add_argument("--expected-authority", required=True, type=Path)
    publication_activation.add_argument("--candidate-manifest", required=True, type=Path)
    publication_activation.add_argument("--artifact-dir", required=True, type=Path)
    publication_activation.add_argument("--activation-output", required=True, type=Path)

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
        elif args.command == "verify-publication-authority":
            authority = _read_json(args.manifest)
            event_evidence = _read_json(args.event_evidence) if args.event_evidence else None
            result = _result_or_error(
                verify_publication_authority,
                authority,
                repository=args.repository,
                actor_id=args.actor_id,
                accepted_run_id=args.accepted_run_id,
                accepted_run_attempt=args.accepted_run_attempt,
                accepted_commit=args.accepted_commit,
                accepted_artifact_name=args.accepted_artifact_name,
                accepted_artifact_id=args.accepted_artifact_id,
                accepted_artifact_service_digest=args.accepted_artifact_service_digest,
                tag=args.tag,
                workflow_ref=args.workflow_ref,
                environment=args.environment,
                trusted_owner=args.trusted_owner,
                trusted_repository=args.trusted_repository,
                trusted_workflow=args.trusted_workflow,
                trusted_environment=args.trusted_environment,
                workflow_execution_commit=args.workflow_execution_commit,
                workflow_file_sha256=args.workflow_file_sha256,
                branch_protection_preflight_receipt_sha256=args.branch_protection_preflight_receipt_sha256,
                observed_branch=args.observed_branch,
                observed_at=args.observed_at,
                event_evidence=event_evidence,
                check_policy_json=args.check_policy_json,
            )
        elif args.command == "publication-check-evidence":
            evidence_kwargs = {
                "repository": args.repository,
                "github_token": os.environ.get(args.github_token_env),
                "accepted_commit": args.accepted_commit,
                "evidence_root": args.evidence_root,
            }
            evidence = collect_publication_check_evidence(**evidence_kwargs)
            _write_json(args.output, evidence)
            result = VerificationResult(
                True,
                (),
                {
                    "accepted_commit": args.accepted_commit,
                    "evidence_file": str(args.output),
                    "evidence_repository": args.repository,
                },
            )
        elif args.command == "publication-manifests":
            candidate_manifest = _read_json(args.candidate_manifest)
            authority, activation = build_publication_manifests(
                candidate_manifest,
                artifact_dir=args.artifact_dir,
                actor_id=args.actor_id,
                authorized_actor_id=args.authorized_actor_id,
                artifact_id=args.artifact_id,
                artifact_service_digest=args.artifact_service_digest,
                tag=args.tag,
                workflow_ref=args.workflow_ref,
                environment=args.environment,
                trusted_owner=args.trusted_owner,
                trusted_repository=args.trusted_repository,
                trusted_workflow=args.trusted_workflow,
                trusted_environment=args.trusted_environment,
                workflow_execution_commit=args.workflow_execution_commit,
                workflow_file_sha256=args.workflow_file_sha256,
                branch_protection_preflight_receipt_sha256=args.branch_protection_preflight_receipt_sha256,
                check_policy_json=args.check_policy_json,
                originating_pr_number=args.originating_pr_number,
                originating_pr_head_sha=args.originating_pr_head_sha,
                originating_pr_base_sha=args.originating_pr_base_sha,
                originating_pr_merge_commit_sha=args.originating_pr_merge_commit_sha,
                originating_pr_merge_tree_sha=args.originating_pr_merge_tree_sha,
                pr_head_dependency_review_run_id=args.pr_head_dependency_review_run_id,
                pr_head_dependency_review_job_id=args.pr_head_dependency_review_job_id,
                merge_push_dependency_security_run_id=args.merge_push_dependency_security_run_id,
                observed_branch=args.observed_branch,
                observed_at=args.observed_at,
            )
            _write_json(args.authority_output, authority)
            _write_json(args.activation_output, activation)
            result = verify_publication_activation(
                activation,
                expected=authority,
                downloaded_manifest=candidate_manifest,
                downloaded_artifact_dir=args.artifact_dir,
            )
        elif args.command == "publication-activation":
            authority = _read_json(args.expected_authority)
            candidate_manifest = _read_json(args.candidate_manifest)
            activation = build_publication_activation(
                authority,
                artifact_manifest=candidate_manifest,
                artifact_dir=args.artifact_dir,
            )
            _write_json(args.activation_output, activation)
            result = verify_publication_activation(
                activation,
                expected=authority,
                downloaded_manifest=candidate_manifest,
                downloaded_artifact_dir=args.artifact_dir,
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
    "PUBLICATION_AUTHORITY_SCHEMA_V2",
    "PUBLICATION_AUTHORITY_SCHEMA_V3",
    "PUBLICATION_CHECK_EVIDENCE_SCHEMA_VERSION",
    "HISTORY_EXPECTATION_SCHEMA_VERSION",
    "OPERATION_SCHEMA_VERSION",
    "PROVENANCE_SCHEMA_VERSION",
    "Finding",
    "VerificationResult",
    "archive_member_manifest",
    "build_artifact_manifest",
    "build_installed_manifest",
    "build_publication_activation",
    "build_publication_manifests",
    "canonical_digest",
    "canonical_json",
    "canonical_json_bytes",
    "build_operation_manifest",
    "collect_publication_check_evidence",
    "main",
    "parse_check_policy_document",
    "sha256_bytes",
    "sha256_file",
    "verify_artifact_manifest",
    "verify_installed_distribution",
    "verify_installed_manifest",
    "verify_publication_authority",
    "verify_publication_activation",
    "verify_operation_manifest",
    "verify_canonical_value",
    "collect_reachable_history",
    "verify_reachable_history",
    "verify_release_contract",
]


if __name__ == "__main__":
    sys.exit(main())
