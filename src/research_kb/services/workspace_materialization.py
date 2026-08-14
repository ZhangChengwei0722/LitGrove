from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
from dataclasses import replace
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ContextManager

import yaml

from research_kb.contracts.validator import validate_record
from research_kb.errors import (
    INVALID_AUTHORITY,
    MATERIALIZATION_CONFLICT,
    PROTECTED_INPUT_CHANGED,
    WRITE_CONFLICT,
    Diagnostic,
    ResearchKBError,
)
from research_kb.process_events import Clock, timestamp, utc_now
from research_kb.services.bootstrap import WorkspaceBootstrapService
from research_kb.storage.json_io import atomic_write_bytes, read_json_document, serialize_json
from research_kb.workspace import WorkspaceLayout
from research_kb.workspace_validation import MANAGED_DIRECTORIES, build_workspace_marker_for_documents
from research_kb.workspace_materialization import (
    MATERIALIZATION_PROTOCOL,
    ROOT_SECURITY_POLICY,
    RootSecurityAttestation,
    RootSecurityController,
    WorkspaceMaterializationProposal,
    WorkspaceMaterializationReceipt,
    WorkspaceMaterializationRecovery,
    WorkspaceMaterializationRequest,
    canonical_digest,
    deterministic_uuid4,
    path_identity,
)


PhaseHook = Callable[[str], None]
WriterMutex = Callable[[str], ContextManager[object]]
_NAME = re.compile(r"^[^\\/:*?\"<>|\x00-\x1f]{1,80}$")
_SLUG = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_JOURNAL_MAX_BYTES = 131_072
_RESERVED = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
}
_SECTIONS = (
    "research_background_significance",
    "research_problem",
    "method_principle_advantages",
    "conclusions_applications",
    "innovation",
    "limitations",
    "future_outlook",
)


class WorkspaceMaterializationApplicationService:
    def __init__(self, *, clock: Clock = utc_now, phase_hook: PhaseHook | None = None):
        self.clock = clock
        self.phase_hook = phase_hook

    def prepare(
        self,
        request: WorkspaceMaterializationRequest,
        root_security_controller: RootSecurityController,
    ) -> WorkspaceMaterializationProposal:
        now = self.clock()
        expires_at = _as_utc(request.expires_at)
        _validate_request(request, now=now, expires_at=expires_at)
        parent = request.workspace_parent.expanduser().resolve(strict=True)
        if not parent.is_dir():
            raise _error(MATERIALIZATION_CONFLICT, "/workspace_parent", "workspace parent is not a directory")
        target = parent / request.workspace_name
        if os.path.lexists(target):
            raise _error(WRITE_CONFLICT, "/workspace_name", "workspace target already exists")
        parent_attestation = root_security_controller.inspect(parent)
        _require_secure_attestation(parent, parent_attestation)
        _validate_external_roots(request, target)
        source_root_attestations = tuple(
            root_security_controller.inspect(item.path.expanduser().absolute())
            for item in request.source_roots
        )
        for item, attestation in zip(request.source_roots, source_root_attestations, strict=True):
            _require_external_source_attestation(item.path.expanduser().absolute(), attestation)
        local_inbox = request.local_inbox.expanduser().resolve(strict=True)
        local_inbox_attestation = root_security_controller.inspect(local_inbox)
        _require_secure_attestation(local_inbox, local_inbox_attestation)

        seed = _proposal_seed(target, request.idempotency_key)
        workspace_id = f"workspace_{deterministic_uuid4(seed + ':workspace')}"
        domain_profile_id = f"domain-{canonical_digest({'seed': seed, 'kind': 'domain'})[:20]}"
        proposal_id = f"proposal_{deterministic_uuid4(seed + ':proposal')}"
        operation_id = f"operation_{deterministic_uuid4(seed + ':operation')}"
        workspace_config = _workspace_config(request, workspace_id, target)
        domain_profile = _domain_profile(request.workspace_label, domain_profile_id)
        _validate_contract("workspace", workspace_config, actor="cli")
        _validate_contract("domain-profile", domain_profile, actor="cli")
        basis = {
            "protocol": MATERIALIZATION_PROTOCOL,
            "workspace_id": workspace_id,
            "domain_profile_id": domain_profile_id,
            "proposal_id": proposal_id,
            "operation_id": operation_id,
            "target_identity": path_identity(target),
            "parent_attestation": parent_attestation.to_dict(),
            "source_root_attestations": [item.to_dict() for item in source_root_attestations],
            "local_inbox_attestation": local_inbox_attestation.to_dict(),
            "workspace_config": workspace_config,
            "domain_profile": domain_profile,
            "expires_at": _format_timestamp(expires_at),
            "idempotency_key_sha256": canonical_digest(request.idempotency_key),
        }
        proposal_digest = canonical_digest(basis)
        preview = {
            "protocol": MATERIALIZATION_PROTOCOL,
            "workspace_label": request.workspace_label,
            "workspace_name": request.workspace_name,
            "source_root_ids": [item.root_id for item in request.source_roots],
            "external_source_root_count": len(request.source_roots),
            "local_inbox": "existing_external_reference",
            "managed_actions": [
                "create_secure_staging_generation",
                "write_current_workspace_contract",
                "write_generic_domain_profile",
                "initialize_p7d-1_knowledge_scaffold",
                "atomically_publish_workspace",
            ],
            "proposal_digest": proposal_digest,
            "expires_at": _format_timestamp(expires_at),
        }
        return WorkspaceMaterializationProposal(
            protocol=MATERIALIZATION_PROTOCOL,
            workspace_id=workspace_id,
            domain_profile_id=domain_profile_id,
            proposal_id=proposal_id,
            operation_id=operation_id,
            target=target,
            request=request,
            parent_attestation=parent_attestation,
            source_root_attestations=source_root_attestations,
            local_inbox_attestation=local_inbox_attestation,
            workspace_config=workspace_config,
            domain_profile=domain_profile,
            preview=preview,
            proposal_digest=proposal_digest,
            preview_digest=canonical_digest(preview),
            expires_at=_format_timestamp(expires_at),
        )

    def commit(
        self,
        proposal: WorkspaceMaterializationProposal,
        *,
        preview_digest: str | None = None,
        actor: str,
        root_security_controller: RootSecurityController | None = None,
        writer_mutex: WriterMutex | None = None,
    ) -> WorkspaceMaterializationReceipt:
        if actor != "user":
            raise _error(INVALID_AUTHORITY, "/actor", "workspace materialization requires user authority")
        _validate_proposal_integrity(proposal)
        if preview_digest is None or preview_digest != proposal.preview_digest:
            raise _error(PROTECTED_INPUT_CHANGED, "/preview_digest", "approved workspace preview does not match proposal")
        if root_security_controller is None or writer_mutex is None:
            raise _error(MATERIALIZATION_CONFLICT, "", "security controller and writer mutex are required")
        if self.clock() >= _parse_timestamp(proposal.expires_at):
            raise _error(PROTECTED_INPUT_CHANGED, "/expires_at", "workspace proposal is expired")

        request = proposal.request
        parent = request.workspace_parent.expanduser().resolve(strict=True)
        mutex_key = canonical_digest({"parent": path_identity(parent), "target": path_identity(proposal.target)})
        with writer_mutex(mutex_key):
            _revalidate_proposal_roots(proposal, root_security_controller)

            existing = self._existing_receipt(proposal, root_security_controller)
            if existing is not None:
                return existing
            if os.path.lexists(proposal.target):
                recovered = self._resume_published_generation(proposal, root_security_controller)
                if recovered is not None:
                    return recovered
                raise _error(WRITE_CONFLICT, "/workspace_name", "workspace target appeared after preview")

            staging = _staging_path(proposal)
            if os.path.lexists(staging):
                raise _error(WRITE_CONFLICT, "/operation_id", "operation-owned staging already exists")
            created = root_security_controller.secure_create(staging, operation_id=proposal.operation_id)
            _require_secure_attestation(staging, created)
            control = staging / ".research-kb-materialization"
            control.mkdir()
            journal_path = control / "journal.jsonl"
            self._append_journal(proposal, journal_path, "intent", _generation_digest(staging))
            self._phase("journal_written")

            _write_yaml(staging / "workspace.yaml", proposal.workspace_config, proposal.operation_id)
            _write_yaml(staging / "domain-profile.yaml", proposal.domain_profile, proposal.operation_id)
            result = WorkspaceBootstrapService(staging / "workspace.yaml").run()
            if result.exit_code != 0:
                raise _error(MATERIALIZATION_CONFLICT, "/generation", "generated workspace failed initialization")
            final_marker = build_workspace_marker_for_documents(
                proposal.target / "workspace.yaml",
                proposal.workspace_config,
                proposal.domain_profile,
            )
            atomic_write_bytes(
                staging / "knowledge" / ".research-kb" / "workspace.json",
                serialize_json(final_marker),
                f"{proposal.operation_id}-final-marker",
            )
            _validate_staged_generation(staging, final_marker)
            generation_digest = _generation_digest(staging)
            self._append_journal(proposal, journal_path, "generated", generation_digest)
            self._phase("generated_files_written")

            staged_attestation = root_security_controller.verify(staging)
            _require_secure_attestation(staging, staged_attestation)
            if staged_attestation.volume_id != proposal.parent_attestation.volume_id:
                raise _error(MATERIALIZATION_CONFLICT, "/generation", "staging volume changed before publication")
            self._append_journal(proposal, journal_path, "validated", generation_digest)
            self._phase("staged_generation_validated")
            if os.path.lexists(proposal.target):
                raise _error(WRITE_CONFLICT, "/workspace_name", "workspace target appeared before publication")
            os.replace(staging, proposal.target)
            self._phase("published")

            final_attestation = root_security_controller.verify(proposal.target)
            _require_secure_attestation(proposal.target, final_attestation)
            if final_attestation.volume_id != proposal.parent_attestation.volume_id:
                raise _error(MATERIALIZATION_CONFLICT, "/generation", "published workspace volume changed")
            _ = WorkspaceLayout.load(proposal.target / "workspace.yaml")
            if _generation_digest(proposal.target) != generation_digest:
                raise _error(PROTECTED_INPUT_CHANGED, "/generation", "published workspace differs from validated generation")
            receipt = self._write_receipt(proposal, generation_digest)
            self._phase("receipt_persisted")
            self._append_journal(
                proposal,
                proposal.target / ".research-kb-materialization" / "journal.jsonl",
                "complete",
                generation_digest,
                receipt_digest=receipt.receipt_digest,
            )
            self._phase("receipt_written")
            return receipt

    def inspect_recovery(
        self,
        parent: Path,
        operation_id: str,
        root_security_controller: RootSecurityController,
    ) -> WorkspaceMaterializationRecovery:
        parent = parent.expanduser().resolve(strict=True)
        _require_secure_attestation(parent, root_security_controller.inspect(parent))
        stages = sorted(parent.glob(f".*.{operation_id}.stage"))
        finals: list[Path] = []
        receipt_close_pending: list[Path] = []
        published_without_receipt: list[Path] = []
        changed_published: list[Path] = []
        corrupt_published: list[Path] = []
        for child in parent.iterdir():
            if child in stages:
                continue
            if _is_unsafe_link(child) or not child.is_dir():
                continue
            control = child / ".research-kb-materialization"
            if _is_unsafe_link(control):
                continue
            receipt = control / "receipt.json"
            journal_path = control / "journal.jsonl"
            if receipt.is_file() and not _is_unsafe_link(receipt):
                _require_secure_attestation(child, root_security_controller.verify(child))
                record = read_json_document(receipt, record_kind="workspace-materialization-receipt")
                if record.get("operation_id") == operation_id:
                    journal = _read_journal(journal_path)
                    if not journal:
                        corrupt_published.append(child)
                        continue
                    generation_digest = _generation_digest(child)
                    if not _valid_receipt_digest(record) or generation_digest != record.get("generation_digest"):
                        changed_published.append(child)
                    elif (
                        journal
                        and journal[-1]["phase"] == "complete"
                        and journal[-1]["receipt_digest"] == record["receipt_digest"]
                    ):
                        finals.append(child)
                    elif (
                        journal
                        and journal[-1]["phase"] == "validated"
                        and journal[-1]["generation_digest"] == generation_digest
                    ):
                        receipt_close_pending.append(child)
                    else:
                        changed_published.append(child)
            elif journal_path.is_file():
                _require_secure_attestation(child, root_security_controller.verify(child))
                journal = _read_journal(journal_path)
                if not journal:
                    corrupt_published.append(child)
                    continue
                if journal and journal[-1].get("operation_id") == operation_id:
                    if journal[-1].get("phase") == "validated":
                        published_without_receipt.append(child)
                    else:
                        corrupt_published.append(child)
        if (
            len(stages) > 1
            or len(finals) > 1
            or len(receipt_close_pending) > 1
            or len(published_without_receipt) > 1
            or len(changed_published) > 1
            or len(corrupt_published) > 1
            or sum(bool(items) for items in (stages, finals, receipt_close_pending, published_without_receipt, changed_published, corrupt_published)) > 1
        ):
            return WorkspaceMaterializationRecovery(operation_id, "ambiguous", ("manual_resolution_required",))
        if corrupt_published:
            return WorkspaceMaterializationRecovery(operation_id, "corrupt", ("manual_resolution_required",))
        if changed_published:
            return WorkspaceMaterializationRecovery(operation_id, "changed", ("manual_resolution_required",))
        if finals:
            _require_secure_attestation(finals[0], root_security_controller.verify(finals[0]))
            return WorkspaceMaterializationRecovery(operation_id, "complete", ("no_change",))
        if receipt_close_pending:
            return WorkspaceMaterializationRecovery(
                operation_id,
                "receipt_close_pending",
                ("complete_matching_receipt_journal",),
            )
        if published_without_receipt:
            published = published_without_receipt[0]
            _require_secure_attestation(published, root_security_controller.verify(published))
            journal = _read_journal(published / ".research-kb-materialization" / "journal.jsonl")
            if journal[-1].get("generation_digest") == _generation_digest(published):
                return WorkspaceMaterializationRecovery(
                    operation_id,
                    "published_receipt_missing",
                    ("resume_matching_published_generation",),
                )
            return WorkspaceMaterializationRecovery(operation_id, "changed", ("manual_resolution_required",))
        if not stages:
            return WorkspaceMaterializationRecovery(operation_id, "absent", ())
        stage = stages[0]
        _require_secure_attestation(stage, root_security_controller.verify(stage))
        journal = _read_journal(stage / ".research-kb-materialization" / "journal.jsonl")
        if not journal or journal[-1].get("operation_id") != operation_id:
            return WorkspaceMaterializationRecovery(operation_id, "foreign", ("manual_resolution_required",))
        if journal[-1].get("generation_digest") != _generation_digest(stage):
            return WorkspaceMaterializationRecovery(operation_id, "changed", ("manual_resolution_required",))
        return WorkspaceMaterializationRecovery(operation_id, "owned_incomplete", ("discard_unchanged_owned_staging",))

    def recover(
        self,
        proposal: WorkspaceMaterializationProposal,
        *,
        action: str,
        actor: str,
        root_security_controller: RootSecurityController,
        writer_mutex: WriterMutex,
    ) -> WorkspaceMaterializationReceipt | WorkspaceMaterializationRecovery:
        if actor != "user":
            raise _error(INVALID_AUTHORITY, "/actor", "workspace materialization recovery requires user authority")
        _validate_proposal_integrity(proposal)
        parent = proposal.request.workspace_parent.expanduser().resolve(strict=True)
        mutex_key = canonical_digest({"parent": path_identity(parent), "target": path_identity(proposal.target)})
        with writer_mutex(mutex_key):
            _revalidate_proposal_roots(proposal, root_security_controller)
            recovery = self.inspect_recovery(parent, proposal.operation_id, root_security_controller)
            if action not in recovery.actions or action == "manual_resolution_required":
                raise _error(PROTECTED_INPUT_CHANGED, "/recovery/action", "recovery action is not current for this operation")
            if action == "no_change":
                receipt = self._existing_receipt(proposal, root_security_controller)
                if receipt is None:
                    raise _error(PROTECTED_INPUT_CHANGED, "/recovery", "completed materialization receipt is unavailable")
                return receipt
            if action in {"resume_matching_published_generation", "complete_matching_receipt_journal"}:
                receipt = self._existing_receipt(proposal, root_security_controller)
                if receipt is None:
                    receipt = self._resume_published_generation(proposal, root_security_controller)
                if receipt is None:
                    raise _error(PROTECTED_INPUT_CHANGED, "/recovery", "published materialization no longer matches")
                return receipt
            if action == "discard_unchanged_owned_staging":
                staging = _staging_path(proposal)
                journal = _read_journal(staging / ".research-kb-materialization" / "journal.jsonl")
                if (
                    not journal
                    or journal[-1].get("operation_id") != proposal.operation_id
                    or journal[-1].get("proposal_digest") != proposal.proposal_digest
                    or journal[-1].get("generation_digest") != _generation_digest(staging)
                    or _contains_unsafe_entry(staging)
                ):
                    raise _error(PROTECTED_INPUT_CHANGED, "/recovery", "operation-owned staging changed before recovery")
                shutil.rmtree(staging)
                if os.path.lexists(staging):
                    raise _error(MATERIALIZATION_CONFLICT, "/recovery", "operation-owned staging could not be removed")
                return WorkspaceMaterializationRecovery(proposal.operation_id, "discarded", ())
        raise _error(MATERIALIZATION_CONFLICT, "/recovery/action", "recovery action is unsupported")

    def _resume_published_generation(
        self,
        proposal: WorkspaceMaterializationProposal,
        root_security_controller: RootSecurityController,
    ) -> WorkspaceMaterializationReceipt | None:
        target = proposal.target
        if not target.is_dir():
            return None
        _require_secure_attestation(target, root_security_controller.verify(target))
        journal_path = target / ".research-kb-materialization" / "journal.jsonl"
        journal = _read_journal(journal_path)
        if not journal:
            return None
        head = journal[-1]
        if (
            head.get("operation_id") != proposal.operation_id
            or head.get("proposal_id") != proposal.proposal_id
            or head.get("proposal_digest") != proposal.proposal_digest
            or head.get("preview_digest") != proposal.preview_digest
            or head.get("phase") != "validated"
        ):
            return None
        generation_digest = _generation_digest(target)
        if head.get("generation_digest") != generation_digest:
            raise _error(PROTECTED_INPUT_CHANGED, "/generation", "published generation changed before recovery")
        _ = WorkspaceLayout.load(target / "workspace.yaml")
        receipt = self._write_receipt(proposal, generation_digest)
        self._append_journal(
            proposal,
            journal_path,
            "complete",
            generation_digest,
            receipt_digest=receipt.receipt_digest,
        )
        return replace(receipt, result="recovered")

    def _existing_receipt(
        self,
        proposal: WorkspaceMaterializationProposal,
        root_security_controller: RootSecurityController,
    ) -> WorkspaceMaterializationReceipt | None:
        if os.path.lexists(proposal.target):
            _require_secure_attestation(proposal.target, root_security_controller.verify(proposal.target))
        receipt_path = proposal.target / ".research-kb-materialization" / "receipt.json"
        if _is_unsafe_link(receipt_path) or not receipt_path.is_file():
            return None
        record = read_json_document(receipt_path, record_kind="workspace-materialization-receipt")
        _validate_contract("workspace-materialization-receipt", record, actor="stored")
        if not _valid_receipt_digest(record):
            raise _error(PROTECTED_INPUT_CHANGED, "/receipt_digest", "workspace materialization receipt digest is invalid")
        if (
            record["operation_id"] != proposal.operation_id
            or record["proposal_digest"] != proposal.proposal_digest
            or record["preview_digest"] != proposal.preview_digest
        ):
            raise _error(WRITE_CONFLICT, "/workspace_name", "existing workspace belongs to a different proposal")
        if _generation_digest(proposal.target) != record["generation_digest"]:
            raise _error(PROTECTED_INPUT_CHANGED, "/generation_digest", "existing workspace generation changed")
        journal_path = proposal.target / ".research-kb-materialization" / "journal.jsonl"
        journal = _read_journal(journal_path)
        if not journal:
            raise _error(PROTECTED_INPUT_CHANGED, "/journal", "completed workspace journal is missing or invalid")
        head = journal[-1]
        if head["phase"] == "validated" and head["generation_digest"] == record["generation_digest"]:
            self._append_journal(
                proposal,
                journal_path,
                "complete",
                record["generation_digest"],
                receipt_digest=record["receipt_digest"],
            )
            return _receipt_from_record(record, result="recovered")
        if head["phase"] != "complete" or head["receipt_digest"] != record["receipt_digest"]:
            raise _error(PROTECTED_INPUT_CHANGED, "/journal", "completed workspace journal does not match the receipt")
        return _receipt_from_record(record, result="no_change")

    def _write_receipt(
        self,
        proposal: WorkspaceMaterializationProposal,
        generation_digest: str,
    ) -> WorkspaceMaterializationReceipt:
        payload = {
            "schema_version": "1.0",
            "protocol": MATERIALIZATION_PROTOCOL,
            "operation_id": proposal.operation_id,
            "proposal_id": proposal.proposal_id,
            "workspace_id": proposal.workspace_id,
            "proposal_digest": proposal.proposal_digest,
            "preview_digest": proposal.preview_digest,
            "generation_digest": generation_digest,
            "created_at": timestamp(self.clock),
        }
        record = {**payload, "receipt_digest": canonical_digest(payload), "result": "created"}
        _validate_contract("workspace-materialization-receipt", record, actor="cli")
        target = proposal.target / ".research-kb-materialization" / "receipt.json"
        atomic_write_bytes(target, serialize_json(record), proposal.operation_id)
        return _receipt_from_record(record)

    def _append_journal(
        self,
        proposal: WorkspaceMaterializationProposal,
        path: Path,
        phase: str,
        generation_digest: str,
        *,
        receipt_digest: str | None = None,
    ) -> None:
        record = {
            "schema_version": "1.0",
            "protocol": MATERIALIZATION_PROTOCOL,
            "operation_id": proposal.operation_id,
            "proposal_id": proposal.proposal_id,
            "workspace_id": proposal.workspace_id,
            "proposal_digest": proposal.proposal_digest,
            "preview_digest": proposal.preview_digest,
            "target_identity": path_identity(proposal.target),
            "phase": phase,
            "generation_digest": generation_digest,
            "receipt_digest": receipt_digest,
            "recorded_at": timestamp(self.clock),
        }
        _validate_contract("workspace-materialization-journal", record, actor="cli")
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = _read_journal(path)
        if path.exists() and not existing:
            raise _error(PROTECTED_INPUT_CHANGED, "/journal", "workspace materialization journal is invalid")
        if not _valid_journal_chain([*existing, record]):
            raise _error(PROTECTED_INPUT_CHANGED, "/journal", "workspace materialization journal transition is invalid")
        with path.open("ab") as handle:
            handle.write(serialize_json(record))
            handle.flush()
            os.fsync(handle.fileno())

    def _phase(self, phase: str) -> None:
        if self.phase_hook is not None:
            self.phase_hook(phase)


def _workspace_config(
    request: WorkspaceMaterializationRequest,
    workspace_id: str,
    target: Path,
) -> dict[str, Any]:
    try:
        local_inbox = Path(os.path.relpath(request.local_inbox.resolve(strict=True), target)).as_posix()
    except ValueError as error:
        raise _error(
            MATERIALIZATION_CONFLICT,
            "/local_inbox",
            "local inbox must share the managed workspace volume",
        ) from error
    return {
        "contract_version": "1.0",
        "workspace": {
            "id": workspace_id,
            "knowledge_root": "./knowledge",
            "source_roots": [
                {"root_id": item.root_id, "path": item.path.expanduser().resolve(strict=True).as_posix(), "read_only_assets": True}
                for item in request.source_roots
            ],
            "local_inbox": local_inbox,
            "domain_profile": "./domain-profile.yaml",
        },
        "runtime": {"path_serialization": "workspace_relative_posix", "default_encoding": "utf-8", "line_ending": "lf"},
    }


def _proposal_seed(target: Path, idempotency_key: str) -> str:
    return f"{MATERIALIZATION_PROTOCOL}:{path_identity(target)}:{idempotency_key}"


def _validate_proposal_integrity(proposal: WorkspaceMaterializationProposal) -> None:
    request = proposal.request
    parent = request.workspace_parent.expanduser().resolve(strict=True)
    target = parent / request.workspace_name
    seed = _proposal_seed(target, request.idempotency_key)
    expected_workspace_id = f"workspace_{deterministic_uuid4(seed + ':workspace')}"
    expected_domain_profile_id = f"domain-{canonical_digest({'seed': seed, 'kind': 'domain'})[:20]}"
    expected_proposal_id = f"proposal_{deterministic_uuid4(seed + ':proposal')}"
    expected_operation_id = f"operation_{deterministic_uuid4(seed + ':operation')}"
    expected_config = _workspace_config(request, expected_workspace_id, target)
    expected_profile = _domain_profile(request.workspace_label, expected_domain_profile_id)
    expected_expires_at = _format_timestamp(_as_utc(request.expires_at))
    basis = {
        "protocol": MATERIALIZATION_PROTOCOL,
        "workspace_id": expected_workspace_id,
        "domain_profile_id": expected_domain_profile_id,
        "proposal_id": expected_proposal_id,
        "operation_id": expected_operation_id,
        "target_identity": path_identity(target),
        "parent_attestation": proposal.parent_attestation.to_dict(),
        "source_root_attestations": [item.to_dict() for item in proposal.source_root_attestations],
        "local_inbox_attestation": proposal.local_inbox_attestation.to_dict(),
        "workspace_config": expected_config,
        "domain_profile": expected_profile,
        "expires_at": expected_expires_at,
        "idempotency_key_sha256": canonical_digest(request.idempotency_key),
    }
    expected_digest = canonical_digest(basis)
    expected_preview = {
        "protocol": MATERIALIZATION_PROTOCOL,
        "workspace_label": request.workspace_label,
        "workspace_name": request.workspace_name,
        "source_root_ids": [item.root_id for item in request.source_roots],
        "external_source_root_count": len(request.source_roots),
        "local_inbox": "existing_external_reference",
        "managed_actions": [
            "create_secure_staging_generation",
            "write_current_workspace_contract",
            "write_generic_domain_profile",
            "initialize_p7d-1_knowledge_scaffold",
            "atomically_publish_workspace",
        ],
        "proposal_digest": expected_digest,
        "expires_at": expected_expires_at,
    }
    valid = (
        proposal.protocol == MATERIALIZATION_PROTOCOL
        and proposal.expires_at == expected_expires_at
        and proposal.target == target
        and proposal.workspace_id == expected_workspace_id
        and proposal.domain_profile_id == expected_domain_profile_id
        and proposal.proposal_id == expected_proposal_id
        and proposal.operation_id == expected_operation_id
        and proposal.workspace_config == expected_config
        and proposal.domain_profile == expected_profile
        and proposal.proposal_digest == expected_digest
        and proposal.preview == expected_preview
        and proposal.preview_digest == canonical_digest(expected_preview)
    )
    if not valid:
        raise _error(PROTECTED_INPUT_CHANGED, "/proposal", "workspace materialization proposal changed after preview")


def _domain_profile(label: str, profile_id: str) -> dict[str, Any]:
    return {
        "contract_version": "1.0",
        "domain_profile": {"id": profile_id, "name": label, "version": "1.0"},
        "paper_card_sections": [
            {"section_id": section, "label": section.replace("_", " ").title()} for section in _SECTIONS
        ],
        "evidence_axes": ["input", "process", "outcome"],
        "question_types": ["mechanism", "comparison"],
        "terminology": {},
        "step7_extensions": {},
    }


def _validate_request(request: WorkspaceMaterializationRequest, *, now: datetime, expires_at: datetime) -> None:
    for value, path in ((request.workspace_name, "/workspace_name"), (request.workspace_label, "/workspace_label")):
        if not isinstance(value, str) or not _NAME.fullmatch(value) or value.endswith((".", " ")):
            raise _error(MATERIALIZATION_CONFLICT, path, "workspace name or label is not supported")
    reserved_basename = request.workspace_name.casefold().split(".", 1)[0]
    if reserved_basename in _RESERVED or request.workspace_name in {".", ".."}:
        raise _error(MATERIALIZATION_CONFLICT, "/workspace_name", "workspace name is reserved")
    if not request.idempotency_key or len(request.idempotency_key.encode("utf-8")) > 1024:
        raise _error(MATERIALIZATION_CONFLICT, "/idempotency_key", "idempotency key is missing or too large")
    if expires_at <= now:
        raise _error(PROTECTED_INPUT_CHANGED, "/expires_at", "workspace proposal expiry is not in the future")
    if not request.source_roots:
        raise _error(MATERIALIZATION_CONFLICT, "/source_roots", "at least one source root is required")
    root_ids = [item.root_id for item in request.source_roots]
    if len(root_ids) != len(set(root_ids)) or any(not _SLUG.fullmatch(item) for item in root_ids):
        raise _error(MATERIALIZATION_CONFLICT, "/source_roots", "source root IDs must be unique slugs")


def _validate_external_roots(request: WorkspaceMaterializationRequest, target: Path) -> None:
    roots = [item.path.expanduser().resolve(strict=True) for item in request.source_roots]
    if any(not root.is_dir() for root in roots):
        raise _error(MATERIALIZATION_CONFLICT, "/source_roots", "source root is not an existing directory")
    inbox = request.local_inbox.expanduser().resolve(strict=True)
    if not inbox.is_dir():
        raise _error(MATERIALIZATION_CONFLICT, "/local_inbox", "local inbox is not an existing directory")
    if not any(inbox == root or inbox.is_relative_to(root) for root in roots):
        raise _error(MATERIALIZATION_CONFLICT, "/local_inbox", "local inbox is not addressable through a source root")
    normalized_target = target.resolve(strict=False)
    if any(_overlap(normalized_target, root) for root in (*roots, inbox)):
        raise _error(MATERIALIZATION_CONFLICT, "/workspace_name", "workspace target overlaps an external source root")


def _require_secure_attestation(path: Path, attestation: RootSecurityAttestation) -> None:
    valid = (
        attestation.path_identity == path_identity(path)
        and attestation.filesystem.upper() == "NTFS"
        and attestation.local
        and attestation.reparse_free
        and attestation.acl_policy_id == ROOT_SECURITY_POLICY
        and attestation.acl_secure
        and bool(attestation.volume_id)
    )
    if not valid:
        raise _error(MATERIALIZATION_CONFLICT, "/root_security", "root security attestation is not acceptable")


def _require_external_source_attestation(path: Path, attestation: RootSecurityAttestation) -> None:
    valid = (
        attestation.path_identity == path_identity(path)
        and attestation.local
        and attestation.reparse_free
        and bool(attestation.volume_id)
        and bool(attestation.filesystem)
    )
    if not valid:
        raise _error(MATERIALIZATION_CONFLICT, "/source_roots", "source root security attestation is not acceptable")


def _revalidate_proposal_roots(
    proposal: WorkspaceMaterializationProposal,
    root_security_controller: RootSecurityController,
) -> None:
    request = proposal.request
    parent = request.workspace_parent.expanduser().resolve(strict=True)
    current_parent = root_security_controller.inspect(parent)
    _require_secure_attestation(parent, current_parent)
    if current_parent != proposal.parent_attestation:
        raise _error(PROTECTED_INPUT_CHANGED, "/workspace_parent", "workspace parent security changed after preview")
    _validate_external_roots(request, proposal.target)
    if len(proposal.source_root_attestations) != len(request.source_roots):
        raise _error(PROTECTED_INPUT_CHANGED, "/source_roots", "source root security basis changed after preview")
    for item, expected in zip(request.source_roots, proposal.source_root_attestations, strict=True):
        source_path = item.path.expanduser().absolute()
        current = root_security_controller.inspect(source_path)
        _require_external_source_attestation(source_path, current)
        if current != expected:
            raise _error(PROTECTED_INPUT_CHANGED, "/source_roots", "source root security changed after preview")
    local_inbox = request.local_inbox.expanduser().resolve(strict=True)
    current_local_inbox = root_security_controller.inspect(local_inbox)
    _require_secure_attestation(local_inbox, current_local_inbox)
    if current_local_inbox != proposal.local_inbox_attestation:
        raise _error(PROTECTED_INPUT_CHANGED, "/local_inbox", "local inbox security changed after preview")


def _generation_digest(root: Path) -> str:
    inventory: list[dict[str, Any]] = []
    if not root.exists():
        return canonical_digest(inventory)
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        if relative == ".research-kb-materialization" or relative.startswith(".research-kb-materialization/"):
            continue
        if _is_unsafe_link(path):
            inventory.append({"path": relative, "type": "unsupported"})
        elif path.is_dir():
            inventory.append({"path": relative, "type": "directory"})
        elif path.is_file():
            inventory.append({"path": relative, "type": "file", "sha256": _sha256_file(path), "bytes": path.stat().st_size})
        else:
            inventory.append({"path": relative, "type": "unsupported"})
    return canonical_digest(inventory)


def _staging_path(proposal: WorkspaceMaterializationProposal) -> Path:
    return proposal.request.workspace_parent / f".{proposal.request.workspace_name}.{proposal.operation_id}.stage"


def _read_journal(path: Path) -> list[dict[str, Any]]:
    if _is_unsafe_link(path) or not path.is_file():
        return []
    try:
        if path.stat().st_size > _JOURNAL_MAX_BYTES:
            return []
    except OSError:
        return []
    records: list[dict[str, Any]] = []
    for raw in path.read_bytes().splitlines():
        try:
            record = json.loads(raw.decode("utf-8"))
        except (UnicodeError, ValueError):
            return []
        if not isinstance(record, dict) or validate_record("workspace-materialization-journal", record, actor="stored"):
            return []
        records.append(record)
    return records if _valid_journal_chain(records) else []


def _valid_journal_chain(records: list[dict[str, Any]]) -> bool:
    phases = [record.get("phase") for record in records]
    if phases not in (
        ["intent"],
        ["intent", "generated"],
        ["intent", "generated", "validated"],
        ["intent", "generated", "validated", "complete"],
    ):
        return False
    identity_fields = (
        "protocol",
        "operation_id",
        "proposal_id",
        "workspace_id",
        "proposal_digest",
        "preview_digest",
        "target_identity",
    )
    first = records[0]
    if any(any(record.get(field) != first.get(field) for field in identity_fields) for record in records[1:]):
        return False
    if any(record.get("receipt_digest") is not None for record in records[:-1]):
        return False
    if phases[-1] == "complete" and records[-1].get("receipt_digest") is None:
        return False
    if phases[-1] != "complete" and records[-1].get("receipt_digest") is not None:
        return False
    generated = [record["generation_digest"] for record in records if record["phase"] != "intent"]
    return len(set(generated)) <= 1


def _write_yaml(path: Path, value: dict[str, Any], write_id: str) -> None:
    content = yaml.safe_dump(value, allow_unicode=True, sort_keys=False).encode("utf-8")
    atomic_write_bytes(path, content, f"{write_id}-{path.stem}")


def _validate_staged_generation(staging: Path, expected_marker: dict[str, Any]) -> None:
    for relative in MANAGED_DIRECTORIES:
        path = staging / "knowledge" / Path(*relative.split("/"))
        if not path.is_dir():
            raise _error(MATERIALIZATION_CONFLICT, "/generation", "staged workspace scaffold is incomplete")
    marker = read_json_document(
        staging / "knowledge" / ".research-kb" / "workspace.json",
        record_kind="workspace-marker",
    )
    _validate_contract("workspace-marker", marker, actor="stored")
    if marker != expected_marker:
        raise _error(PROTECTED_INPUT_CHANGED, "/generation", "staged workspace marker does not bind final target")


def _validate_contract(kind: str, record: dict[str, Any], *, actor: str) -> None:
    diagnostics = validate_record(kind, record, actor=actor)
    if diagnostics:
        raise ResearchKBError(diagnostics[0])


def _receipt_from_record(record: dict[str, Any], *, result: str | None = None) -> WorkspaceMaterializationReceipt:
    return WorkspaceMaterializationReceipt(
        operation_id=record["operation_id"], proposal_id=record["proposal_id"], workspace_id=record["workspace_id"],
        proposal_digest=record["proposal_digest"], preview_digest=record["preview_digest"],
        generation_digest=record["generation_digest"], receipt_digest=record["receipt_digest"],
        result=result or record["result"], created_at=record["created_at"],
    )


def _valid_receipt_digest(record: dict[str, Any]) -> bool:
    payload = {key: value for key, value in record.items() if key not in {"receipt_digest", "result"}}
    return canonical_digest(payload) == record.get("receipt_digest")


def _as_utc(value: object) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise _error(MATERIALIZATION_CONFLICT, "/expires_at", "expiry must be timezone-aware")
    return value.astimezone(UTC)


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _overlap(left: Path, right: Path) -> bool:
    return left == right or left.is_relative_to(right) or right.is_relative_to(left)


def _is_unsafe_link(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        if callable(is_junction) and is_junction():
            return True
        attributes = path.lstat().st_file_attributes
    except AttributeError:
        return False
    except OSError:
        return True
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _contains_unsafe_entry(root: Path) -> bool:
    try:
        return _is_unsafe_link(root) or any(_is_unsafe_link(path) for path in root.rglob("*"))
    except OSError:
        return True


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _error(code: str, path: str, message: str) -> ResearchKBError:
    return ResearchKBError(Diagnostic(code, "workspace-materialization", None, path, message))


__all__ = ["WorkspaceMaterializationApplicationService"]
