from __future__ import annotations

import hashlib
import os
import stat
import unicodedata
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from research_kb.compatibility import CompatibilityContext, CompatibilitySourceRef, LegacyReaderAdapter
from research_kb.compatibility.models import (
    DIFFERENCE_TYPES,
    DISPOSITIONS,
    normalize_inventory_candidate,
    normalize_source_ref,
)
from research_kb.contracts.validator import validate_record
from research_kb.errors import (
    COMPATIBILITY_ADAPTER_ERROR,
    COMPATIBILITY_OUTPUT_INVALID,
    DUPLICATE_ID,
    PROTECTED_INPUT_CHANGED,
    Diagnostic,
    ResearchKBError,
)
from research_kb.storage.json_io import file_sha256, serialize_json
from research_kb.workspace import WorkspaceLayout


@dataclass(frozen=True, slots=True)
class CompatibilityInspectionResult:
    report: dict[str, Any]
    exit_code: int


@dataclass(frozen=True, slots=True)
class _ProtectedInput:
    source_ref: CompatibilitySourceRef
    path: Path


class CompatibilityAdapterRegistry:
    def __init__(self, adapters: Iterable[LegacyReaderAdapter] = ()):
        self._adapters: dict[str, LegacyReaderAdapter] = {}
        for adapter in adapters:
            adapter_id = _validate_adapter_metadata(adapter)
            if adapter_id in self._adapters:
                raise ResearchKBError(
                    Diagnostic(DUPLICATE_ID, "compatibility-adapter", adapter_id, "/adapter_id", "duplicate compatibility adapter ID")
                )
            self._adapters[adapter_id] = adapter

    def require(self, adapter_id: str) -> LegacyReaderAdapter:
        adapter = self._adapters.get(adapter_id)
        if adapter is None:
            raise ResearchKBError(
                Diagnostic(COMPATIBILITY_ADAPTER_ERROR, "compatibility-adapter", None, "/adapter_id", "compatibility adapter is not explicitly registered")
            )
        return adapter


class CompatibilityInspectionService:
    def __init__(self, layout: WorkspaceLayout, registry: CompatibilityAdapterRegistry):
        self.layout = layout
        self.registry = registry

    def inspect(self, adapter_id: str) -> CompatibilityInspectionResult:
        adapter = self.registry.require(adapter_id)
        context = CompatibilityContext(self.layout)
        protected = self._protected_inputs(adapter, context)
        before = self._snapshots(protected, changed=False)
        candidates = None
        adapter_error: Exception | None = None
        try:
            candidates = list(adapter.iter_inventory(context))
        except Exception as error:
            adapter_error = error
        finally:
            after = self._snapshots(protected, changed=True)
        if serialize_json({"snapshots": before}) != serialize_json({"snapshots": after}):
            raise _protected_changed()
        if adapter_error is not None:
            raise ResearchKBError(
                Diagnostic(COMPATIBILITY_ADAPTER_ERROR, "compatibility-adapter", adapter_id, "", "compatibility adapter inspection failed")
            ) from adapter_error
        assert candidates is not None
        report = self._report(adapter, candidates, before, after)
        return CompatibilityInspectionResult(
            report,
            1 if report["blocking_difference_count"] else 0,
        )

    def _protected_inputs(
        self,
        adapter: LegacyReaderAdapter,
        context: CompatibilityContext,
    ) -> tuple[_ProtectedInput, ...]:
        try:
            declared = list(adapter.protected_inputs(context))
        except Exception as error:
            raise ResearchKBError(
                Diagnostic(COMPATIBILITY_ADAPTER_ERROR, "compatibility-adapter", adapter.adapter_id, "", "compatibility adapter could not declare protected inputs")
            ) from error
        if not declared:
            raise _invalid_output("adapter must declare at least one protected input", "/source_snapshot_before")
        result: list[_ProtectedInput] = []
        seen: set[tuple[str, str]] = set()
        for declared_ref in declared:
            try:
                source_ref = normalize_source_ref(declared_ref)
            except ResearchKBError as error:
                raise _invalid_output("adapter declared an invalid protected input reference", "/source_snapshot_before") from error
            except Exception as error:
                raise _invalid_output("adapter declared a malformed protected input", "/source_snapshot_before") from error
            key = (source_ref.root_role, source_ref.relative_path)
            if key in seen:
                raise ResearchKBError(
                    Diagnostic(DUPLICATE_ID, "compatibility-source", None, "/source_snapshot_before", "duplicate protected input reference")
                )
            seen.add(key)
            if _has_unsafe_source_component(self.layout, source_ref):
                raise _invalid_output("protected input reference contains an unsafe link or reparse point", "/source_snapshot_before")
            try:
                _, path = self.layout.resolve_source(source_ref.root_role, source_ref.relative_path)
            except ResearchKBError as error:
                raise _invalid_output("protected input reference is not readable through a declared source root", "/source_snapshot_before") from error
            if not path.exists() or (not path.is_file() and not path.is_dir()):
                raise _invalid_output("protected input is missing or has an unsupported type", "/source_snapshot_before")
            logical_path = self.layout.source_roots[source_ref.root_role].joinpath(
                *PurePosixPath(source_ref.relative_path).parts
            )
            result.append(_ProtectedInput(source_ref, logical_path))
        return tuple(sorted(result, key=lambda item: (item.source_ref.root_role, item.source_ref.relative_path)))

    def _snapshots(self, protected: tuple[_ProtectedInput, ...], *, changed: bool) -> list[dict[str, Any]]:
        snapshots = []
        for item in protected:
            try:
                if _has_unsafe_source_component(self.layout, item.source_ref):
                    if changed:
                        raise _protected_changed()
                    raise _invalid_output(
                        "protected input reference contains an unsafe link or reparse point",
                        "/source_snapshot_before",
                    )
                snapshots.append(_snapshot(item))
            except (OSError, ResearchKBError) as error:
                if changed:
                    raise _protected_changed() from error
                if isinstance(error, ResearchKBError):
                    raise
                raise _invalid_output("protected input could not be fingerprinted", "/source_snapshot_before") from error
        return snapshots

    def _report(
        self,
        adapter: LegacyReaderAdapter,
        candidates,
        before: list[dict[str, Any]],
        after: list[dict[str, Any]],
    ) -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        differences: list[dict[str, Any]] = []
        identities: set[tuple[str, str, str]] = set()
        difference_ids: set[str] = set()
        for candidate in candidates:
            try:
                item, candidate_differences = normalize_inventory_candidate(adapter.source_system, candidate)
            except ResearchKBError as error:
                if error.diagnostic.code == COMPATIBILITY_OUTPUT_INVALID:
                    raise
                raise _invalid_output("adapter yielded an invalid compatibility candidate", "/items") from error
            except Exception as error:
                raise _invalid_output("adapter yielded a malformed compatibility candidate", "/items") from error
            identity = item["legacy_identity"]
            identity_key = (identity["source_system"], identity["record_kind"], identity["legacy_id"])
            if identity_key in identities:
                raise ResearchKBError(
                    Diagnostic(DUPLICATE_ID, "compatibility-item", identity["legacy_id"], "/legacy_identity", "duplicate legacy compatibility identity")
                )
            identities.add(identity_key)
            self._require_output_ref(item["source_ref"], before)
            for difference in candidate_differences:
                if difference["difference_id"] in difference_ids:
                    raise ResearchKBError(
                        Diagnostic(DUPLICATE_ID, "compatibility-difference", difference["difference_id"], "/difference_id", "duplicate deterministic compatibility difference ID")
                    )
                difference_ids.add(difference["difference_id"])
                if difference["private_detail_ref"] is not None:
                    self._require_output_ref(difference["private_detail_ref"], before)
                diagnostics = validate_record("compatibility-difference", difference, actor="stored")
                if diagnostics:
                    raise _invalid_output("normalized compatibility difference failed public schema", diagnostics[0].json_path)
            items.append(item)
            differences.extend(candidate_differences)
        items.sort(key=lambda item: (
            item["legacy_identity"]["source_system"],
            item["legacy_identity"]["record_kind"],
            item["legacy_identity"]["legacy_id"],
        ))
        differences.sort(key=lambda item: item["difference_id"])
        item_kind_counts = Counter(item["legacy_identity"]["record_kind"] for item in items)
        disposition_counts = Counter(item["disposition"] for item in items)
        difference_counts = Counter(item["difference_type"] for item in differences)
        blocking = sum(bool(item["blocking"]) for item in differences)
        report = {
            "schema_version": "1.0",
            "workspace_id": self.layout.workspace_id,
            "adapter_id": adapter.adapter_id,
            "adapter_version": adapter.adapter_version,
            "source_system": adapter.source_system,
            "read_only": True,
            "status": "blocking_differences" if blocking else "success",
            "source_snapshot_before": before,
            "source_snapshot_after": after,
            "items": items,
            "differences": differences,
            "item_counts_by_kind": dict(sorted(item_kind_counts.items())),
            "item_counts_by_disposition": {value: disposition_counts[value] for value in DISPOSITIONS},
            "difference_counts_by_type": {value: difference_counts[value] for value in DIFFERENCE_TYPES},
            "blocking_difference_count": blocking,
            "protected_inputs_unchanged": True,
        }
        diagnostics = validate_record("compatibility-report", report, actor="stored")
        if diagnostics:
            raise _invalid_output("normalized compatibility report failed public schema", diagnostics[0].json_path)
        return report

    def _require_output_ref(self, value: dict[str, str], protected: list[dict[str, Any]]) -> None:
        source_ref = CompatibilitySourceRef(value["root_role"], value["relative_path"])
        if not _source_ref_is_protected(source_ref, protected):
            raise _invalid_output("compatibility output reference is not covered by a protected input", "/source_ref")
        if _has_unsafe_source_component(self.layout, source_ref):
            raise _invalid_output("compatibility output reference contains an unsafe link or reparse point", "/source_ref")
        try:
            _, path = self.layout.resolve_source(value["root_role"], value["relative_path"])
        except ResearchKBError as error:
            raise _invalid_output("compatibility output reference is outside declared source roots", "/source_ref") from error
        if not path.exists():
            raise _invalid_output("compatibility output reference does not exist", "/source_ref")


def _validate_adapter_metadata(adapter: LegacyReaderAdapter) -> str:
    try:
        adapter_id = adapter.adapter_id
        adapter_version = adapter.adapter_version
        source_system = adapter.source_system
        supported_contract_versions = tuple(adapter.supported_contract_versions)
    except Exception as error:
        raise ResearchKBError(
            Diagnostic(COMPATIBILITY_ADAPTER_ERROR, "compatibility-adapter", None, "", "adapter metadata is incomplete or unreadable")
        ) from error
    for path, value in (("/adapter_id", adapter_id), ("/source_system", source_system)):
        if not isinstance(value, str) or not value or not value[0].islower() or any(
            character not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for character in value
        ) or len(value) > 64:
            raise ResearchKBError(
                Diagnostic(COMPATIBILITY_ADAPTER_ERROR, "compatibility-adapter", None, path, "adapter metadata must use a lower-case slug")
            )
    if (
        not isinstance(adapter_version, str)
        or not adapter_version
        or len(adapter_version) > 64
        or "/" in adapter_version
        or "\\" in adapter_version
    ):
        raise ResearchKBError(
            Diagnostic(COMPATIBILITY_ADAPTER_ERROR, "compatibility-adapter", adapter_id, "/adapter_version", "adapter version must be a bounded non-empty string")
        )
    if "1.0" not in supported_contract_versions:
        raise ResearchKBError(
            Diagnostic(COMPATIBILITY_ADAPTER_ERROR, "compatibility-adapter", adapter_id, "/supported_contract_versions", "adapter does not support compatibility contract 1.0")
        )
    return adapter_id


def _snapshot(item: _ProtectedInput) -> dict[str, Any]:
    if not item.path.exists():
        raise _protected_changed()
    if item.path.is_file():
        digest = file_sha256(item.path)
        if digest is None:
            raise _invalid_output("protected file could not be fingerprinted", "/source_snapshot_before")
        kind = "file"
    elif item.path.is_dir():
        digest = _tree_sha256(item.path)
        kind = "tree"
    else:
        raise _invalid_output("protected input has an unsupported type", "/source_snapshot_before")
    return {
        "source_ref": item.source_ref.to_dict(),
        "source_kind": kind,
        "algorithm": "sha256",
        "value": digest,
    }


def _tree_sha256(root: Path) -> str:
    entries: list[tuple[str, str, str | None]] = []
    pending = [root]
    while pending:
        current = pending.pop()
        with os.scandir(current) as scanned:
            children = sorted(scanned, key=lambda item: unicodedata.normalize("NFC", item.name).casefold())
        for child in children:
            path = Path(child.path)
            relative = unicodedata.normalize("NFC", path.relative_to(root).as_posix())
            if _is_unsafe_link(path):
                raise _invalid_output("protected tree contains an unsafe link or reparse point", "/source_snapshot_before")
            if child.is_dir(follow_symlinks=False):
                entries.append(("D", relative, None))
                pending.append(path)
            elif child.is_file(follow_symlinks=False):
                digest = file_sha256(path)
                if digest is None:
                    raise _invalid_output("protected tree file could not be fingerprinted", "/source_snapshot_before")
                entries.append(("F", relative, digest))
            else:
                raise _invalid_output("protected tree contains an unsupported path type", "/source_snapshot_before")
    hasher = hashlib.sha256()
    for kind, relative, digest in sorted(entries, key=lambda value: (value[1], value[0])):
        if kind == "D":
            hasher.update(f"D\0{relative}\n".encode("utf-8"))
        else:
            hasher.update(f"F\0{relative}\0{digest}\n".encode("utf-8"))
    return hasher.hexdigest()


def _is_unsafe_link(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    if callable(is_junction) and is_junction():
        return True
    try:
        attributes = getattr(os.lstat(path), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _has_unsafe_source_component(layout: WorkspaceLayout, source_ref: CompatibilitySourceRef) -> bool:
    root = layout.source_roots.get(source_ref.root_role)
    if root is None:
        return False
    current = root
    for part in PurePosixPath(source_ref.relative_path).parts:
        current = current / part
        if _is_unsafe_link(current):
            return True
    return False


def _source_ref_is_protected(source_ref: CompatibilitySourceRef, protected: list[dict[str, Any]]) -> bool:
    for snapshot in protected:
        protected_ref = snapshot["source_ref"]
        if source_ref.root_role != protected_ref["root_role"]:
            continue
        protected_path = protected_ref["relative_path"]
        if source_ref.relative_path == protected_path:
            return True
        if snapshot["source_kind"] == "tree" and source_ref.relative_path.startswith(f"{protected_path}/"):
            return True
    return False


def _invalid_output(message: str, path: str) -> ResearchKBError:
    return ResearchKBError(Diagnostic(COMPATIBILITY_OUTPUT_INVALID, "compatibility", None, path, message))


def _protected_changed() -> ResearchKBError:
    return ResearchKBError(
        Diagnostic(PROTECTED_INPUT_CHANGED, "compatibility", None, "/source_snapshot_after", "protected compatibility input changed during inspection")
    )
