from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from research_kb.compatibility import (
    CompatibilityContext,
    CompatibilitySourceRef,
    DifferenceCandidate,
    InventoryCandidate,
)
from research_kb.services.bootstrap import WorkspaceBootstrapService
from research_kb.workspace import WorkspaceLayout


ROOT = Path(__file__).resolve().parents[1]


def make_compatibility_workspace(tmp_path: Path, domain: str = "alpha") -> WorkspaceLayout:
    fixture_name = f"domain_{domain}"
    runtime_root = tmp_path / fixture_name
    shutil.copytree(ROOT / "tests" / "fixtures" / "workspaces" / fixture_name, runtime_root)
    shutil.copy2(
        ROOT / "tests" / "fixtures" / "compatibility" / fixture_name / "legacy.jsonl",
        runtime_root / "sources" / "legacy.jsonl",
    )
    result = WorkspaceBootstrapService(runtime_root / "workspace.yaml").run()
    if result.exit_code != 0:
        raise AssertionError(result.to_dict())
    return WorkspaceLayout.load(runtime_root / "workspace.yaml")


class SyntheticLegacyAdapter:
    adapter_version = "1.0"
    source_system = "synthetic-legacy"
    supported_contract_versions = ("1.0",)

    def __init__(self, domain: str = "alpha", *, reverse: bool = False):
        self.domain = domain
        self.reverse = reverse
        self.adapter_id = f"synthetic-{domain}-legacy"
        self.root_id = f"{domain}-sources"

    def protected_inputs(self, context: CompatibilityContext):
        del context
        return (CompatibilitySourceRef(self.root_id, "legacy.jsonl"),)

    def iter_inventory(self, context: CompatibilityContext):
        source = context.resolve_source(CompatibilitySourceRef(self.root_id, "legacy.jsonl"))
        records = [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines()]
        if self.reverse:
            records.reverse()
        for record in records:
            differences = tuple(
                DifferenceCandidate(
                    difference_type=item["difference_type"],
                    severity=item["severity"],
                    field_path=item["field_path"],
                    legacy_value_digest=_digest(item["legacy_value"]),
                    projected_value_digest=_digest(item["projected_value"]),
                    loss_scope=item["loss_scope"],
                    message=f"Synthetic {item['difference_type'].replace('_', ' ')} finding.",
                    risk="Synthetic compatibility risk only.",
                    recommended_action="Inspect the synthetic mapping rule.",
                    private_detail_ref=CompatibilitySourceRef(self.root_id, "legacy.jsonl"),
                )
                for item in record["differences"]
            )
            yield InventoryCandidate(
                record_kind=record["record_kind"],
                legacy_id=record["legacy_id"],
                source_ref=CompatibilitySourceRef(self.root_id, "legacy.jsonl"),
                disposition=record["disposition"],
                projected_kind=record["projected_kind"],
                projection_status=record["projection_status"],
                record_role=record["record_role"],
                unsupported_fields=tuple(record["unsupported_fields"]),
                diagnostic_codes=tuple(record["diagnostic_codes"]),
                differences=differences,
            )


class CleanLegacyAdapter(SyntheticLegacyAdapter):
    def iter_inventory(self, context: CompatibilityContext):
        first = next(iter(super().iter_inventory(context)))
        yield InventoryCandidate(
            record_kind=first.record_kind,
            legacy_id=first.legacy_id,
            source_ref=first.source_ref,
            disposition=first.disposition,
            projected_kind=first.projected_kind,
            projection_status=first.projection_status,
            record_role=first.record_role,
            unsupported_fields=first.unsupported_fields,
            diagnostic_codes=first.diagnostic_codes,
            differences=(),
        )


def _digest(value: str | None) -> str | None:
    return hashlib.sha256(value.encode("utf-8")).hexdigest() if value is not None else None
