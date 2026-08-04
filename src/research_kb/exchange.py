from __future__ import annotations

import os
import re
import stat
import zipfile
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any

from research_kb.bundle import BundleEntry, load_workspace_entries, validate_workspace_entries
from research_kb.contracts.validator import validate_record
from research_kb.errors import (
    INVALID_AUTHORITY,
    PATH_ESCAPE,
    PROTECTED_INPUT_CHANGED,
    PRIVACY_LEAK,
    SCHEMA_VALIDATION_FAILED,
    UNRESOLVED_REFERENCE,
    WRITE_CONFLICT,
    Diagnostic,
    ResearchKBError,
)
from research_kb.identifiers import Namespace, allocate_id, validate_id
from research_kb.process_events import Clock, timestamp, utc_now
from research_kb.source_assets import current_source_asset_heads
from research_kb.storage.json_io import (
    atomic_write_bytes,
    read_json_document,
    serialize_json,
    serialize_jsonl,
    sha256_bytes,
)
from research_kb.storage.locking import workspace_lock
from research_kb.workspace import WorkspaceLayout


BUNDLE_FORMAT = "research-kb-exchange-bundle@1.0"
EXCHANGE_INTERFACE_VERSION = "1.0"
EXPORT_SELECTORS = ("paper", "question", "direction", "workspace")
RIGHTS_ASSERTION = "user_asserts_redistribution_authorized"

_ID_FIELDS = {
    "registry-paper": "paper_id",
    "paper-card": "paper_id",
    "evidence": "evidence_id",
    "review-queue": "queue_id",
    "review-memory": "review_memory_id",
    "primary-semantic-bundle": "paper_id",
    "review-semantic-bundle": "paper_id",
    "question-mapping": "question_id",
    "direction-bundle": "direction_id",
    "field-map-bundle": "field_map_entry_id",
    "question-revision-bundle": "question_id",
    "tag-bundle": "tag_id",
    "tag-link-bundle": "tag_link_id",
    "screening-criteria-bundle": "criteria_id",
    "screening-decision-bundle": "decision_id",
    "step7-synthesis": "candidate_id",
    "step7-review-angle": "candidate_id",
    "step7-insight": "candidate_id",
    "step7-cross-view": "candidate_id",
}

_PAPER_SCIENTIFIC_KINDS = frozenset(
    {
        "registry-paper",
        "paper-card",
        "evidence",
        "review-queue",
        "review-memory",
        "primary-semantic-bundle",
        "review-semantic-bundle",
    }
)
_QUESTION_KINDS = frozenset(
    {
        "question-mapping",
        "question-revision-bundle",
        "screening-criteria-bundle",
        "screening-decision-bundle",
        "step7-synthesis",
        "step7-review-angle",
        "step7-insight",
        "step7-cross-view",
    }
)
_DIRECTION_KINDS = frozenset({"direction-bundle", "field-map-bundle"})
_WINDOWS_ABSOLUTE_PATH = re.compile(r"(?i)(?:^|[\s'\"])(?:[a-z]:[\\/]|\\\\)")
_COMMON_POSIX_ABSOLUTE_PATH = re.compile(r"(?:^|[\s'\"])/(?:Users|home|tmp|var|mnt|Volumes)/")


class ExchangeExportService:
    def __init__(
        self,
        layout: WorkspaceLayout,
        *,
        clock: Clock = utc_now,
        export_id_factory: Callable[[], str] | None = None,
        lock_timeout: float = 30.0,
        phase_hook: Callable[[str], None] | None = None,
    ):
        self.layout = layout
        self.clock = clock
        self.export_id_factory = export_id_factory or (lambda: allocate_id(Namespace.EXPORT))
        self.lock_timeout = lock_timeout
        self.phase_hook = phase_hook

    def preview(self, request: Mapping[str, Any]) -> dict[str, Any]:
        selection = _validate_selection_request(request, build=False)
        include_sources, rights_assertion = _validate_source_options(request)
        plan = self._plan(selection, include_sources=include_sources, rights_assertion=rights_assertion)
        public_plan = {key: value for key, value in plan.items() if not key.startswith("_")}
        return {
            "status": "success",
            "interface_version": EXCHANGE_INTERFACE_VERSION,
            "bundle_format": BUNDLE_FORMAT,
            "export_id": self.export_id_factory(),
            "created_at": timestamp(self.clock),
            **public_plan,
            "rights_status": "asserted_by_user" if include_sources else "not_required",
            "source_inclusion_available": True,
            "persistent_writes": 0,
            "canonical_scientific_write": False,
        }

    def build(
        self,
        request: Mapping[str, Any],
        *,
        target: Path,
        actor: str,
    ) -> dict[str, Any]:
        if actor not in {"cli", "user"}:
            raise _error(INVALID_AUTHORITY, "/actor", "Exchange archive creation requires cli or user authority")
        selection = _validate_selection_request(request, build=True)
        include_sources, rights_assertion = _validate_source_options(request)
        expected_basis = request["expected_basis_digest"]
        export_id = validate_id(request["export_id"], Namespace.EXPORT)
        created_at = _require_timestamp(request["created_at"], "/created_at")
        resolved_target = _validate_create_only_target(Path(target))
        with workspace_lock(self.layout.lock_path, timeout=self.lock_timeout):
            return self._build_locked(
                selection=selection,
                include_sources=include_sources,
                rights_assertion=rights_assertion,
                expected_basis=expected_basis,
                export_id=export_id,
                created_at=created_at,
                resolved_target=resolved_target,
                actor=actor,
            )

    def _build_locked(
        self,
        *,
        selection: dict[str, str],
        include_sources: bool,
        rights_assertion: str | None,
        expected_basis: str,
        export_id: str,
        created_at: str,
        resolved_target: Path,
        actor: str,
    ) -> dict[str, Any]:
        plan = self._plan(selection, include_sources=include_sources, rights_assertion=rights_assertion)
        if include_sources and plan["missing_source_count"]:
            raise _error(
                PROTECTED_INPUT_CHANGED,
                "/include_sources",
                "one or more selected source PDFs are unavailable, changed or unsupported",
            )
        if plan["basis_digest"] != expected_basis:
            raise _error(
                PROTECTED_INPUT_CHANGED,
                "/expected_basis_digest",
                "Exchange selection changed after preview",
            )
        receipt_path = self.layout.ensure_writable_target(
            self.layout.exchange_export_receipt_path(export_id)
        )
        archive_entries, manifest_sha256 = _archive_entries(
            workspace_id=self.layout.workspace_id,
            export_id=export_id,
            created_at=created_at,
            selection=selection,
            data_files=plan["_data_files"],
            inventory=plan["_inventory"],
            record_count=plan["record_count"],
            record_kind_counts=plan["record_kind_counts"],
            source_count=plan["source_count"],
            include_sources=include_sources,
            rights_assertion=rights_assertion,
            basis_digest=plan["basis_digest"],
        )
        stage = resolved_target.parent / f".{resolved_target.name}.{export_id}.tmp"
        if os.path.lexists(stage):
            raise _error(WRITE_CONFLICT, "/target", "operation-owned Exchange stage already exists")
        archive_sha256: str | None = None
        published = False
        receipt_created = False
        try:
            _write_archive(stage, archive_entries)
            archive_sha256 = _file_sha256(stage)
            _validate_written_archive(stage, archive_entries)
            if os.path.lexists(resolved_target):
                raise _error(WRITE_CONFLICT, "/target", "Exchange target already exists")
            os.replace(stage, resolved_target)
            published = True
            if self.phase_hook is not None:
                self.phase_hook("published")
            local_receipt = _build_local_export_receipt(
                export_id=export_id,
                workspace_id=self.layout.workspace_id,
                selection=selection,
                include_sources=include_sources,
                rights_assertion=rights_assertion,
                record_count=plan["record_count"],
                source_count=plan["source_count"],
                manifest_sha256=manifest_sha256,
                basis_digest=plan["basis_digest"],
                archive_sha256=archive_sha256,
                archive_bytes=resolved_target.stat().st_size,
                actor=actor,
                created_at=created_at,
            )
            if receipt_path.exists():
                existing_receipt = read_json_document(
                    receipt_path,
                    record_kind="exchange-local-export-receipt",
                )
                if existing_receipt != local_receipt:
                    raise _error(WRITE_CONFLICT, "/export_id", "Exchange export ID is already bound to another archive")
            else:
                atomic_write_bytes(receipt_path, serialize_json(local_receipt), export_id)
                receipt_created = True
            if self.phase_hook is not None:
                self.phase_hook("receipt_recorded")
        except BaseException:
            if (
                published
                and archive_sha256 is not None
                and resolved_target.is_file()
                and _file_sha256(resolved_target) == archive_sha256
            ):
                resolved_target.unlink(missing_ok=True)
            if receipt_created and receipt_path.is_file():
                receipt_path.unlink(missing_ok=True)
            raise
        finally:
            stage.unlink(missing_ok=True)
        return {
            "status": "success",
            "interface_version": EXCHANGE_INTERFACE_VERSION,
            "bundle_format": BUNDLE_FORMAT,
            "result": "created",
            "export_id": export_id,
            "selection": selection,
            "record_count": plan["record_count"],
            "record_kind_counts": plan["record_kind_counts"],
            "source_count": plan["source_count"],
            "include_sources": include_sources,
            "rights_assertion": rights_assertion,
            "manifest_sha256": manifest_sha256,
            "archive_sha256": archive_sha256,
            "archive_bytes": resolved_target.stat().st_size,
            "export_receipt_ref": export_id,
            "persistent_writes": 1,
            "canonical_scientific_write": False,
        }

    def _plan(
        self,
        selection: dict[str, str],
        *,
        include_sources: bool,
        rights_assertion: str | None,
    ) -> dict[str, Any]:
        entries = load_workspace_entries(self.layout)
        validate_workspace_entries(entries)
        selected = _select_entries(entries, selection)
        envelopes = [build_exchange_envelope(self.layout.workspace_id, kind, record) for kind, record in selected]
        grouped: dict[str, list[dict[str, Any]]] = {}
        for envelope in envelopes:
            grouped.setdefault(envelope["record_kind"], []).append(envelope)
        record_files: dict[str, bytes] = {}
        counts: Counter[str] = Counter()
        for kind, records in sorted(grouped.items()):
            ordered = sorted(
                records,
                key=lambda item: (item["origin_record_id"], item["revision_digest"]),
            )
            record_files[f"records/{kind}.jsonl"] = serialize_jsonl(ordered)
            counts[kind] = len(ordered)
        data_files = dict(record_files)
        inventory = [
            {
                "path": path,
                "sha256": sha256_bytes(content),
                "bytes": len(content),
                "record_count": counts[path.removeprefix("records/").removesuffix(".jsonl")],
                "entry_kind": "records",
            }
            for path, content in sorted(record_files.items())
        ]
        source_count = 0
        missing_sources: list[dict[str, str]] = []
        if include_sources:
            source_files, source_descriptors, missing_sources = self._collect_sources(selected, entries)
            source_count = len(source_descriptors)
            if source_descriptors:
                source_index = serialize_jsonl(source_descriptors)
                data_files["sources/index.jsonl"] = source_index
                inventory.append(
                    {
                        "path": "sources/index.jsonl",
                        "sha256": sha256_bytes(source_index),
                        "bytes": len(source_index),
                        "record_count": source_count,
                        "entry_kind": "source_index",
                    }
                )
            for path, content in sorted(source_files.items()):
                data_files[path] = content
                inventory.append(
                    {
                        "path": path,
                        "sha256": sha256_bytes(content),
                        "bytes": len(content),
                        "record_count": 0,
                        "entry_kind": "source",
                    }
                )
        inventory.sort(key=lambda item: item["path"])
        basis = {
            "bundle_format": BUNDLE_FORMAT,
            "origin_workspace_id": self.layout.workspace_id,
            "selection": selection,
            "include_sources": include_sources,
            "rights_assertion": rights_assertion,
            "entries": inventory,
            "missing_sources": missing_sources,
        }
        return {
            "selection": selection,
            "record_count": sum(counts.values()),
            "record_kind_counts": dict(sorted(counts.items())),
            "structured_bytes": sum(len(content) for path, content in data_files.items() if not path.endswith(".pdf")),
            "estimated_archive_bytes": sum(len(content) for content in data_files.values()),
            "source_count": source_count,
            "pdf_count": len([path for path in data_files if path.startswith("sources/sha256/")]),
            "missing_source_count": len(missing_sources),
            "missing_sources": missing_sources[:100],
            "basis_digest": sha256_bytes(serialize_json(basis)),
            "_data_files": data_files,
            "_inventory": inventory,
        }

    def _collect_sources(
        self,
        selected: list[BundleEntry],
        entries: list[BundleEntry],
    ) -> tuple[dict[str, bytes], list[dict[str, Any]], list[dict[str, str]]]:
        files: dict[str, bytes] = {}
        descriptors: list[dict[str, Any]] = []
        missing: list[dict[str, str]] = []
        papers = [record for kind, record in selected if kind == "registry-paper"]
        selected_paper_ids = {paper["paper_id"] for paper in papers}
        source_states = [record for kind, record in entries if kind == "source-asset-state"]
        source_heads = current_source_asset_heads(source_states) if source_states else ()
        heads_by_paper: dict[str, list[dict[str, Any]]] = {}
        for head in source_heads:
            if head.get("paper_id") in selected_paper_ids:
                heads_by_paper.setdefault(head["paper_id"], []).append(head)
        for paper in sorted(papers, key=lambda item: item["paper_id"]):
            assets = sorted(
                heads_by_paper.get(paper["paper_id"], []),
                key=lambda item: (item["asset_role"], item["source_asset_id"]),
            )
            if not any(item["asset_role"] == "main_pdf" for item in assets):
                assets.insert(
                    0,
                    {
                        "paper_id": paper["paper_id"],
                        "source_asset_id": None,
                        "asset_role": "main_pdf",
                        "source_ref": paper["source_ref"],
                        "source_fingerprint": paper["source_fingerprint"],
                        "manifestation_id": f"sha256:{paper['source_fingerprint']['value']}",
                        "manifestation_status": "active",
                        "availability": "available",
                    },
                )
            for asset in assets:
                self._collect_source_asset(asset, files, descriptors, missing)
        return files, descriptors, missing

    def _collect_source_asset(
        self,
        asset: Mapping[str, Any],
        files: dict[str, bytes],
        descriptors: list[dict[str, Any]],
        missing: list[dict[str, str]],
    ) -> None:
        source_ref = asset["source_ref"]
        if asset.get("availability") != "available" or asset.get("manifestation_status") != "active":
            missing.append(
                {
                    "paper_id": asset["paper_id"],
                    "source_asset_id": asset.get("source_asset_id") or "registry-main-pdf",
                    "asset_role": asset["asset_role"],
                    "reason": "source_asset_not_current_and_available",
                }
            )
            return
        try:
            _, source_path = self.layout.resolve_source(source_ref["root_id"], source_ref["relative_path"])
            if (
                source_path.suffix.lower() != ".pdf"
                or not source_path.is_file()
                or _has_unsafe_component(source_path)
            ):
                raise OSError("source is not an available regular PDF")
            content = source_path.read_bytes()
            digest = sha256_bytes(content)
            if digest != asset["source_fingerprint"]["value"] or not content.startswith(bytes((37, 80, 68, 70, 45))):
                raise OSError("source fingerprint or PDF signature changed")
        except (OSError, ResearchKBError):
            missing.append(
                {
                    "paper_id": asset["paper_id"],
                    "source_asset_id": asset.get("source_asset_id") or "registry-main-pdf",
                    "asset_role": asset["asset_role"],
                    "reason": "unavailable_changed_or_not_pdf",
                }
            )
            return
        archive_path = f"sources/sha256/{digest}.pdf"
        files.setdefault(archive_path, content)
        descriptor = {
            "schema_version": "1.0",
            "paper_id": asset["paper_id"],
            "source_asset_id": asset.get("source_asset_id"),
            "asset_role": asset["asset_role"],
            "source_fingerprint": asset["source_fingerprint"],
            "manifestation_id": asset["manifestation_id"],
            "content_type": "application/pdf",
            "archive_path": archive_path,
            "bytes": len(content),
        }
        diagnostics = validate_record("exchange-source-envelope", descriptor, actor="stored")
        if diagnostics:
            raise ResearchKBError(diagnostics[0])
        descriptors.append(descriptor)


def _select_entries(entries: list[BundleEntry], selection: Mapping[str, str]) -> list[BundleEntry]:
    exportable = [(kind, record) for kind, record in entries if kind in _ID_FIELDS]
    scope = selection["scope"]
    selector_id = selection.get("selector_id")
    if scope == "workspace":
        selected = exportable
    elif scope == "paper":
        selected = [
            (kind, record)
            for kind, record in exportable
            if kind in _PAPER_SCIENTIFIC_KINDS and selector_id in _paper_ids(kind, record)
        ]
        selected = _add_direct_tags(exportable, selected, "paper", selector_id)
    elif scope == "question":
        question_records = [
            (kind, record)
            for kind, record in exportable
            if kind in _QUESTION_KINDS and selector_id in _question_ids(kind, record)
        ]
        paper_ids = {
            paper_id
            for kind, record in question_records
            for paper_id in _paper_ids(kind, record)
        }
        selected = [
            *question_records,
            *[
                (kind, record)
                for kind, record in exportable
                if kind in _PAPER_SCIENTIFIC_KINDS and _paper_ids(kind, record) & paper_ids
            ],
        ]
        selected = _add_direct_tags(exportable, selected, "question", selector_id)
    else:
        direction_records = [
            (kind, record)
            for kind, record in exportable
            if kind in _DIRECTION_KINDS and selector_id in _direction_ids(kind, record)
        ]
        paper_ids = {
            paper_id
            for kind, record in direction_records
            for paper_id in _paper_ids(kind, record)
        }
        selected = [
            *direction_records,
            *[
                (kind, record)
                for kind, record in exportable
                if kind in _PAPER_SCIENTIFIC_KINDS and _paper_ids(kind, record) & paper_ids
            ],
        ]
        selected = _add_direct_tags(exportable, selected, "direction", selector_id)
    selected = _deduplicate_entries(selected)
    if not selected:
        raise _error(UNRESOLVED_REFERENCE, "/selector_id", "Exchange selector does not resolve to an exportable record")
    return selected


def _add_direct_tags(
    exportable: list[BundleEntry],
    selected: list[BundleEntry],
    target_kind: str,
    target_id: str | None,
) -> list[BundleEntry]:
    links = [
        (kind, record)
        for kind, record in exportable
        if kind == "tag-link-bundle"
        and record.get("target_kind") == target_kind
        and record.get("target_id") == target_id
    ]
    tag_ids = {record["tag_id"] for _, record in links}
    tags = [
        (kind, record)
        for kind, record in exportable
        if kind == "tag-bundle" and record.get("tag_id") in tag_ids
    ]
    return [*selected, *links, *tags]


def _deduplicate_entries(entries: Iterable[BundleEntry]) -> list[BundleEntry]:
    unique: dict[tuple[str, str], BundleEntry] = {}
    for kind, record in entries:
        identifier = _record_id(kind, record)
        unique[(kind, identifier)] = (kind, record)
    return [unique[key] for key in sorted(unique)]


def build_exchange_envelope(workspace_id: str, kind: str, record: Mapping[str, Any]) -> dict[str, Any]:
    projected_kind = kind
    projected = deepcopy(dict(record))
    if kind == "registry-paper":
        projected_kind = "exchange-paper-identity"
        projected.pop("source_ref", None)
        diagnostics = validate_record(projected_kind, projected, actor="stored")
        if diagnostics:
            raise ResearchKBError(diagnostics[0])
    _assert_path_free(projected)
    record_digest = sha256_bytes(serialize_json(projected))
    envelope = {
        "schema_version": "1.0",
        "origin_workspace_id": workspace_id,
        "origin_record_id": _record_id(kind, record),
        "record_kind": projected_kind,
        "revision_digest": record_digest,
        "claimed_review_status": _claimed_review_status(record),
        "local_admissibility": "external_unreviewed",
        "record": projected,
    }
    diagnostics = validate_record("exchange-record-envelope", envelope, actor="stored")
    if diagnostics:
        raise ResearchKBError(diagnostics[0])
    return envelope


def _record_id(kind: str, record: Mapping[str, Any]) -> str:
    field = _ID_FIELDS[kind]
    value = record.get(field)
    if not isinstance(value, str) or not value:
        raise _error(SCHEMA_VALIDATION_FAILED, f"/{field}", "exportable record lacks its stable identity")
    return value


def _claimed_review_status(record: Mapping[str, Any]) -> str | None:
    value = record.get("review_status")
    return value if isinstance(value, str) else None


def _assert_path_free(value: Any, path: str = "") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            _assert_path_free(item, f"{path}/{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _assert_path_free(item, f"{path}/{index}")
    elif isinstance(value, str) and (
        _WINDOWS_ABSOLUTE_PATH.search(value)
        or _COMMON_POSIX_ABSOLUTE_PATH.search(value)
        or (value.startswith("/") and not any(character.isspace() for character in value))
    ):
        raise _error(PRIVACY_LEAK, path, "Exchange record contains an absolute local path")


def _paper_ids(kind: str, record: Mapping[str, Any]) -> set[str]:
    direct = record.get("paper_id")
    result = {direct} if isinstance(direct, str) else set()
    if kind == "question-mapping":
        result.update(item["paper_id"] for item in record.get("paper_links", []) if isinstance(item.get("paper_id"), str))
    elif kind == "question-revision-bundle":
        for revision in record.get("revisions", []):
            mapping = revision.get("question_mapping", {})
            result.update(item["paper_id"] for item in mapping.get("paper_links", []) if isinstance(item.get("paper_id"), str))
    elif kind in {"direction-bundle", "field-map-bundle"}:
        child = "direction" if kind == "direction-bundle" else "field_map_entry"
        for revision in record.get("revisions", []):
            result.update(item["paper_id"] for item in revision.get(child, {}).get("links", []) if isinstance(item.get("paper_id"), str))
    elif kind.startswith("step7-"):
        result.update(item["paper_id"] for item in record.get("paper_card_base", []) if isinstance(item.get("paper_id"), str))
    return result


def _question_ids(kind: str, record: Mapping[str, Any]) -> set[str]:
    if kind in _QUESTION_KINDS:
        value = record.get("question_id")
        return {value} if isinstance(value, str) else set()
    return set()


def _direction_ids(kind: str, record: Mapping[str, Any]) -> set[str]:
    if kind == "direction-bundle":
        value = record.get("direction_id")
        return {value} if isinstance(value, str) else set()
    if kind == "field-map-bundle":
        result = set()
        for revision in record.get("revisions", []):
            for item in revision.get("field_map_entry", {}).get("direction_refs", []):
                value = item.get("direction_id")
                if isinstance(value, str):
                    result.add(value)
        return result
    return set()


def _validate_selection_request(request: Mapping[str, Any], *, build: bool) -> dict[str, str]:
    if not isinstance(request, Mapping):
        raise _error(SCHEMA_VALIDATION_FAILED, "", "Exchange request must be an object")
    allowed = {"scope", "selector_id", "include_sources", "rights_assertion"}
    required = {"scope", "include_sources"}
    if build:
        allowed.update({"expected_basis_digest", "export_id", "created_at"})
        required.update({"expected_basis_digest", "export_id", "created_at"})
    if set(request) - allowed or not required.issubset(request):
        raise _error(SCHEMA_VALIDATION_FAILED, "", "Exchange request has missing or unknown fields")
    scope = request.get("scope")
    if scope not in EXPORT_SELECTORS:
        raise _error(SCHEMA_VALIDATION_FAILED, "/scope", "unsupported Exchange selector")
    selector_id = request.get("selector_id")
    if scope == "workspace":
        if selector_id is not None:
            raise _error(SCHEMA_VALIDATION_FAILED, "/selector_id", "workspace selector cannot include selector_id")
        selection = {"scope": "workspace"}
    else:
        namespaces = {"paper": Namespace.PAPER, "question": Namespace.QUESTION, "direction": Namespace.DIRECTION}
        selection = {"scope": scope, "selector_id": validate_id(selector_id, namespaces[scope])}
    if build:
        digest = request.get("expected_basis_digest")
        if not isinstance(digest, str) or len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            raise _error(SCHEMA_VALIDATION_FAILED, "/expected_basis_digest", "expected basis digest is invalid")
    return selection


def _validate_source_options(request: Mapping[str, Any]) -> tuple[bool, str | None]:
    include_sources = request.get("include_sources")
    if not isinstance(include_sources, bool):
        raise _error(SCHEMA_VALIDATION_FAILED, "/include_sources", "include_sources must be boolean")
    rights_assertion = request.get("rights_assertion")
    if include_sources:
        if rights_assertion != RIGHTS_ASSERTION:
            raise _error(
                INVALID_AUTHORITY,
                "/rights_assertion",
                "source-inclusive Exchange requires the explicit redistribution rights assertion",
            )
        return True, RIGHTS_ASSERTION
    if rights_assertion is not None:
        raise _error(SCHEMA_VALIDATION_FAILED, "/rights_assertion", "source-free Exchange cannot carry a rights assertion")
    return False, None


def _archive_entries(
    *,
    workspace_id: str,
    export_id: str,
    created_at: str,
    selection: Mapping[str, str],
    data_files: Mapping[str, bytes],
    inventory: list[dict[str, Any]],
    record_count: int,
    record_kind_counts: Mapping[str, int],
    source_count: int,
    include_sources: bool,
    rights_assertion: str | None,
    basis_digest: str,
) -> tuple[dict[str, bytes], str]:
    manifest = {
        "schema_version": "1.0",
        "bundle_format": BUNDLE_FORMAT,
        "export_id": export_id,
        "origin_workspace_id": workspace_id,
        "selection": dict(selection),
        "contract_versions": ["1.0"],
        "record_count": record_count,
        "record_kind_counts": dict(record_kind_counts),
        "source_count": source_count,
        "include_sources": include_sources,
        "rights_assertion": rights_assertion,
        "basis_digest": basis_digest,
        "entries": inventory,
        "created_at": created_at,
    }
    diagnostics = validate_record("exchange-bundle-manifest", manifest, actor="stored")
    if diagnostics:
        raise ResearchKBError(diagnostics[0])
    manifest_bytes = serialize_json(manifest)
    manifest_sha256 = sha256_bytes(manifest_bytes)
    receipt = {
        "schema_version": "1.0",
        "export_id": export_id,
        "origin_workspace_id": workspace_id,
        "selection": dict(selection),
        "manifest_sha256": manifest_sha256,
        "basis_digest": basis_digest,
        "archive_digest_scope": "returned_out_of_band",
        "created_at": created_at,
    }
    diagnostics = validate_record("exchange-export-receipt", receipt, actor="stored")
    if diagnostics:
        raise ResearchKBError(diagnostics[0])
    return {"manifest.json": manifest_bytes, "receipt.json": serialize_json(receipt), **data_files}, manifest_sha256


def _build_local_export_receipt(
    *,
    export_id: str,
    workspace_id: str,
    selection: Mapping[str, str],
    include_sources: bool,
    rights_assertion: str | None,
    record_count: int,
    source_count: int,
    manifest_sha256: str,
    basis_digest: str,
    archive_sha256: str,
    archive_bytes: int,
    actor: str,
    created_at: str,
) -> dict[str, Any]:
    receipt = {
        "schema_version": "1.0",
        "export_id": export_id,
        "origin_workspace_id": workspace_id,
        "selection": dict(selection),
        "include_sources": include_sources,
        "rights_assertion": rights_assertion,
        "record_count": record_count,
        "source_count": source_count,
        "manifest_sha256": manifest_sha256,
        "basis_digest": basis_digest,
        "archive_sha256": archive_sha256,
        "archive_bytes": archive_bytes,
        "actor": actor,
        "created_at": created_at,
    }
    diagnostics = validate_record("exchange-local-export-receipt", receipt, actor="stored")
    if diagnostics:
        raise ResearchKBError(diagnostics[0])
    return receipt


def _write_archive(path: Path, entries: Mapping[str, bytes]) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(descriptor)
    try:
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
            for name, content in sorted(entries.items()):
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_STORED
                info.create_system = 3
                info.external_attr = (stat.S_IFREG | 0o600) << 16
                info.flag_bits = 0x800
                archive.writestr(info, content)
        with path.open("rb+") as handle:
            os.fsync(handle.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def _validate_written_archive(path: Path, expected: Mapping[str, bytes]) -> None:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if names != sorted(expected) or names != sorted(names) or len(names) != len(set(names)):
            raise _error(SCHEMA_VALIDATION_FAILED, "/archive", "Exchange archive entry inventory is not canonical")
        for info in archive.infolist():
            if info.compress_type != zipfile.ZIP_STORED or archive.read(info.filename) != expected[info.filename]:
                raise _error(SCHEMA_VALIDATION_FAILED, "/archive", "Exchange archive content failed post-write validation")


def _file_sha256(path: Path) -> str:
    digest = __import__("hashlib").sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_create_only_target(path: Path) -> Path:
    if not path.is_absolute():
        raise _error(PATH_ESCAPE, "/target", "Exchange target must be absolute")
    if path.suffix.lower() != ".zip" or not path.name.endswith(".rkb-exchange.zip"):
        raise _error(SCHEMA_VALIDATION_FAILED, "/target", "Exchange target must end with .rkb-exchange.zip")
    parent = path.parent
    if not parent.is_dir() or _has_unsafe_component(parent):
        raise _error(PATH_ESCAPE, "/target", "Exchange target parent is unavailable or link-backed")
    if os.path.lexists(path):
        raise _error(WRITE_CONFLICT, "/target", "Exchange target already exists")
    return path.resolve(strict=False)


def _has_unsafe_component(path: Path) -> bool:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        if os.path.lexists(current) and _is_unsafe_link(current):
            return True
    return False


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


def _require_timestamp(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.endswith("Z") or "T" not in value:
        raise _error(SCHEMA_VALIDATION_FAILED, path, "timestamp must be UTC RFC3339")
    return value


def _error(code: str, path: str, message: str) -> ResearchKBError:
    return ResearchKBError(Diagnostic(code, "exchange", None, path, message))


__all__ = [
    "BUNDLE_FORMAT",
    "EXCHANGE_INTERFACE_VERSION",
    "EXPORT_SELECTORS",
    "RIGHTS_ASSERTION",
    "ExchangeExportService",
    "build_exchange_envelope",
]
