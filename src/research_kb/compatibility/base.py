from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from research_kb.workspace import WorkspaceLayout


@dataclass(frozen=True, slots=True)
class CompatibilitySourceRef:
    root_role: str
    relative_path: str

    def to_dict(self) -> dict[str, str]:
        return {"root_role": self.root_role, "relative_path": self.relative_path}


@dataclass(frozen=True, slots=True)
class DifferenceCandidate:
    difference_type: str
    severity: str
    field_path: str
    legacy_value_digest: str | None
    projected_value_digest: str | None
    loss_scope: str | None
    message: str
    risk: str
    recommended_action: str
    private_detail_ref: CompatibilitySourceRef | None = None


@dataclass(frozen=True, slots=True)
class InventoryCandidate:
    record_kind: str
    legacy_id: str
    source_ref: CompatibilitySourceRef
    disposition: str
    projected_kind: str | None
    projection_status: str
    record_role: str
    unsupported_fields: tuple[str, ...]
    diagnostic_codes: tuple[str, ...]
    differences: tuple[DifferenceCandidate, ...]


class CompatibilityContext:
    __slots__ = ("_layout",)

    def __init__(self, layout: WorkspaceLayout):
        self._layout = layout

    @property
    def workspace_id(self) -> str:
        return self._layout.workspace_id

    @property
    def source_root_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._layout.source_roots))

    def resolve_source(self, source_ref: CompatibilitySourceRef) -> Path:
        _, path = self._layout.resolve_source(source_ref.root_role, source_ref.relative_path)
        return path


class LegacyReaderAdapter(Protocol):
    adapter_id: str
    adapter_version: str
    source_system: str
    supported_contract_versions: tuple[str, ...]

    def protected_inputs(self, context: CompatibilityContext) -> Iterable[CompatibilitySourceRef]: ...

    def iter_inventory(self, context: CompatibilityContext) -> Iterable[InventoryCandidate]: ...
