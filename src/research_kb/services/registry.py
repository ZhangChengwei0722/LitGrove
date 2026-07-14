from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from research_kb.bundle import load_workspace_entries, records_of_kind, validate_workspace_entries
from research_kb.contracts.validator import validate_record
from research_kb.errors import (
    GROUNDING_MISMATCH,
    INVALID_AUTHORITY,
    SCHEMA_VALIDATION_FAILED,
    UNRESOLVED_REFERENCE,
    Diagnostic,
    ResearchKBError,
)
from research_kb.identifiers import Namespace, allocate_id
from research_kb.process_events import timestamp
from research_kb.storage.json_io import file_sha256, read_jsonl, serialize_jsonl
from research_kb.storage.transactions import TransactionManager, TransactionResult
from research_kb.workspace import WorkspaceLayout


IdAllocator = Callable[[Namespace], str]


class RegistryService:
    def __init__(
        self,
        layout: WorkspaceLayout,
        *,
        transaction_manager: TransactionManager | None = None,
        id_allocator: IdAllocator = allocate_id,
    ):
        self.layout = layout
        self.transactions = transaction_manager or TransactionManager(layout)
        self.id_allocator = id_allocator

    def add(
        self,
        *,
        root_id: str,
        relative_path: str,
        metadata: Mapping[str, Any],
        actor: str = "agent",
    ) -> tuple[dict[str, Any], TransactionResult]:
        unexpected_metadata = set(metadata) - {"bibliography", "review_status", "fixture_origin"}
        if unexpected_metadata:
            raise ResearchKBError(
                Diagnostic(
                    SCHEMA_VALIDATION_FAILED,
                    "registry-paper",
                    None,
                    "/metadata",
                    f"unsupported metadata fields: {', '.join(sorted(unexpected_metadata))}",
                )
            )
        source_ref, source = self.layout.resolve_source(root_id, relative_path)
        if not source.is_file():
            raise ResearchKBError(
                Diagnostic(SCHEMA_VALIDATION_FAILED, "registry-paper", None, "/source_ref", "source asset does not exist")
            )
        source_hash = file_sha256(source)
        assert source_hash is not None
        registry_before = file_sha256(self.layout.registry_path)
        current_entries = load_workspace_entries(self.layout)
        validate_workspace_entries(current_entries)
        current = records_of_kind(current_entries, "registry-paper")
        paper_id = self.id_allocator(Namespace.PAPER)
        now = timestamp(self.transactions.clock)
        duplicate_ids = [
            record["paper_id"]
            for record in current
            if record["source_fingerprint"]["value"] == source_hash
        ]
        bibliography_value = metadata.get("bibliography", {})
        if not isinstance(bibliography_value, Mapping):
            raise ResearchKBError(
                Diagnostic(SCHEMA_VALIDATION_FAILED, "registry-paper", None, "/bibliography", "bibliography must be an object")
            )
        bibliography = dict(bibliography_value)
        bibliography.setdefault("title", None)
        bibliography.setdefault("authors", [])
        bibliography.setdefault("year", None)
        bibliography.setdefault("doi", None)
        record = {
            "schema_version": "1.0",
            "paper_id": paper_id,
            "source_ref": source_ref.to_dict(),
            "source_fingerprint": {"algorithm": "sha256", "value": source_hash},
            "bibliography": bibliography,
            "screening_status": "candidate",
            "duplicate_candidate_ids": duplicate_ids,
            "review_status": metadata.get("review_status", "ai_checked"),
            "automation_status": "pending",
            "created_at": now,
            "updated_at": now,
        }
        if metadata.get("fixture_origin") is not None:
            record["fixture_origin"] = metadata["fixture_origin"]
        candidate_diagnostics = validate_record("registry-paper", record, actor=actor)
        if candidate_diagnostics:
            raise ResearchKBError(candidate_diagnostics[0])
        record["automation_status"] = "passed_auto_checks"

        proposed: list[dict[str, Any]] = []
        for existing in current:
            if existing["paper_id"] not in duplicate_ids:
                proposed.append(existing)
                continue
            updated = dict(existing)
            updated["duplicate_candidate_ids"] = sorted({*existing["duplicate_candidate_ids"], paper_id})
            updated["updated_at"] = now
            proposed.append(updated)
        proposed.append(record)

        def validate_source_stability() -> None:
            if file_sha256(source) != source_hash:
                raise ResearchKBError(
                    Diagnostic(GROUNDING_MISMATCH, "registry-paper", paper_id, "/source_fingerprint", "source changed during Registry operation")
                )

        def validate_temp(path: Path) -> None:
            validate_source_stability()
            temporary_records = read_jsonl(path, record_kind="registry-paper", missing_ok=False, id_field="paper_id")
            entries = load_workspace_entries(
                self.layout,
                overrides={self.layout.registry_path: [("registry-paper", item) for item in temporary_records]},
            )
            validate_workspace_entries(entries)

        output_refs = [paper_id, *duplicate_ids]
        result = self.transactions.promote_bytes(
            target=self.layout.registry_path,
            content=serialize_jsonl(proposed),
            target_store="registry",
            operation="registry_add",
            actor=actor,
            input_refs=duplicate_ids,
            output_refs=output_refs,
            validator=validate_temp,
            post_replace_validator=validate_source_stability,
            expected_before_sha256=registry_before,
        )
        return record, result

    def replace(
        self,
        *,
        paper_id: str,
        changes: Mapping[str, Any],
        actor: str = "agent",
    ) -> tuple[dict[str, Any], TransactionResult]:
        allowed = {"bibliography", "screening_status", "review_status"}
        unexpected = set(changes) - allowed
        if unexpected:
            raise ResearchKBError(
                Diagnostic(
                    INVALID_AUTHORITY,
                    "registry-paper",
                    paper_id,
                    "/payload",
                    f"Registry identity fields are CLI-owned: {', '.join(sorted(unexpected))}",
                )
            )
        if actor != "user" and changes.get("screening_status") in {"included", "excluded"}:
            raise ResearchKBError(
                Diagnostic(INVALID_AUTHORITY, "registry-paper", paper_id, "/screening_status", "final screening state is user-only")
            )
        if actor != "user" and changes.get("review_status") in {"human_checked", "verified"}:
            raise ResearchKBError(
                Diagnostic(INVALID_AUTHORITY, "registry-paper", paper_id, "/review_status", "human-only review state")
            )

        registry_before = file_sha256(self.layout.registry_path)
        current_entries = load_workspace_entries(self.layout)
        validate_workspace_entries(current_entries)
        current = records_of_kind(current_entries, "registry-paper")
        existing = next((record for record in current if record["paper_id"] == paper_id), None)
        if existing is None:
            raise ResearchKBError(
                Diagnostic(UNRESOLVED_REFERENCE, "registry-paper", paper_id, "/paper_id", "paper is not registered")
            )
        _, source = self.layout.resolve_source(
            existing["source_ref"]["root_id"],
            existing["source_ref"]["relative_path"],
        )
        source_hash = existing["source_fingerprint"]["value"]
        if file_sha256(source) != source_hash:
            raise ResearchKBError(
                Diagnostic(GROUNDING_MISMATCH, "registry-paper", paper_id, "/source_fingerprint", "registered source fingerprint is stale")
            )

        updated = dict(existing)
        if "bibliography" in changes:
            if not isinstance(changes["bibliography"], Mapping):
                raise ResearchKBError(
                    Diagnostic(SCHEMA_VALIDATION_FAILED, "registry-paper", paper_id, "/bibliography", "bibliography must be an object")
                )
            bibliography = dict(existing["bibliography"])
            bibliography.update(changes["bibliography"])
            updated["bibliography"] = bibliography
        for field in ("screening_status", "review_status"):
            if field in changes:
                updated[field] = changes[field]
        updated["automation_status"] = "pending"
        updated["updated_at"] = timestamp(self.transactions.clock)
        diagnostics = validate_record("registry-paper", updated, actor="stored")
        if diagnostics:
            raise ResearchKBError(diagnostics[0])
        updated["automation_status"] = "passed_auto_checks"
        proposed = [updated if record["paper_id"] == paper_id else record for record in current]

        def validate_source_stability() -> None:
            if file_sha256(source) != source_hash:
                raise ResearchKBError(
                    Diagnostic(GROUNDING_MISMATCH, "registry-paper", paper_id, "/source_fingerprint", "source changed during Registry operation")
                )

        def validate_temp(path: Path) -> None:
            validate_source_stability()
            temporary_records = read_jsonl(path, record_kind="registry-paper", missing_ok=False, id_field="paper_id")
            entries = load_workspace_entries(
                self.layout,
                overrides={self.layout.registry_path: [("registry-paper", item) for item in temporary_records]},
            )
            validate_workspace_entries(entries)

        result = self.transactions.promote_bytes(
            target=self.layout.registry_path,
            content=serialize_jsonl(proposed),
            target_store="registry",
            operation="registry_replace",
            actor=actor,
            input_refs=[paper_id],
            output_refs=[paper_id],
            validator=validate_temp,
            post_replace_validator=validate_source_stability,
            expected_before_sha256=registry_before,
        )
        return updated, result
