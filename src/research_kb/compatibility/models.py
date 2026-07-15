from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from research_kb.compatibility.base import CompatibilitySourceRef, DifferenceCandidate, InventoryCandidate
from research_kb.errors import COMPATIBILITY_OUTPUT_INVALID, Diagnostic, ResearchKBError, json_pointer
from research_kb.paths import make_source_ref, validate_root_id


DISPOSITIONS = (
    "direct_read",
    "adapter_projection",
    "unsupported_for_now",
    "legacy_reading_view",
)
DIFFERENCE_TYPES = (
    "representation_only",
    "field_mapping_loss",
    "provenance_break",
    "semantic_mismatch",
    "status_authority_mismatch",
    "unsupported_legacy_view",
)
RECORD_ROLES = ("canonical", "candidate", "reading_view", "other")
PROJECTION_STATUSES = ("complete", "partial", "not_attempted")
SEVERITIES = ("info", "warning", "error")
LOSS_SCOPES = ("identity", "provenance", "authority", "evidence_support", "other", None)
BLOCKING_LOSS_SCOPES = {"identity", "provenance", "authority", "evidence_support"}
_SLUG = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_DIAGNOSTIC = re.compile(r"^[A-Z][A-Z0-9_-]{1,63}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_WINDOWS_PATH = re.compile(r"(?i)(?:^|[\s'\"=:(])(?:[a-z]:[\\/]|\\\\)")
_POSIX_PATH = re.compile(r"(?:^|[\s'\"=:(])/(?!/)[^/\s'\",;:()\[\]]+(?:/[^/\s'\",;:()\[\]]+)*")
_PRIVATE_HOME_SEGMENTS = ("users", "home")


def build_difference_id(
    *,
    source_system: str,
    record_kind: str,
    legacy_id: str,
    difference_type: str,
    field_path: str,
    legacy_value_digest: str | None,
    projected_value_digest: str | None,
) -> str:
    projection = {
        "difference_type": difference_type,
        "field_path": normalize_json_pointer(field_path),
        "legacy_id": legacy_id,
        "legacy_value_digest": legacy_value_digest,
        "projected_value_digest": projected_value_digest,
        "record_kind": record_kind,
        "source_system": source_system,
    }
    canonical = json.dumps(projection, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"diff_sha256_{hashlib.sha256(canonical).hexdigest()}"


def apply_difference_policy(
    difference_type: str,
    severity: str,
    record_role: str,
    loss_scope: str | None,
) -> tuple[str, bool]:
    _require_choice(difference_type, DIFFERENCE_TYPES, "/difference_type")
    _require_choice(severity, SEVERITIES, "/severity")
    _require_choice(record_role, RECORD_ROLES, "/record_role")
    _require_choice(loss_scope, LOSS_SCOPES, "/loss_scope")
    blocking = False
    if difference_type in {"provenance_break", "status_authority_mismatch"}:
        severity = "error"
        blocking = True
    elif difference_type == "semantic_mismatch":
        blocking = record_role in {"canonical", "candidate"}
    elif difference_type == "field_mapping_loss":
        blocking = loss_scope in BLOCKING_LOSS_SCOPES
    elif difference_type == "unsupported_legacy_view":
        blocking = record_role != "reading_view"
    return severity, blocking


def normalize_source_ref(source_ref: CompatibilitySourceRef) -> CompatibilitySourceRef:
    validate_root_id(source_ref.root_role)
    normalized = make_source_ref(source_ref.root_role, source_ref.relative_path)
    return CompatibilitySourceRef(normalized.root_id, normalized.relative_path)


def normalize_inventory_candidate(
    source_system: str,
    candidate: InventoryCandidate,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    _require_slug(source_system, "/legacy_identity/source_system")
    _require_slug(candidate.record_kind, "/legacy_identity/record_kind")
    _require_text(candidate.legacy_id, "/legacy_identity/legacy_id", max_length=512)
    source_ref = normalize_source_ref(candidate.source_ref)
    _require_choice(candidate.disposition, DISPOSITIONS, "/disposition")
    if candidate.projected_kind is not None:
        _require_slug(candidate.projected_kind, "/projected_kind")
    _require_choice(candidate.projection_status, PROJECTION_STATUSES, "/projection_status")
    _require_choice(candidate.record_role, RECORD_ROLES, "/record_role")
    unsupported_fields = _unique_sorted(
        normalize_json_pointer(value) for value in candidate.unsupported_fields
    )
    diagnostic_codes = _unique_sorted(candidate.diagnostic_codes)
    if any(_DIAGNOSTIC.fullmatch(value) is None for value in diagnostic_codes):
        raise _output_error("diagnostic code is not a bounded public code", "/diagnostic_codes")
    identity = {
        "source_system": source_system,
        "record_kind": candidate.record_kind,
        "legacy_id": candidate.legacy_id,
    }
    item = {
        "legacy_identity": identity,
        "source_ref": source_ref.to_dict(),
        "disposition": candidate.disposition,
        "projected_kind": candidate.projected_kind,
        "projection_status": candidate.projection_status,
        "record_role": candidate.record_role,
        "unsupported_fields": unsupported_fields,
        "diagnostic_codes": diagnostic_codes,
    }
    differences = [
        normalize_difference(source_system, candidate.record_kind, candidate.legacy_id, candidate.record_role, value)
        for value in candidate.differences
    ]
    return item, differences


def normalize_difference(
    source_system: str,
    record_kind: str,
    legacy_id: str,
    record_role: str,
    candidate: DifferenceCandidate,
) -> dict[str, Any]:
    field_path = normalize_json_pointer(candidate.field_path)
    _require_digest(candidate.legacy_value_digest, "/legacy_value_digest")
    _require_digest(candidate.projected_value_digest, "/projected_value_digest")
    severity, blocking = apply_difference_policy(
        candidate.difference_type,
        candidate.severity,
        record_role,
        candidate.loss_scope,
    )
    for path, value in (
        ("/message", candidate.message),
        ("/risk", candidate.risk),
        ("/recommended_action", candidate.recommended_action),
    ):
        _require_text(value, path, max_length=512)
    private_ref = normalize_source_ref(candidate.private_detail_ref) if candidate.private_detail_ref else None
    identity = {"source_system": source_system, "record_kind": record_kind, "legacy_id": legacy_id}
    return {
        "schema_version": "1.0",
        "difference_id": build_difference_id(
            source_system=source_system,
            record_kind=record_kind,
            legacy_id=legacy_id,
            difference_type=candidate.difference_type,
            field_path=field_path,
            legacy_value_digest=candidate.legacy_value_digest,
            projected_value_digest=candidate.projected_value_digest,
        ),
        "legacy_identity": identity,
        "difference_type": candidate.difference_type,
        "severity": severity,
        "blocking": blocking,
        "field_path": field_path,
        "legacy_value_digest": candidate.legacy_value_digest,
        "projected_value_digest": candidate.projected_value_digest,
        "loss_scope": candidate.loss_scope,
        "message": candidate.message,
        "risk": candidate.risk,
        "recommended_action": candidate.recommended_action,
        "private_detail_ref": private_ref.to_dict() if private_ref else None,
    }


def normalize_json_pointer(value: str) -> str:
    if not isinstance(value, str) or not value.startswith("/"):
        raise _output_error("field path must be a non-empty JSON pointer", "/field_path")
    parts = []
    for raw in value[1:].split("/"):
        if re.search(r"~(?![01])", raw):
            raise _output_error("field path contains an invalid JSON pointer escape", "/field_path")
        parts.append(raw.replace("~1", "/").replace("~0", "~"))
    return json_pointer(parts)


def _require_slug(value: object, path: str) -> None:
    if not isinstance(value, str) or _SLUG.fullmatch(value) is None:
        raise _output_error("value must be a lower-case slug", path)


def _require_text(value: object, path: str, *, max_length: int) -> None:
    if not isinstance(value, str) or not value or len(value) > max_length:
        raise _output_error("value must be a bounded non-empty string", path)
    path_like = _WINDOWS_PATH.search(value) or _POSIX_PATH.search(value) or value.startswith(("~/", "//", "\\\\"))
    folded = value.casefold()
    private_home = any(f"/{segment}/" in folded for segment in _PRIVATE_HOME_SEGMENTS)
    if path_like or private_home:
        raise _output_error("output value resembles a private absolute path", path)


def _require_digest(value: object, path: str) -> None:
    if value is not None and (not isinstance(value, str) or _DIGEST.fullmatch(value) is None):
        raise _output_error("value digest must be lowercase SHA-256 or null", path)


def _require_choice(value: object, choices: tuple[object, ...], path: str) -> None:
    if value not in choices:
        raise _output_error("value is outside the public compatibility contract", path)


def _unique_sorted(values) -> list[str]:
    result = sorted(values)
    if len(result) != len(set(result)):
        raise _output_error("duplicate compatibility output value", "")
    return result


def _output_error(message: str, path: str) -> ResearchKBError:
    return ResearchKBError(Diagnostic(COMPATIBILITY_OUTPUT_INVALID, "compatibility", None, path, message))
