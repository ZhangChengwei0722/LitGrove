from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from research_kb.catalog.models import canonical_digest
from research_kb.errors import INCOMPLETE_TRANSACTION, Diagnostic


TRUSTED_PARSE_POLICY = "trusted-local-pdf@1.0"
TRUSTED_PARSE_PROFILE = "trusted-local-pdf-standard@1.0"


@dataclass(frozen=True, slots=True)
class TrustedParseAuthorityPreview:
    authority_id: str
    state_id: str
    preview_digest: str
    candidate: dict[str, Any]


@dataclass(frozen=True, slots=True)
class TrustedParseAuthorityMutation:
    authority_id: str
    state_id: str
    revision: int
    result: str
    event_id: str | None


@dataclass(frozen=True, slots=True)
class TrustedParseAuthorityProjection:
    authority_id: str
    status: str
    record: dict[str, Any]
    reasons: tuple[str, ...]


def trusted_parse_authority_chain_diagnostics(records: Iterable[dict[str, Any]]) -> list[Diagnostic]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen_states: set[str] = set()
    diagnostics: list[Diagnostic] = []
    for record in records:
        state_id = record.get("state_id")
        if isinstance(state_id, str):
            if state_id in seen_states:
                diagnostics.append(_diagnostic(record, "/state_id", "duplicate trusted Parse authority state"))
            seen_states.add(state_id)
        authority_id = record.get("authority_id")
        if isinstance(authority_id, str):
            grouped[authority_id].append(record)
    for history in grouped.values():
        ordered = sorted(history, key=lambda item: item.get("revision", 0))
        if not ordered or ordered[0].get("revision") != 1:
            diagnostics.append(_diagnostic(ordered[0] if ordered else {}, "/revision", "authority chain must begin at revision one"))
            continue
        root = ordered[0]
        if root.get("predecessor") is not None or root.get("decision") != "active":
            diagnostics.append(_diagnostic(root, "/predecessor", "authority root must be active with no predecessor"))
        for index, record in enumerate(ordered[1:], start=1):
            previous = ordered[index - 1]
            if record.get("revision") != previous.get("revision", 0) + 1:
                diagnostics.append(_diagnostic(record, "/revision", "authority revisions are not contiguous"))
            expected = {"state_id": previous.get("state_id"), "state_digest": canonical_digest(previous)}
            if record.get("predecessor") != expected:
                diagnostics.append(_diagnostic(record, "/predecessor", "authority predecessor does not match prior revision"))
            for field in (
                "authority_id",
                "workspace_id",
                "paper_id",
                "source_ref",
                "source_fingerprint",
                "parser",
                "parser_profile_id",
                "policy_version",
                "allowed_operation",
                "idempotency_key_sha256",
                "created_at",
                "expires_at",
            ):
                if record.get(field) != root.get(field):
                    diagnostics.append(_diagnostic(record, f"/{field}", f"authority {field} changed across revisions"))
            if previous.get("decision") == "revoked" or record.get("decision") != "revoked":
                diagnostics.append(_diagnostic(record, "/decision", "only one terminal revocation successor is allowed"))
    return _deduplicate(diagnostics)


def current_authority_heads(records: Iterable[dict[str, Any]]) -> tuple[dict[str, Any], ...]:
    records = list(records)
    diagnostics = trusted_parse_authority_chain_diagnostics(records)
    if diagnostics:
        from research_kb.errors import ResearchKBError

        raise ResearchKBError(diagnostics[0])
    heads: dict[str, dict[str, Any]] = {}
    for record in records:
        existing = heads.get(record["authority_id"])
        if existing is None or record["revision"] > existing["revision"]:
            heads[record["authority_id"]] = record
    return tuple(sorted(heads.values(), key=lambda item: item["authority_id"]))


def _diagnostic(record: dict[str, Any], path: str, message: str) -> Diagnostic:
    return Diagnostic(INCOMPLETE_TRANSACTION, "trusted-parse-authority", record.get("state_id"), path, message)


def _deduplicate(items: list[Diagnostic]) -> list[Diagnostic]:
    result: list[Diagnostic] = []
    seen: set[tuple[str, str | None, str, str]] = set()
    for item in items:
        key = (item.code, item.record_id, item.json_path, item.message)
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


__all__ = [
    "TRUSTED_PARSE_POLICY",
    "TRUSTED_PARSE_PROFILE",
    "TrustedParseAuthorityMutation",
    "TrustedParseAuthorityPreview",
    "TrustedParseAuthorityProjection",
    "current_authority_heads",
    "trusted_parse_authority_chain_diagnostics",
]
