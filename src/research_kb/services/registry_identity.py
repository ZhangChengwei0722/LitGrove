from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from research_kb.bundle import load_workspace_entries, records_of_kind, validate_workspace_entries
from research_kb.catalog.models import canonical_digest
from research_kb.contracts.validator import validate_record
from research_kb.errors import (
    DUPLICATE_ID,
    INVALID_AUTHORITY,
    SCHEMA_VALIDATION_FAILED,
    WRITE_CONFLICT,
    Diagnostic,
    ResearchKBError,
)
from research_kb.identifiers import Namespace, allocate_id, validate_id
from research_kb.identity_corrections import identity_correction_diagnostics, project_registry_identity
from research_kb.process_events import timestamp
from research_kb.services._pipeline_authority import require_job_authority
from research_kb.storage.json_io import file_sha256, read_jsonl, serialize_jsonl
from research_kb.storage.transactions import TransactionManager, TransactionResult
from research_kb.workspace import WorkspaceLayout


IdAllocator = Callable[[Namespace], str]


@dataclass(frozen=True, slots=True)
class RegistryIdentityCorrectionResult:
    correction: dict[str, Any]
    transaction: TransactionResult | None


class RegistryIdentityCorrectionService:
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

    def list(self) -> dict[str, Any]:
        entries = load_workspace_entries(self.layout)
        validate_workspace_entries(entries)
        papers = records_of_kind(entries, "registry-paper")
        corrections = records_of_kind(entries, "registry-identity-correction")
        head = corrections[-1] if corrections else None
        return {
            "status": "success",
            "interface_version": "1.0",
            "current_correction_id": None if head is None else head["correction_id"],
            "current_correction_digest": None if head is None else canonical_digest(head),
            "items": list(project_registry_identity(papers, corrections).values()),
            "persistent_writes": 0,
        }

    def record(
        self,
        *,
        job_id: str,
        operation: str,
        subject_paper_ids: list[str],
        retained_paper_id: str | None,
        supersedes_correction_id: str | None,
        rationale: str,
        expected_previous_correction_id: str | None,
        expected_previous_correction_digest: str | None,
        actor: str,
        fixture_origin: str | None = None,
    ) -> RegistryIdentityCorrectionResult:
        require_job_authority(self.layout, job_id, "registry_identity_correction")
        if actor != "user":
            raise ResearchKBError(
                Diagnostic(INVALID_AUTHORITY, "registry-identity-correction", None, "/actor", "Registry identity correction requires user authority")
            )
        if not isinstance(subject_paper_ids, list) or any(
            not isinstance(paper_id, str) for paper_id in subject_paper_ids
        ):
            raise ResearchKBError(
                Diagnostic(
                    SCHEMA_VALIDATION_FAILED,
                    "registry-identity-correction",
                    None,
                    "/subject_paper_ids",
                    "identity correction subjects must be a list of paper IDs",
                )
            )
        subjects = sorted(set(subject_paper_ids))
        entries = load_workspace_entries(self.layout)
        validate_workspace_entries(entries)
        papers = records_of_kind(entries, "registry-paper")
        current = records_of_kind(entries, "registry-identity-correction")
        head = current[-1] if current else None
        actual_id = None if head is None else head["correction_id"]
        actual_digest = None if head is None else canonical_digest(head)
        if actual_id != expected_previous_correction_id or actual_digest != expected_previous_correction_digest:
            if head is not None and head["previous_correction_id"] == expected_previous_correction_id and _intent(head) == {
                "operation": operation,
                "subject_paper_ids": subjects,
                "retained_paper_id": retained_paper_id,
                "supersedes_correction_id": supersedes_correction_id,
                "rationale": rationale,
                "job_id": job_id,
                "actor": actor,
            }:
                return RegistryIdentityCorrectionResult(head, None)
            raise ResearchKBError(
                Diagnostic(WRITE_CONFLICT, "registry-identity-correction", actual_id, "/previous_correction_id", "Registry identity correction head changed before mutation")
            )

        correction_id = self.id_allocator(Namespace.IDENTITY_CORRECTION)
        validate_id(correction_id, Namespace.IDENTITY_CORRECTION)
        if correction_id in {item["correction_id"] for item in current}:
            raise ResearchKBError(
                Diagnostic(DUPLICATE_ID, "registry-identity-correction", correction_id, "/correction_id", "allocated correction ID is already in use")
            )
        correction = {
            "schema_version": "1.0",
            "correction_id": correction_id,
            "workspace_id": self.layout.workspace_id,
            "previous_correction_id": actual_id,
            "previous_correction_digest": actual_digest,
            "operation": operation,
            "subject_paper_ids": subjects,
            "retained_paper_id": retained_paper_id,
            "supersedes_correction_id": supersedes_correction_id,
            "rationale": rationale,
            "job_id": job_id,
            "actor": actor,
            "created_at": timestamp(self.transactions.clock),
        }
        if fixture_origin is not None:
            correction["fixture_origin"] = fixture_origin
        diagnostics = validate_record("registry-identity-correction", correction, actor=actor)
        if diagnostics:
            raise ResearchKBError(diagnostics[0])
        proposed = [*current, correction]
        semantic = identity_correction_diagnostics(proposed, papers)
        if semantic:
            raise ResearchKBError(semantic[0])
        target = self.layout.identity_corrections_path
        before_sha256 = file_sha256(target)

        def validate_temp(path: Path) -> None:
            temporary = read_jsonl(
                path,
                record_kind="registry-identity-correction",
                missing_ok=False,
                id_field="correction_id",
            )
            failures = identity_correction_diagnostics(temporary, papers)
            if failures:
                raise ResearchKBError(failures[0])
            workspace_entries = load_workspace_entries(
                self.layout,
                overrides={target: [("registry-identity-correction", item) for item in temporary]},
            )
            validate_workspace_entries(workspace_entries)

        transaction = self.transactions.promote_bytes(
            target=target,
            content=serialize_jsonl(proposed),
            target_store="identity_corrections",
            operation="registry_identity_correction",
            actor=actor,
            input_refs=list(
                dict.fromkeys(
                    [
                        *subjects,
                        *([retained_paper_id] if retained_paper_id is not None else []),
                        *([supersedes_correction_id] if supersedes_correction_id is not None else []),
                        *([actual_id] if actual_id is not None else []),
                    ]
                )
            ),
            output_refs=[correction_id],
            validator=validate_temp,
            expected_before_sha256=before_sha256,
            job_id=job_id,
        )
        return RegistryIdentityCorrectionResult(correction, transaction)


def _intent(correction: dict[str, Any]) -> dict[str, Any]:
    return {
        key: correction[key]
        for key in (
            "operation",
            "subject_paper_ids",
            "retained_paper_id",
            "supersedes_correction_id",
            "rationale",
            "job_id",
            "actor",
        )
    }


__all__ = ["RegistryIdentityCorrectionResult", "RegistryIdentityCorrectionService"]
