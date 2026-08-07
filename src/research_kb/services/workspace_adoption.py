from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from research_kb.application import APPLICATION_SERVICE_INTERFACE_VERSION
from research_kb.errors import INCOMPLETE_TRANSACTION, PROTECTED_INPUT_CHANGED, Diagnostic, ResearchKBError
from research_kb.services.workspace_storage import WorkspaceStorageRoots
from research_kb.storage.json_io import file_sha256
from research_kb.workspace import WorkspaceLayout
from research_kb.workspace_validation import (
    SourceRootBinding,
    WorkspaceContext,
    canonical_path_identity,
    validate_initialized_workspace,
)


@dataclass(frozen=True, slots=True)
class WorkspaceAdoptionInspection:
    descriptor: dict[str, Any]
    writable_roots: WorkspaceStorageRoots
    source_roots: tuple[SourceRootBinding, ...]
    basis_digest: str


class WorkspaceAdoptionApplicationService:
    """Build a stable, read-only basis for adopting an initialized workspace."""

    def inspect(self, config_path: Path) -> WorkspaceAdoptionInspection:
        # Guardian imports focused service modules through the services package.
        # Resolve it only after package initialization to avoid an import cycle.
        from research_kb.guardian import GuardianService

        config_path = Path(config_path).resolve(strict=False)
        context = validate_initialized_workspace(config_path).require_valid()
        try:
            protected_before = _protected_input_snapshot(context)
        except OSError as error:
            raise _protected_input_changed() from error

        # Keep one validated context for the inspection. Reloading a layout here
        # could pair Guardian reads with a different config or profile.
        layout = WorkspaceLayout._from_context(context)
        try:
            guardian = GuardianService(layout).check(write_report=False).report
        finally:
            _require_protected_inputs_unchanged(config_path, protected_before)

        findings = sorted(
            (
                {
                    "code": item["code"],
                    "severity": item["severity"],
                    "record_ref": item.get("record_ref"),
                    "message": item["message"],
                }
                for item in guardian["findings"]
            ),
            key=lambda item: (
                item["severity"],
                item["code"],
                item["record_ref"] or "",
                item["message"],
            ),
        )
        transaction_findings = [item for item in findings if item["code"] == INCOMPLETE_TRANSACTION]
        ineligibility_reasons = []
        if not context.local_inbox.is_dir():
            ineligibility_reasons.append("local_inbox_missing")
        profile = context.domain_profile.data["domain_profile"]
        identity = {
            "workspace_id": context.workspace_id,
            "domain_profile_id": context.domain_profile_id,
            "domain_name": profile["name"],
            "domain_version": profile["version"],
        }
        source_documents = dict(protected_before["documents"])
        protected_roots = protected_before["roots"]
        root_identity = {
            "workspace_config_root": protected_roots["workspace_config_root"],
            "knowledge_root": protected_roots["knowledge_root"],
            "local_inbox": protected_roots["local_inbox"],
            "source_roots": [
                {
                    "root_id": root_id,
                    "path": path,
                    "read_only_assets": read_only_assets,
                }
                for root_id, path, read_only_assets in protected_roots["source_roots"]
            ],
        }
        admissible = (
            guardian["status"] != "failure"
            and not transaction_findings
            and not ineligibility_reasons
        )
        basis = {
            "application_service_interface_version": APPLICATION_SERVICE_INTERFACE_VERSION,
            "workspace": identity,
            "source_documents": source_documents,
            "roots": root_identity,
            "guardian": {"status": guardian["status"], "findings": findings},
            "transaction_recovery": {
                "status": "current" if not transaction_findings else "recovery_required",
                "findings": transaction_findings,
            },
            "adoption": {
                "admissible": admissible,
                "ineligibility_reasons": ineligibility_reasons,
                "protected_inputs_unchanged": True,
            },
        }
        basis_digest = hashlib.sha256(_canonical_bytes(basis)).hexdigest()
        descriptor = {
            "status": "success",
            "interface_version": "1.0",
            "application_service_interface_version": APPLICATION_SERVICE_INTERFACE_VERSION,
            "persistent_writes": 0,
            "canonical_scientific_write": False,
            "workspace": identity,
            "guardian": {
                "status": guardian["status"],
                "finding_count": len(findings),
                "error_count": sum(item["severity"] == "error" for item in findings),
                "warning_count": sum(item["severity"] == "warning" for item in findings),
            },
            "transaction_recovery": {
                "status": "current" if not transaction_findings else "recovery_required",
                "action_count": len(transaction_findings),
            },
            "admissible": admissible,
            "adoption_status": "admissible" if admissible else "ineligible",
            "ineligibility_reasons": ineligibility_reasons,
            "protected_inputs_unchanged": True,
            "adoption_basis_digest": basis_digest,
        }
        return WorkspaceAdoptionInspection(
            descriptor=descriptor,
            writable_roots=WorkspaceStorageRoots(
                workspace_config_root=context.config.path.parent,
                knowledge_root=context.knowledge_root,
                local_inbox=context.local_inbox,
            ),
            source_roots=context.source_root_items,
            basis_digest=basis_digest,
        )


def _required_digest(path: Path) -> str:
    digest = file_sha256(path)
    if digest is None:
        raise OSError("workspace adoption input is unavailable")
    return digest


def _path_identity(path: Path) -> str:
    return canonical_path_identity(path)


def _protected_input_snapshot(context: WorkspaceContext) -> dict[str, Any]:
    return {
        "documents": {
            "workspace_config_sha256": _required_digest(context.config.path),
            "domain_profile_sha256": _required_digest(context.domain_profile.path),
            "workspace_marker_sha256": _required_digest(context.marker_path),
        },
        "roots": {
            "workspace_config_root": _path_identity(context.config.path.parent),
            "knowledge_root": _path_identity(context.knowledge_root),
            "local_inbox": _path_identity(context.local_inbox),
            "source_roots": tuple(
                (
                    item.root_id,
                    _path_identity(item.path),
                    item.read_only_assets,
                )
                for item in sorted(
                    context.source_root_items,
                    key=lambda value: (value.root_id, _path_identity(value.path)),
                )
            ),
        },
    }


def _require_protected_inputs_unchanged(
    config_path: Path,
    protected_before: dict[str, Any],
) -> None:
    try:
        current_context = validate_initialized_workspace(config_path).require_valid()
        protected_after = _protected_input_snapshot(current_context)
    except (OSError, ResearchKBError) as error:
        raise _protected_input_changed() from error
    if protected_after != protected_before:
        raise _protected_input_changed()


def _protected_input_changed() -> ResearchKBError:
    return ResearchKBError(
        Diagnostic(
            PROTECTED_INPUT_CHANGED,
            "workspace-adoption",
            None,
            "/protected_inputs",
            "workspace adoption protected input changed during inspection",
        )
    )


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


__all__ = ["WorkspaceAdoptionApplicationService", "WorkspaceAdoptionInspection"]
