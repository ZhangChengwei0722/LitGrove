from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import Any

from research_kb.bundle import load_workspace_entries, records_of_kind, validate_workspace_entries
from research_kb.catalog.models import canonical_digest
from research_kb.contracts.validator import validate_record
from research_kb.errors import (
    INVALID_AUTHORITY,
    PROTECTED_INPUT_CHANGED,
    TRUST_AUTHORITY_INVALID,
    UNRESOLVED_REFERENCE,
    WRITE_CONFLICT,
    Diagnostic,
    ResearchKBError,
)
from research_kb.identifiers import Namespace, allocate_id, validate_id
from research_kb.process_events import Clock, timestamp, utc_now
from research_kb.services.parse_application import ParseAdapterRegistry
from research_kb.source_resolution import observe_paper_source
from research_kb.storage.json_io import file_sha256, read_jsonl, serialize_jsonl
from research_kb.storage.transactions import TransactionManager
from research_kb.trusted_parse_authority import (
    TRUSTED_PARSE_POLICY,
    TRUSTED_PARSE_PROFILE,
    TrustedParseAuthorityMutation,
    TrustedParseAuthorityPreview,
    TrustedParseAuthorityProjection,
    current_authority_heads,
    trusted_parse_authority_chain_diagnostics,
)
from research_kb.workspace import WorkspaceLayout
from research_kb.workspace_materialization import deterministic_uuid4


class TrustedParseAuthorityService:
    def __init__(
        self,
        layout: WorkspaceLayout,
        *,
        transaction_manager: TransactionManager | None = None,
        clock: Clock = utc_now,
        policy_version: str = TRUSTED_PARSE_POLICY,
        parser_profiles: Iterable[str] = (TRUSTED_PARSE_PROFILE,),
        parser_version_resolver: Callable[[str], str] | None = None,
    ):
        self.layout = layout
        self.transactions = transaction_manager or TransactionManager(layout, clock=clock)
        self.clock = clock
        self.policy_version = policy_version
        self.parser_profiles = frozenset(parser_profiles)
        self.parser_version_resolver = parser_version_resolver or _default_parser_version

    def preview(
        self,
        *,
        paper_id: str,
        adapter_name: str,
        adapter_version: str,
        parser_profile_id: str,
        policy_version: str,
        allowed_operation: str,
        idempotency_key: str,
        actor: str,
        expires_at: datetime,
    ) -> TrustedParseAuthorityPreview:
        if actor != "user":
            raise _error(INVALID_AUTHORITY, "/actor", "trusted Parse authority preview requires user authority")
        if policy_version != self.policy_version or parser_profile_id not in self.parser_profiles:
            raise _error(TRUST_AUTHORITY_INVALID, "/policy_version", "trusted Parse policy or parser profile is unsupported")
        if allowed_operation != "parse_run":
            raise _error(TRUST_AUTHORITY_INVALID, "/allowed_operation", "trusted Parse authority may grant only parse_run")
        try:
            actual_parser_version = self.parser_version_resolver(adapter_name)
        except ResearchKBError as error:
            raise _error(TRUST_AUTHORITY_INVALID, "/parser", "trusted Parse adapter is unavailable") from error
        if actual_parser_version != adapter_version:
            raise _error(TRUST_AUTHORITY_INVALID, "/parser", "trusted Parse adapter version is not current")
        if not idempotency_key or len(idempotency_key.encode("utf-8")) > 1024:
            raise _error(TRUST_AUTHORITY_INVALID, "/idempotency_key", "idempotency key is missing or too large")
        now = self.clock()
        expiry = _as_utc(expires_at)
        if expiry <= now:
            raise _error(TRUST_AUTHORITY_INVALID, "/expires_at", "trusted Parse authority expiry must be in the future")
        entries = load_workspace_entries(self.layout)
        validate_workspace_entries(entries)
        paper = _paper(entries, paper_id)
        observation = observe_paper_source(self.layout, entries, paper)
        if observation.state != "current" or observation.live_sha256 is None:
            raise _error(TRUST_AUTHORITY_INVALID, "/source_fingerprint", "source manifestation is not current")
        seed = f"{self.layout.workspace_id}:{idempotency_key}:trusted-parse"
        authority_id = f"parseauth_{deterministic_uuid4(seed + ':authority')}"
        state_id = f"parseauthstate_{deterministic_uuid4(seed + ':state:1')}"
        created_at = timestamp(self.clock)
        candidate = {
            "schema_version": "1.0",
            "state_id": state_id,
            "authority_id": authority_id,
            "workspace_id": self.layout.workspace_id,
            "revision": 1,
            "predecessor": None,
            "paper_id": paper_id,
            "source_ref": observation.source_ref,
            "source_fingerprint": {"algorithm": "sha256", "value": observation.live_sha256},
            "parser": {"adapter": adapter_name, "version": adapter_version},
            "parser_profile_id": parser_profile_id,
            "policy_version": policy_version,
            "allowed_operation": allowed_operation,
            "idempotency_key_sha256": hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest(),
            "decision": "active",
            "revocation_reason": None,
            "actor": "user",
            "decision_at": created_at,
            "expires_at": _format_timestamp(expiry),
            "created_at": created_at,
        }
        _validate_record(candidate)
        return TrustedParseAuthorityPreview(authority_id, state_id, canonical_digest(candidate), candidate)

    def commit(
        self,
        preview: TrustedParseAuthorityPreview,
        *,
        preview_digest: str | None = None,
        actor: str,
        job_id: str | None = None,
    ) -> TrustedParseAuthorityMutation:
        if actor != "user":
            raise _error(INVALID_AUTHORITY, "/actor", "trusted Parse authority commit requires user authority")
        if preview_digest is None or preview_digest != preview.preview_digest:
            raise _error(PROTECTED_INPUT_CHANGED, "/preview_digest", "trusted Parse authority preview changed")
        if canonical_digest(preview.candidate) != preview.preview_digest:
            raise _error(PROTECTED_INPUT_CHANGED, "/candidate", "trusted Parse authority candidate changed")
        if job_id is not None:
            job_id = validate_id(job_id, Namespace.JOB)
        records = self._records()
        same = [item for item in records if item["authority_id"] == preview.authority_id]
        if same:
            exact = [item for item in same if item == preview.candidate]
            if len(exact) == 1:
                return TrustedParseAuthorityMutation(preview.authority_id, preview.state_id, 1, "no_change", None)
            raise _error(WRITE_CONFLICT, "/authority_id", "trusted Parse authority ID is already in use")
        projection = self._project_record(preview.candidate)
        if projection.status != "current":
            raise _error(TRUST_AUTHORITY_INVALID, "/candidate", "trusted Parse authority candidate is no longer current")
        return self._append(
            preview.candidate,
            records,
            operation="trusted_parse_authority_commit",
            job_id=job_id,
        )

    def revoke(self, authority_id: str, *, actor: str, reason: str) -> TrustedParseAuthorityMutation:
        if actor != "user":
            raise _error(INVALID_AUTHORITY, "/actor", "trusted Parse authority revocation requires user authority")
        if not reason or len(reason) > 256:
            raise _error(TRUST_AUTHORITY_INVALID, "/reason", "revocation reason is missing or too large")
        records = self._records()
        heads = {item["authority_id"]: item for item in current_authority_heads(records)}
        head = heads.get(authority_id)
        if head is None:
            raise _error(TRUST_AUTHORITY_INVALID, "/authority_id", "trusted Parse authority does not exist")
        if head["decision"] == "revoked":
            return TrustedParseAuthorityMutation(authority_id, head["state_id"], head["revision"], "no_change", None)
        successor = {
            **head,
            "state_id": allocate_id(Namespace.PARSE_AUTHORITY_STATE),
            "revision": head["revision"] + 1,
            "predecessor": {"state_id": head["state_id"], "state_digest": canonical_digest(head)},
            "decision": "revoked",
            "revocation_reason": reason,
            "decision_at": timestamp(self.clock),
        }
        _validate_record(successor)
        return self._append(successor, records, operation="trusted_parse_authority_revoke")

    def current(self, authority_id: str) -> TrustedParseAuthorityProjection:
        records = self._records()
        heads = {item["authority_id"]: item for item in current_authority_heads(records)}
        head = heads.get(authority_id)
        if head is None:
            raise _error(TRUST_AUTHORITY_INVALID, "/authority_id", "trusted Parse authority does not exist")
        if head["decision"] == "revoked":
            return TrustedParseAuthorityProjection(authority_id, "revoked", head, ("user_revoked",))
        return self._project_record(head)

    def _project_record(self, head: dict[str, Any]) -> TrustedParseAuthorityProjection:
        authority_id = head["authority_id"]
        if self.clock() >= _parse_timestamp(head["expires_at"]):
            return TrustedParseAuthorityProjection(authority_id, "expired", head, ("authority_expired",))
        reasons: list[str] = []
        if head["workspace_id"] != self.layout.workspace_id:
            reasons.append("workspace_binding_changed")
        if head["policy_version"] != self.policy_version:
            reasons.append("policy_changed")
        if head["parser_profile_id"] not in self.parser_profiles:
            reasons.append("parser_profile_changed")
        try:
            actual_parser_version = self.parser_version_resolver(head["parser"]["adapter"])
        except ResearchKBError:
            reasons.append("parser_unavailable")
        else:
            if actual_parser_version != head["parser"]["version"]:
                reasons.append("parser_version_changed")
        entries = load_workspace_entries(self.layout)
        paper = _paper(entries, head["paper_id"])
        observation = observe_paper_source(self.layout, entries, paper)
        if observation.source_ref != head["source_ref"]:
            reasons.append("source_reference_changed")
        if observation.state != "current" or observation.live_sha256 != head["source_fingerprint"]["value"]:
            reasons.append("source_manifestation_changed")
        return TrustedParseAuthorityProjection(authority_id, "stale" if reasons else "current", head, tuple(reasons))

    def _append(
        self,
        record: dict[str, Any],
        existing: list[dict[str, Any]],
        *,
        operation: str,
        job_id: str | None = None,
    ) -> TrustedParseAuthorityMutation:
        updated = [*existing, record]
        diagnostics = trusted_parse_authority_chain_diagnostics(updated)
        if diagnostics:
            raise ResearchKBError(diagnostics[0])
        target = self.layout.trusted_parse_authorities_path
        before = file_sha256(target)

        def validate_temp(path):
            temporary = read_jsonl(path, record_kind="trusted-parse-authority", missing_ok=False, id_field="state_id")
            chain = trusted_parse_authority_chain_diagnostics(temporary)
            if chain:
                raise ResearchKBError(chain[0])
            entries = load_workspace_entries(
                self.layout,
                overrides={target: [("trusted-parse-authority", item) for item in temporary]},
            )
            validate_workspace_entries(entries)

        transaction = self.transactions.promote_bytes(
            target=target,
            content=serialize_jsonl(updated),
            target_store="trusted_parse_authorities",
            operation=operation,
            actor="user",
            input_refs=[record["paper_id"]],
            output_refs=[record["authority_id"], record["state_id"]],
            validator=validate_temp,
            expected_before_sha256=before,
            job_id=job_id,
        )
        return TrustedParseAuthorityMutation(
            record["authority_id"], record["state_id"], record["revision"], "created", transaction.event_id
        )

    def _records(self) -> list[dict[str, Any]]:
        records = read_jsonl(
            self.layout.trusted_parse_authorities_path,
            record_kind="trusted-parse-authority",
            id_field="state_id",
        )
        for record in records:
            _validate_record(record)
        diagnostics = trusted_parse_authority_chain_diagnostics(records)
        if diagnostics:
            raise ResearchKBError(diagnostics[0])
        return records


def _paper(entries: list[tuple[str, dict[str, Any]]], paper_id: str) -> dict[str, Any]:
    papers = {item["paper_id"]: item for item in records_of_kind(entries, "registry-paper")}
    try:
        return papers[paper_id]
    except KeyError as error:
        raise ResearchKBError(
            Diagnostic(UNRESOLVED_REFERENCE, "trusted-parse-authority", paper_id, "/paper_id", "paper is not registered")
        ) from error


def _validate_record(record: dict[str, Any]) -> None:
    diagnostics = validate_record("trusted-parse-authority", record, actor="user")
    if diagnostics:
        raise ResearchKBError(diagnostics[0])


def _as_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise _error(TRUST_AUTHORITY_INVALID, "/expires_at", "authority expiry must be timezone-aware")
    return value.astimezone(UTC)


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _default_parser_version(adapter_name: str) -> str:
    return ParseAdapterRegistry().create(adapter_name).version


def _error(code: str, path: str, message: str) -> ResearchKBError:
    return ResearchKBError(Diagnostic(code, "trusted-parse-authority", None, path, message))


__all__ = ["TrustedParseAuthorityService"]
