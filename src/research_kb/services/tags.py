from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any

from research_kb.bundle import BundleEntry, load_workspace_entries, records_of_kind
from research_kb.catalog.models import canonical_digest
from research_kb.contracts.validator import validate_record
from research_kb.errors import (
    DUPLICATE_ID,
    INVALID_AUTHORITY,
    SCHEMA_VALIDATION_FAILED,
    UNRESOLVED_REFERENCE,
    WRITE_CONFLICT,
    Diagnostic,
    ResearchKBError,
)
from research_kb.identifiers import Namespace, allocate_id, validate_id
from research_kb.identity_corrections import project_registry_identity
from research_kb.process_events import Clock, timestamp, utc_now
from research_kb.storage.json_io import file_sha256, read_json_document, serialize_json
from research_kb.storage.transactions import TransactionManager, TransactionResult
from research_kb.tag_bundles import (
    active_tag,
    active_tag_link_state,
    clean_tag_text,
    normalize_tag_name,
    tag_bundle_diagnostics,
    tag_link_bundle_diagnostics,
)
from research_kb.workspace import WorkspaceLayout


IdAllocator = Callable[[Namespace], str]
EntriesLoader = Callable[[WorkspaceLayout], list[BundleEntry]]
TARGET_NAMESPACES = {
    "paper": Namespace.PAPER,
    "direction": Namespace.DIRECTION,
    "field_map_entry": Namespace.FIELD_MAP,
    "question": Namespace.QUESTION,
}


class TagService:
    def __init__(
        self,
        layout: WorkspaceLayout,
        *,
        transaction_manager: TransactionManager | None = None,
        id_allocator: IdAllocator = allocate_id,
        entries_loader: EntriesLoader = load_workspace_entries,
        clock: Clock = utc_now,
    ):
        self.layout = layout
        self.transactions = transaction_manager or TransactionManager(layout, clock=clock)
        self.id_allocator = id_allocator
        self.entries_loader = entries_loader
        self.clock = clock

    def promote_tag(
        self,
        payload: Mapping[str, Any],
        *,
        tag_id: str | None = None,
        approval: Mapping[str, Any],
        actor: str,
        expected_revision_id: str | None = None,
        fixture_origin: str | None = None,
    ) -> tuple[dict[str, Any], TransactionResult | None]:
        _require_approval(actor, approval)
        caller_supplied_id = tag_id is not None
        tag_id = self.id_allocator(Namespace.TAG) if tag_id is None else validate_id(tag_id, Namespace.TAG)
        target = self.layout.tag_bundle_path(tag_id)
        existing = self._read_tag_bundle(target)
        if caller_supplied_id and existing is None:
            raise _error(UNRESOLVED_REFERENCE, "tag-bundle", tag_id, "/tag_id", "caller-supplied Tag ID does not exist")
        current = None if existing is None else active_tag(existing)
        if existing is not None and expected_revision_id is None:
            raise _error(WRITE_CONFLICT, "tag-bundle", tag_id, "/expected_revision_id", "current Tag head is required for revision")
        if expected_revision_id is not None and (existing or {}).get("active_revision_id") != expected_revision_id:
            raise _error(WRITE_CONFLICT, "tag-bundle", tag_id, "/expected_revision_id", "Tag head changed before promotion")

        name = clean_tag_text(payload.get("name", (current or {}).get("name")))
        description = clean_tag_text(payload.get("description", (current or {}).get("description", "")))
        status = payload.get("status", (current or {}).get("status", "active"))
        aliases_value = payload.get("aliases", (current or {}).get("aliases", []))
        if not name or len(name) > 80 or len(description) > 500 or status not in {"active", "archived"}:
            raise _error(SCHEMA_VALIDATION_FAILED, "tag-bundle", tag_id, "/tag", "Tag definition is outside the closed field budgets")
        if not isinstance(aliases_value, list) or len(aliases_value) > 25:
            raise _error(SCHEMA_VALIDATION_FAILED, "tag-bundle", tag_id, "/aliases", "aliases must be an array with at most 25 items")
        aliases = [clean_tag_text(item) for item in aliases_value]
        if any(not item or len(item) > 80 for item in aliases):
            raise _error(SCHEMA_VALIDATION_FAILED, "tag-bundle", tag_id, "/aliases", "aliases must be non-empty and at most 80 characters")
        if current is not None and normalize_tag_name(current["name"]) != normalize_tag_name(name):
            aliases.append(current["name"])
        aliases = _unique_aliases(aliases, name)
        tag = {
            "schema_version": "1.0",
            "tag_id": tag_id,
            "name": name,
            "normalized_name": normalize_tag_name(name),
            "description": description,
            "aliases": aliases,
            "status": status,
        }
        self._require_unique_vocabulary(tag, tag_id)
        content_digest = canonical_digest(tag)
        if existing is not None and existing["revisions"][-1]["content_digest"] == content_digest:
            return existing, None
        now = timestamp(self.clock)
        previous = None if existing is None else existing["revisions"][-1]
        revision_id = self.id_allocator(Namespace.TAG_REVISION)
        revision = {
            "revision_id": revision_id,
            "revision_number": 1 if previous is None else len(existing["revisions"]) + 1,
            "predecessor": None if previous is None else {
                "revision_id": previous["revision_id"],
                "revision_digest": canonical_digest(previous),
            },
            "content_digest": content_digest,
            "approval": dict(approval),
            "tag": tag,
            "created_at": now,
        }
        bundle = {
            "schema_version": "1.0",
            "tag_id": tag_id,
            "active_revision_id": revision_id,
            "revisions": [revision] if existing is None else [*deepcopy(existing["revisions"]), revision],
            "created_at": now if existing is None else existing["created_at"],
            "updated_at": now,
        }
        if fixture_origin is not None:
            bundle["fixture_origin"] = fixture_origin
        tx = self._write_bundle(
            target,
            bundle,
            "tag-bundle",
            "organization_tags",
            "tag_id",
            tag_bundle_diagnostics,
            locked_precondition=lambda: self._require_unique_vocabulary(tag, tag_id),
        )
        return bundle, tx

    def set_assignment(
        self,
        *,
        tag_id: str,
        target_kind: str,
        target_id: str,
        state: str,
        approval: Mapping[str, Any],
        actor: str,
        expected_revision_id: str | None = None,
        fixture_origin: str | None = None,
    ) -> tuple[dict[str, Any] | None, TransactionResult | None]:
        _require_approval(actor, approval)
        validate_id(tag_id, Namespace.TAG)
        namespace = TARGET_NAMESPACES.get(target_kind)
        if namespace is None or state not in {"assigned", "removed"}:
            raise _error(SCHEMA_VALIDATION_FAILED, "tag-link-bundle", None, "/target_kind", "unsupported Tag target kind or state")
        validate_id(target_id, namespace)
        tag_bundle = self._read_tag_bundle(self.layout.tag_bundle_path(tag_id))
        if tag_bundle is None:
            raise _error(UNRESOLVED_REFERENCE, "tag-link-bundle", None, "/tag_id", "Tag does not exist")
        tag = active_tag(tag_bundle)
        self._require_target(target_kind, target_id)
        existing = self._find_assignment(tag_id, target_kind, target_id)
        if existing is None and state == "removed":
            return None, None
        if existing is not None and expected_revision_id is None:
            raise _error(WRITE_CONFLICT, "tag-link-bundle", existing["tag_link_id"], "/expected_revision_id", "current Tag assignment head is required for revision")
        if expected_revision_id is not None and (existing or {}).get("active_revision_id") != expected_revision_id:
            raise _error(WRITE_CONFLICT, "tag-link-bundle", None, "/expected_revision_id", "Tag assignment head changed before promotion")
        if existing is not None and active_tag_link_state(existing) == state:
            return existing, None
        if state == "assigned" and tag["status"] != "active":
            raise _error(INVALID_AUTHORITY, "tag-link-bundle", None, "/tag_id", "archived Tag cannot receive a new assignment")
        link_id = existing["tag_link_id"] if existing is not None else self.id_allocator(Namespace.TAG_LINK)
        target = self.layout.tag_link_bundle_path(link_id)
        previous = None if existing is None else existing["revisions"][-1]
        now = timestamp(self.clock)
        revision_id = self.id_allocator(Namespace.TAG_LINK_REVISION)
        revision = {
            "revision_id": revision_id,
            "revision_number": 1 if previous is None else len(existing["revisions"]) + 1,
            "predecessor": None if previous is None else {
                "revision_id": previous["revision_id"],
                "revision_digest": canonical_digest(previous),
            },
            "content_digest": canonical_digest({
                "tag_id": tag_id,
                "target_kind": target_kind,
                "target_id": target_id,
                "state": state,
            }),
            "approval": dict(approval),
            "state": state,
            "created_at": now,
        }
        bundle = {
            "schema_version": "1.0",
            "tag_link_id": link_id,
            "tag_id": tag_id,
            "target_kind": target_kind,
            "target_id": target_id,
            "active_revision_id": revision_id,
            "revisions": [revision] if existing is None else [*deepcopy(existing["revisions"]), revision],
            "created_at": now if existing is None else existing["created_at"],
            "updated_at": now,
        }
        if fixture_origin is not None:
            bundle["fixture_origin"] = fixture_origin
        tx = self._write_bundle(
            target,
            bundle,
            "tag-link-bundle",
            "organization_tag_links",
            "tag_link_id",
            tag_link_bundle_diagnostics,
            locked_precondition=lambda: self._require_assignment_commit_preconditions(
                tag_id,
                target_kind,
                target_id,
                link_id,
                state,
            ),
        )
        return bundle, tx

    def read_tag(self, tag_id: str) -> dict[str, Any]:
        validate_id(tag_id, Namespace.TAG)
        bundle = self._read_tag_bundle(self.layout.tag_bundle_path(tag_id))
        if bundle is None:
            raise _error(UNRESOLVED_REFERENCE, "tag-bundle", tag_id, "/tag_id", "Tag does not exist")
        tag = active_tag(bundle)
        assignments = self.list_assignments(tag_id=tag_id, include_removed=False)
        return {**tag, "revision_id": bundle["active_revision_id"], "assignment_count": len(assignments)}

    def list_tags(self, *, include_archived: bool = False) -> list[dict[str, Any]]:
        root = self.layout.knowledge_root / "organization" / "tags" / "by_id"
        result = []
        for path in sorted(root.glob("*.tag-bundle.json")) if root.exists() else []:
            bundle = self._read_tag_bundle(path)
            tag = active_tag(bundle)
            if include_archived or tag["status"] == "active":
                result.append({**tag, "revision_id": bundle["active_revision_id"]})
        return sorted(result, key=lambda item: (item["normalized_name"], item["tag_id"]))

    def list_assignments(
        self,
        *,
        tag_id: str | None = None,
        target_kind: str | None = None,
        target_id: str | None = None,
        include_removed: bool = False,
    ) -> list[dict[str, Any]]:
        root = self.layout.knowledge_root / "organization" / "tag_links" / "by_id"
        result = []
        for path in sorted(root.glob("*.tag-link-bundle.json")) if root.exists() else []:
            bundle = self._read_link_bundle(path)
            state = active_tag_link_state(bundle)
            if tag_id is not None and bundle["tag_id"] != tag_id:
                continue
            if target_kind is not None and bundle["target_kind"] != target_kind:
                continue
            if target_id is not None and bundle["target_id"] != target_id:
                continue
            if not include_removed and state != "assigned":
                continue
            result.append({
                "tag_link_id": bundle["tag_link_id"],
                "tag_id": bundle["tag_id"],
                "target_kind": bundle["target_kind"],
                "target_id": bundle["target_id"],
                "state": state,
                "revision_id": bundle["active_revision_id"],
                "target_availability": self.target_availability(bundle["target_kind"], bundle["target_id"]),
            })
        return result

    def target_availability(self, target_kind: str, target_id: str) -> str:
        try:
            self._require_target(target_kind, target_id)
        except ResearchKBError:
            return "target_unavailable"
        return "current"

    def _require_unique_vocabulary(self, candidate: Mapping[str, Any], tag_id: str) -> None:
        keys = {candidate["normalized_name"], *map(normalize_tag_name, candidate["aliases"])}
        for existing in self.list_tags(include_archived=True):
            if existing["tag_id"] == tag_id:
                continue
            existing_keys = {existing["normalized_name"], *map(normalize_tag_name, existing["aliases"])}
            if keys & existing_keys:
                raise _error(DUPLICATE_ID, "tag-bundle", tag_id, "/tag/name", f"Tag vocabulary conflicts with {existing['tag_id']}")

    def _require_target(self, target_kind: str, target_id: str) -> None:
        entries = self.entries_loader(self.layout)
        if target_kind == "paper":
            papers = records_of_kind(entries, "registry-paper")
            corrections = [record for kind, record in entries if kind == "registry-identity-correction"]
            projection = project_registry_identity(papers, corrections)
            target = projection.get(target_id)
            if target is None or target.get("canonical_paper_id") != target_id or target.get("library_status") != "active":
                raise _error(UNRESOLVED_REFERENCE, "tag-link-bundle", None, "/target_id", "Tag Paper target is unavailable or no longer canonical")
            return
        target_contracts = {
            "direction": (("direction-bundle", "direction"), "direction_id"),
            "field_map_entry": (("field-map-bundle", "field-map-entry"), "field_map_entry_id"),
            "question": (("question-revision-bundle", "question-mapping"), "question_id"),
        }
        kinds, id_field = target_contracts[target_kind]
        if not any(
            item.get(id_field) == target_id
            for kind in kinds
            for item in records_of_kind(entries, kind)
        ):
            raise _error(UNRESOLVED_REFERENCE, "tag-link-bundle", None, "/target_id", "Tag target does not exist")

    def _find_assignment(self, tag_id: str, target_kind: str, target_id: str) -> dict[str, Any] | None:
        root = self.layout.knowledge_root / "organization" / "tag_links" / "by_id"
        matches = []
        for path in sorted(root.glob("*.tag-link-bundle.json")) if root.exists() else []:
            bundle = self._read_link_bundle(path)
            if (bundle["tag_id"], bundle["target_kind"], bundle["target_id"]) == (tag_id, target_kind, target_id):
                matches.append(bundle)
        if len(matches) > 1:
            raise _error(DUPLICATE_ID, "tag-link-bundle", None, "", "multiple Tag links own the same assignment")
        return None if not matches else matches[0]

    def _require_assignment_owner(
        self,
        tag_id: str,
        target_kind: str,
        target_id: str,
        intended_link_id: str,
    ) -> None:
        current = self._find_assignment(tag_id, target_kind, target_id)
        if current is not None and current["tag_link_id"] != intended_link_id:
            raise _error(
                DUPLICATE_ID,
                "tag-link-bundle",
                intended_link_id,
                "",
                "another Tag link already owns the same assignment",
            )

    def _require_assignment_commit_preconditions(
        self,
        tag_id: str,
        target_kind: str,
        target_id: str,
        intended_link_id: str,
        state: str,
    ) -> None:
        self._require_assignment_owner(tag_id, target_kind, target_id, intended_link_id)
        self._require_target(target_kind, target_id)
        if state == "assigned":
            tag_bundle = self._read_tag_bundle(self.layout.tag_bundle_path(tag_id))
            if tag_bundle is None or active_tag(tag_bundle)["status"] != "active":
                raise _error(
                    INVALID_AUTHORITY,
                    "tag-link-bundle",
                    intended_link_id,
                    "/tag_id",
                    "archived or unavailable Tag cannot receive a new assignment",
                )

    def _read_tag_bundle(self, path: Path) -> dict[str, Any] | None:
        return self._read_bundle(path, "tag-bundle", tag_bundle_diagnostics)

    def _read_link_bundle(self, path: Path) -> dict[str, Any] | None:
        return self._read_bundle(path, "tag-link-bundle", tag_link_bundle_diagnostics)

    @staticmethod
    def _read_bundle(path: Path, kind: str, diagnostics_fn) -> dict[str, Any] | None:
        if not path.is_file():
            return None
        bundle = read_json_document(path, record_kind=kind)
        diagnostics = [*validate_record(kind, bundle, actor="stored"), *diagnostics_fn(bundle)]
        if diagnostics:
            raise ResearchKBError(diagnostics[0])
        return bundle

    def _write_bundle(
        self,
        target: Path,
        bundle: dict[str, Any],
        kind: str,
        store: str,
        id_field: str,
        diagnostics_fn,
        *,
        locked_precondition=None,
    ) -> TransactionResult:
        def validate_temp(path: Path) -> None:
            candidate = read_json_document(path, record_kind=kind)
            diagnostics = [*validate_record(kind, candidate, actor="stored"), *diagnostics_fn(candidate)]
            if diagnostics:
                raise ResearchKBError(diagnostics[0])

        result = self.transactions.promote_bytes(
            target=target,
            content=serialize_json(bundle),
            target_store=store,
            operation=f"{kind}_commit",
            actor="user",
            input_refs=[],
            output_refs=[bundle["active_revision_id"]],
            validator=validate_temp,
            locked_precondition=locked_precondition,
            expected_before_sha256=file_sha256(target),
        )
        return result


def _unique_aliases(values: list[str], name: str) -> list[str]:
    normalized_name = normalize_tag_name(name)
    aliases: dict[str, str] = {}
    for value in values:
        normalized = normalize_tag_name(value)
        if normalized and normalized != normalized_name:
            aliases.setdefault(normalized, value)
    return [aliases[key] for key in sorted(aliases)]


def _require_approval(actor: str, approval: Mapping[str, Any]) -> None:
    required = {"receipt_id", "approved_by", "approved_at", "origin"}
    if actor != "user" or set(approval) != required or approval.get("approved_by") != "user" or approval.get("origin") != "user_authored" or not all(isinstance(approval[field], str) and approval[field] for field in required):
        raise _error(INVALID_AUTHORITY, "tag", None, "/approval", "Tag mutation requires explicit user-authored approval")


def _error(code: str, kind: str, record_id: str | None, path: str, message: str) -> ResearchKBError:
    return ResearchKBError(Diagnostic(code, kind, record_id, path, message))


__all__ = ["TagService"]
