from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


MATERIALIZATION_PROTOCOL = "workspace-materialization@1.0"
ROOT_SECURITY_POLICY = "windows-acl-policy@1.0"


def path_identity(path: Path) -> str:
    canonical = str(path.expanduser().resolve(strict=False)).replace("\\", "/").casefold()
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def canonical_digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def deterministic_uuid4(seed: str) -> uuid.UUID:
    raw = bytearray(hashlib.sha256(seed.encode("utf-8")).digest()[:16])
    raw[6] = (raw[6] & 0x0F) | 0x40
    raw[8] = (raw[8] & 0x3F) | 0x80
    return uuid.UUID(bytes=bytes(raw))


@dataclass(frozen=True, slots=True)
class ExternalSourceRoot:
    root_id: str
    path: Path


@dataclass(frozen=True, slots=True)
class RootSecurityAttestation:
    path_identity: str
    volume_id: str
    filesystem: str
    local: bool
    reparse_free: bool
    acl_policy_id: str
    acl_secure: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "path_identity": self.path_identity,
            "volume_id": self.volume_id,
            "filesystem": self.filesystem,
            "local": self.local,
            "reparse_free": self.reparse_free,
            "acl_policy_id": self.acl_policy_id,
            "acl_secure": self.acl_secure,
        }


class RootSecurityController(Protocol):
    def inspect(self, path: Path) -> RootSecurityAttestation: ...

    def secure_create(self, path: Path, *, operation_id: str) -> RootSecurityAttestation: ...

    def verify(self, path: Path) -> RootSecurityAttestation: ...


@dataclass(frozen=True, slots=True)
class WorkspaceMaterializationRequest:
    workspace_parent: Path
    workspace_name: str
    workspace_label: str
    source_roots: tuple[ExternalSourceRoot, ...]
    local_inbox: Path
    idempotency_key: str
    expires_at: object


@dataclass(frozen=True, slots=True)
class WorkspaceMaterializationProposal:
    protocol: str
    workspace_id: str
    domain_profile_id: str
    proposal_id: str
    operation_id: str
    target: Path
    request: WorkspaceMaterializationRequest
    parent_attestation: RootSecurityAttestation
    source_root_attestations: tuple[RootSecurityAttestation, ...]
    local_inbox_attestation: RootSecurityAttestation
    workspace_config: dict[str, Any]
    domain_profile: dict[str, Any]
    preview: dict[str, Any]
    proposal_digest: str
    preview_digest: str
    expires_at: str


@dataclass(frozen=True, slots=True)
class WorkspaceMaterializationReceipt:
    operation_id: str
    proposal_id: str
    workspace_id: str
    proposal_digest: str
    preview_digest: str
    generation_digest: str
    receipt_digest: str
    result: str
    created_at: str


@dataclass(frozen=True, slots=True)
class WorkspaceMaterializationRecovery:
    operation_id: str
    state: str
    actions: tuple[str, ...]


__all__ = [
    "ExternalSourceRoot",
    "MATERIALIZATION_PROTOCOL",
    "ROOT_SECURITY_POLICY",
    "RootSecurityAttestation",
    "RootSecurityController",
    "WorkspaceMaterializationProposal",
    "WorkspaceMaterializationReceipt",
    "WorkspaceMaterializationRecovery",
    "WorkspaceMaterializationRequest",
    "canonical_digest",
    "deterministic_uuid4",
    "path_identity",
]
