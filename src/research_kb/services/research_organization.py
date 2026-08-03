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
    GROUNDING_MISMATCH,
    INVALID_AUTHORITY,
    SCHEMA_VALIDATION_FAILED,
    UNRESOLVED_REFERENCE,
    Diagnostic,
    ResearchKBError,
)
from research_kb.identifiers import Namespace, allocate_id, validate_id
from research_kb.organization_bundles import (
    ORGANIZATION_BUNDLE_SPECS,
    active_organization_record,
    organization_bundle_diagnostics,
    organization_entries_diagnostics,
    organization_link_freshness,
    project_links_with_freshness,
)
from research_kb.process_events import Clock, timestamp, utc_now
from research_kb.services.question_mapping import mapping_freshness_diagnostics
from research_kb.screening_bundles import require_screening_eligible_links
from research_kb.storage.json_io import file_sha256, read_json_document, serialize_json
from research_kb.storage.transactions import TransactionManager, TransactionResult
from research_kb.workspace import WorkspaceLayout


IdAllocator = Callable[[Namespace], str]
EntriesLoader = Callable[[WorkspaceLayout], list[BundleEntry]]
APPROVED_ORIGINS = {"user_authored", "user_approved_agent_proposal"}
PRIMARY_FACTUAL_STATUSES = {"grounded", "revised"}
PRIMARY_CONTEXT_STATUSES = {"interpretive", "background_only"}


class ResearchOrganizationService:
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

    def promote_direction(
        self,
        payload: Mapping[str, Any],
        *,
        target_id: str | None = None,
        approval: Mapping[str, Any],
        actor: str,
        fixture_origin: str | None = None,
    ) -> tuple[dict[str, Any], TransactionResult | None]:
        self._require_approval(actor, approval)
        direction_id = (
            self.id_allocator(Namespace.DIRECTION)
            if target_id is None
            else validate_id(target_id, Namespace.DIRECTION)
        )
        target = self.layout.direction_bundle_path(direction_id)
        existing = self._read_bundle(target, "direction-bundle")
        existing_record = None if existing is None else active_organization_record(existing, child_field="direction")
        normalized = self._normalize_target_payload(
            payload,
            target_kind="direction",
            target_id_field="direction_id",
            target_id=direction_id,
            existing_record=existing_record,
        )
        return self._promote_bundle(
            target=target,
            bundle_kind="direction-bundle",
            target_store="organization_directions",
            target_id_field="direction_id",
            target_id=direction_id,
            child_field="direction",
            record=normalized,
            approval=approval,
            existing=existing,
            revision_namespace=Namespace.ORGANIZATION_REVISION,
            fixture_origin=fixture_origin,
        )

    def promote_field_map_entry(
        self,
        payload: Mapping[str, Any],
        *,
        target_id: str | None = None,
        approval: Mapping[str, Any],
        actor: str,
        fixture_origin: str | None = None,
    ) -> tuple[dict[str, Any], TransactionResult | None]:
        self._require_approval(actor, approval)
        field_map_id = (
            self.id_allocator(Namespace.FIELD_MAP)
            if target_id is None
            else validate_id(target_id, Namespace.FIELD_MAP)
        )
        target = self.layout.field_map_bundle_path(field_map_id)
        existing = self._read_bundle(target, "field-map-bundle")
        existing_record = None if existing is None else active_organization_record(existing, child_field="field_map_entry")
        normalized = self._normalize_target_payload(
            payload,
            target_kind="field-map-entry",
            target_id_field="field_map_entry_id",
            target_id=field_map_id,
            existing_record=existing_record,
        )
        normalized["direction_refs"] = self._normalize_direction_refs(payload.get("direction_refs", []))
        return self._promote_bundle(
            target=target,
            bundle_kind="field-map-bundle",
            target_store="organization_field_map",
            target_id_field="field_map_entry_id",
            target_id=field_map_id,
            child_field="field_map_entry",
            record=normalized,
            approval=approval,
            existing=existing,
            revision_namespace=Namespace.ORGANIZATION_REVISION,
            fixture_origin=fixture_origin,
        )

    def promote_question(
        self,
        payload: Mapping[str, Any],
        *,
        question_id: str | None = None,
        approval: Mapping[str, Any],
        actor: str,
        fixture_origin: str | None = None,
    ) -> tuple[dict[str, Any], TransactionResult | None]:
        self._require_approval(actor, approval)
        entries = self.entries_loader(self.layout)
        legacy = None
        if question_id is None:
            question_id = self.id_allocator(Namespace.QUESTION)
        else:
            validate_id(question_id, Namespace.QUESTION)
            legacy = next(
                (
                    item
                    for kind, item in entries
                    if kind == "question-mapping" and item["question_id"] == question_id
                ),
                None,
            )
        target = self.layout.question_revision_bundle_path(question_id)
        existing = self._read_bundle(target, "question-revision-bundle")
        if existing is not None and legacy is not None:
            expected = {
                "basis_kind": "legacy_question_mapping",
                "basis_id": question_id,
                "basis_digest": canonical_digest(legacy),
            }
            if existing["revisions"][0].get("predecessor") != expected:
                raise _error(GROUNDING_MISMATCH, "question-revision-bundle", question_id, "/revisions/0/predecessor", "legacy Question predecessor basis changed")
        if existing is None and legacy is None and payload.get("question_text") is None:
            raise _error(UNRESOLVED_REFERENCE, "question-revision-bundle", question_id, "/question_id", "Question does not exist and no new Question definition was supplied")

        active_revision = None if existing is None else existing["revisions"][-1]
        active_mapping = None if active_revision is None else active_revision["question_mapping"]
        basis_mapping = active_mapping or legacy
        factual_links = self._normalize_question_links(
            payload.get("factual_links", []),
            entries,
            existing_mapping=basis_mapping,
        )
        require_screening_eligible_links(
            question_id,
            (link["paper_id"] for link in factual_links),
            entries,
        )
        background_links = self._normalize_unit_links(
            payload.get("background_links", []),
            entries,
            existing_links=[] if active_revision is None else active_revision.get("background_links", []),
            link_namespace=Namespace.QUESTION_BACKGROUND,
            allowed_roles={"question_background"},
        )
        now = timestamp(self.clock)
        profile = records_of_kind(entries, "domain-profile")[0]
        question_mapping = {
            "schema_version": "1.0",
            "question_id": question_id,
            "question_text": payload.get("question_text", (basis_mapping or {}).get("question_text")),
            "scope": payload.get("scope", (basis_mapping or {}).get("scope")),
            "domain_profile_id": (basis_mapping or {}).get("domain_profile_id", profile["domain_profile"]["id"]),
            "paper_links": factual_links,
            "mapping_status": payload.get("mapping_status", (basis_mapping or {}).get("mapping_status", "ai_draft")),
            "created_at": (basis_mapping or {}).get("created_at", now),
            "updated_at": now,
        }
        if fixture_origin is not None:
            question_mapping["fixture_origin"] = fixture_origin
        semantic_digest = _question_content_digest(question_mapping, background_links)
        if active_revision is not None and active_revision.get("content_digest") == semantic_digest:
            return existing, None

        revision_id = self.id_allocator(Namespace.QUESTION_REVISION)
        if active_revision is not None:
            predecessor: dict[str, Any] | None = {
                "basis_kind": "question_revision",
                "basis_id": active_revision["revision_id"],
                "basis_digest": canonical_digest(active_revision),
            }
        elif legacy is not None:
            predecessor = {
                "basis_kind": "legacy_question_mapping",
                "basis_id": question_id,
                "basis_digest": canonical_digest(legacy),
            }
        else:
            predecessor = None
        revision = {
            "revision_id": revision_id,
            "revision_number": 1 if existing is None else len(existing["revisions"]) + 1,
            "predecessor": predecessor,
            "content_digest": semantic_digest,
            "approval": dict(approval),
            "question_mapping": question_mapping,
            "background_links": background_links,
            "created_at": now,
        }
        bundle = {
            "schema_version": "1.0",
            "question_id": question_id,
            "active_revision_id": revision_id,
            "revisions": [revision] if existing is None else [*deepcopy(existing["revisions"]), revision],
            "created_at": now if existing is None else existing["created_at"],
            "updated_at": now,
        }
        if fixture_origin is not None:
            bundle["fixture_origin"] = fixture_origin
        transaction = self._write_bundle(
            target,
            bundle,
            bundle_kind="question-revision-bundle",
            target_store="organization_questions",
            target_id_field="question_id",
            child_field="question_mapping",
            expected_before=file_sha256(target),
        )
        return bundle, transaction

    def read_direction(self, direction_id: str) -> dict[str, Any]:
        validate_id(direction_id, Namespace.DIRECTION)
        return self._read_projection(
            self.layout.direction_bundle_path(direction_id),
            bundle_kind="direction-bundle",
            child_field="direction",
        )

    def read_field_map_entry(self, field_map_entry_id: str) -> dict[str, Any]:
        validate_id(field_map_entry_id, Namespace.FIELD_MAP)
        return self._read_projection(
            self.layout.field_map_bundle_path(field_map_entry_id),
            bundle_kind="field-map-bundle",
            child_field="field_map_entry",
        )

    def read_question(self, question_id: str) -> dict[str, Any]:
        validate_id(question_id, Namespace.QUESTION)
        target = self.layout.question_revision_bundle_path(question_id)
        bundle = self._read_bundle(target, "question-revision-bundle")
        entries = self.entries_loader(self.layout)
        diagnostics = organization_entries_diagnostics(entries)
        if diagnostics:
            raise ResearchKBError(diagnostics[0])
        if bundle is None:
            legacy = next(
                (item for item in records_of_kind(entries, "question-mapping") if item["question_id"] == question_id),
                None,
            )
            if legacy is None:
                raise _error(UNRESOLVED_REFERENCE, "question-mapping", question_id, "/question_id", "Question does not exist")
            return {
                **_project_question_freshness(legacy, entries),
                "compatibility_source": "legacy",
                "background_links": [],
            }
        revision = bundle["revisions"][-1]
        return {
            **_project_question_freshness(revision["question_mapping"], entries),
            "compatibility_source": "p7_revision",
            "revision_id": revision["revision_id"],
            "background_links": [
                {
                    **deepcopy(item),
                    "link": {
                        **deepcopy(item["link"]),
                        "freshness": organization_link_freshness(item["link"], entries),
                    },
                }
                for item in revision.get("background_links", [])
            ],
        }

    def list_directions(self) -> list[dict[str, Any]]:
        return self._list_projections(
            self.layout.knowledge_root / "organization" / "directions" / "by_id",
            "*.direction-bundle.json",
            "direction-bundle",
            "direction",
        )

    def list_field_map_entries(self) -> list[dict[str, Any]]:
        return self._list_projections(
            self.layout.knowledge_root / "organization" / "field_map" / "by_id",
            "*.field-map-bundle.json",
            "field-map-bundle",
            "field_map_entry",
        )

    def _promote_bundle(
        self,
        *,
        target: Path,
        bundle_kind: str,
        target_store: str,
        target_id_field: str,
        target_id: str,
        child_field: str,
        record: dict[str, Any],
        approval: Mapping[str, Any],
        existing: dict[str, Any] | None,
        revision_namespace: Namespace,
        fixture_origin: str | None,
    ) -> tuple[dict[str, Any], TransactionResult | None]:
        content_digest = canonical_digest(record)
        if existing is not None and existing["revisions"][-1].get("content_digest") == content_digest:
            return existing, None
        now = timestamp(self.clock)
        revision_id = self.id_allocator(revision_namespace)
        previous = None if existing is None else existing["revisions"][-1]
        revision = {
            "revision_id": revision_id,
            "revision_number": 1 if previous is None else len(existing["revisions"]) + 1,
            "predecessor": None if previous is None else {
                "revision_id": previous["revision_id"],
                "revision_digest": canonical_digest(previous),
            },
            "content_digest": content_digest,
            "approval": dict(approval),
            child_field: record,
            "created_at": now,
        }
        bundle = {
            "schema_version": "1.0",
            target_id_field: target_id,
            "active_revision_id": revision_id,
            "revisions": [revision] if existing is None else [*deepcopy(existing["revisions"]), revision],
            "created_at": now if existing is None else existing["created_at"],
            "updated_at": now,
        }
        if fixture_origin is not None:
            bundle["fixture_origin"] = fixture_origin
        transaction = self._write_bundle(
            target,
            bundle,
            bundle_kind=bundle_kind,
            target_store=target_store,
            target_id_field=target_id_field,
            child_field=child_field,
            expected_before=file_sha256(target),
        )
        return bundle, transaction

    def _normalize_target_payload(
        self,
        payload: Mapping[str, Any],
        *,
        target_kind: str,
        target_id_field: str,
        target_id: str,
        existing_record: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        required = (
            {"name", "scope", "status", "unit_links", "gap_notes"}
            if target_kind == "direction"
            else {"title", "entry_type", "definition", "status", "consensus_level", "direction_refs", "unit_links", "aspect_notes"}
        )
        if set(payload) != required:
            raise _error(SCHEMA_VALIDATION_FAILED, target_kind, target_id, "/payload", "payload fields do not match the closed organization contract")
        entries = self.entries_loader(self.layout)
        links = self._normalize_unit_links(
            payload["unit_links"],
            entries,
            existing_links=[] if existing_record is None else existing_record.get("links", []),
            link_namespace=Namespace.ORGANIZATION_LINK,
            allowed_roles={"factual_example", "background_context"},
        )
        result = {
            "schema_version": "1.0",
            target_id_field: target_id,
            **{key: deepcopy(value) for key, value in payload.items() if key not in {"unit_links", "direction_refs"}},
            "links": links,
        }
        return result

    def _normalize_unit_links(
        self,
        value: object,
        entries: list[BundleEntry],
        *,
        existing_links: list[Mapping[str, Any]],
        link_namespace: Namespace,
        allowed_roles: set[str],
    ) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            raise _error(SCHEMA_VALIDATION_FAILED, "organization-link", None, "/unit_links", "unit_links must be an array")
        unwrapped_existing = [item.get("link", item) for item in existing_links]
        existing_by_key = {
            _link_key(item): item.get("organization_link_id")
            for item in unwrapped_existing
        }
        result: dict[tuple[str, str, str], dict[str, Any]] = {}
        for index, source in enumerate(value):
            base = f"/unit_links/{index}"
            if not isinstance(source, Mapping):
                raise _error(SCHEMA_VALIDATION_FAILED, "organization-link", None, base, "organization link must be an object")
            allowed = {"source_kind", "paper_id", "review_memory_id", "unit_id", "role", "rationale"}
            if set(source) - allowed:
                raise _error(SCHEMA_VALIDATION_FAILED, "organization-link", None, base, "organization link contains unsupported fields")
            role = source.get("role")
            if role not in allowed_roles:
                raise _error(GROUNDING_MISMATCH, "organization-link", None, base + "/role", "organization link role is not allowed for this target")
            normalized = self._resolve_unit_link(source, entries, base)
            key = _link_key(normalized)
            if key in result:
                continue
            normalized["organization_link_id"] = existing_by_key.get(key) or self.id_allocator(Namespace.ORGANIZATION_LINK)
            result[key] = normalized
        links = [result[key] for key in sorted(result)]
        if link_namespace == Namespace.QUESTION_BACKGROUND:
            existing_background_ids = {
                _link_key(item.get("link", {})): item.get("question_background_id")
                for item in existing_links
            }
            return [
                {
                    "question_background_id": existing_background_ids.get(_link_key(link))
                    or self.id_allocator(Namespace.QUESTION_BACKGROUND),
                    "link": link,
                }
                for link in links
            ]
        return links

    def _resolve_unit_link(
        self,
        source: Mapping[str, Any],
        entries: list[BundleEntry],
        path: str,
    ) -> dict[str, Any]:
        source_kind = source.get("source_kind")
        paper_id = source.get("paper_id")
        unit_id = source.get("unit_id")
        role = source.get("role")
        rationale = source.get("rationale")
        if not all(isinstance(item, str) and item for item in (paper_id, unit_id, rationale)):
            raise _error(SCHEMA_VALIDATION_FAILED, "organization-link", None, path, "paper_id, unit_id and rationale are required strings")
        if source_kind == "primary":
            card = next((item for item in records_of_kind(entries, "paper-card") if item["paper_id"] == paper_id), None)
            unit = next(
                (unit for section in (card or {}).get("sections", []) for unit in section.get("units", []) if unit["unit_id"] == unit_id),
                None,
            )
            if unit is None:
                raise _error(UNRESOLVED_REFERENCE, "organization-link", None, path + "/unit_id", "current Primary Card Unit does not exist")
            status = unit.get("grounding_status")
            if role == "factual_example" and status not in PRIMARY_FACTUAL_STATUSES:
                raise _error(GROUNDING_MISMATCH, "organization-link", None, path + "/unit_id", "factual organization links require grounded or revised Primary Units")
            if role in {"background_context", "question_background"} and status not in PRIMARY_CONTEXT_STATUSES:
                raise _error(GROUNDING_MISMATCH, "organization-link", None, path + "/unit_id", "Primary context links require interpretive or background_only Units")
            evidence_ids = sorted(unit.get("evidence_ids", [])) if role == "factual_example" else []
            evidence = {item["evidence_id"]: item for item in records_of_kind(entries, "evidence")}
            if any(
                evidence_id not in evidence
                or evidence[evidence_id].get("paper_id") != paper_id
                or evidence[evidence_id].get("canonical") is not True
                for evidence_id in evidence_ids
            ):
                raise _error(GROUNDING_MISMATCH, "organization-link", None, path + "/unit_id", "Primary factual Unit does not have current canonical Evidence closure")
            background = role in {"background_context", "question_background"}
            return {
                "schema_version": "1.0",
                "source_kind": "primary_unit",
                "paper_id": paper_id,
                "source_unit_id": unit_id,
                "source_revision_id": _source_revision(entries, "primary-semantic-bundle", paper_id),
                "role": role,
                "rationale": rationale,
                "evidence_ids": evidence_ids,
                "background_only": background,
                "can_enter_canonical_evidence": False,
                "not_fact": background,
            }
        if source_kind == "review":
            memory = next((item for item in records_of_kind(entries, "review-memory") if item["paper_id"] == paper_id), None)
            unit = next(
                (unit for section in (memory or {}).get("sections", []) for unit in section.get("units", []) if unit["review_unit_id"] == unit_id),
                None,
            )
            if (
                role not in {"background_context", "question_background"}
                or memory is None
                or memory.get("review_memory_id") != source.get("review_memory_id")
                or unit is None
                or unit.get("background_only") is not True
                or unit.get("can_enter_canonical_evidence") is not False
                or unit.get("not_fact") is not True
                or not unit.get("source_notes")
            ):
                raise _error(GROUNDING_MISMATCH, "organization-link", None, path + "/unit_id", "Review links require a current provenance-closed background-only Review Unit")
            return {
                "schema_version": "1.0",
                "source_kind": "review_unit",
                "paper_id": paper_id,
                "review_memory_id": memory["review_memory_id"],
                "source_unit_id": unit_id,
                "source_revision_id": _source_revision(entries, "review-semantic-bundle", paper_id),
                "role": role,
                "rationale": rationale,
                "evidence_ids": [],
                "background_only": True,
                "can_enter_canonical_evidence": False,
                "not_fact": True,
            }
        raise _error(SCHEMA_VALIDATION_FAILED, "organization-link", None, path + "/source_kind", "source_kind must be primary or review")

    def _normalize_question_links(
        self,
        value: object,
        entries: list[BundleEntry],
        *,
        existing_mapping: Mapping[str, Any] | None,
    ) -> list[dict[str, Any]]:
        if not isinstance(value, list) or not value:
            raise _error(SCHEMA_VALIDATION_FAILED, "question-mapping", None, "/factual_links", "factual_links must be a non-empty array")
        cards = {item["paper_id"]: item for item in records_of_kind(entries, "paper-card")}
        evidence = {item["evidence_id"]: item for item in records_of_kind(entries, "evidence")}
        queues = {item["queue_id"]: item for item in records_of_kind(entries, "review-queue")}
        existing_ids = {
            item["paper_id"]: item["question_link_id"]
            for item in (existing_mapping or {}).get("paper_links", [])
        }
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        for index, source in enumerate(value):
            base = f"/factual_links/{index}"
            if not isinstance(source, Mapping):
                raise _error(SCHEMA_VALIDATION_FAILED, "question-mapping", None, base, "factual link must be an object")
            required = {"paper_id", "selected_card_unit_ids", "role_in_question", "relevance_rationale", "boundary_refs"}
            if set(source) != required:
                raise _error(SCHEMA_VALIDATION_FAILED, "question-mapping", None, base, "factual link fields do not match the closed contract")
            paper_id = source.get("paper_id")
            if not isinstance(paper_id, str) or paper_id in seen:
                raise _error(DUPLICATE_ID, "question-mapping", None, base + "/paper_id", "Question factual links require unique paper IDs")
            seen.add(paper_id)
            card = cards.get(paper_id)
            unit_ids = _unique_strings(source.get("selected_card_unit_ids"), base + "/selected_card_unit_ids")
            boundary_ids = set(_unique_strings(source.get("boundary_refs"), base + "/boundary_refs"))
            if card is None or not unit_ids:
                raise _error(UNRESOLVED_REFERENCE, "question-mapping", None, base, "Question factual link requires a current Paper Card and at least one Unit")
            units = {
                unit["unit_id"]: unit
                for section in card["sections"]
                for unit in section["units"]
            }
            evidence_ids: set[str] = set()
            for unit_id in unit_ids:
                unit = units.get(unit_id)
                if unit is None or unit.get("grounding_status") not in PRIMARY_FACTUAL_STATUSES:
                    raise _error(GROUNDING_MISMATCH, "question-mapping", None, base + "/selected_card_unit_ids", "Question factual links require current grounded or revised Primary Units")
                evidence_ids.update(unit.get("evidence_ids", []))
                boundary_ids.update(unit.get("boundary_refs", []))
            if any(evidence_id not in evidence or evidence[evidence_id].get("paper_id") != paper_id or evidence[evidence_id].get("canonical") is not True for evidence_id in evidence_ids):
                raise _error(GROUNDING_MISMATCH, "question-mapping", None, base + "/evidence_ids", "Question factual Evidence closure is missing or inadmissible")
            if any(queue_id not in queues or queues[queue_id].get("paper_id") != paper_id for queue_id in boundary_ids):
                raise _error(GROUNDING_MISMATCH, "question-mapping", None, base + "/boundary_refs", "Question boundary belongs to another paper or is missing")
            result.append(
                {
                    "question_link_id": existing_ids.get(paper_id) or self.id_allocator(Namespace.QUESTION_LINK),
                    "paper_id": paper_id,
                    "selected_card_unit_ids": sorted(unit_ids),
                    "role_in_question": source["role_in_question"],
                    "relevance_rationale": source["relevance_rationale"],
                    "evidence_ids": sorted(evidence_ids),
                    "boundary_refs": sorted(boundary_ids),
                }
            )
        return sorted(result, key=lambda item: item["paper_id"])

    def _normalize_direction_refs(self, value: object) -> list[dict[str, str]]:
        direction_ids = _unique_strings(value, "/direction_refs")
        result: list[dict[str, str]] = []
        for direction_id in direction_ids:
            validate_id(direction_id, Namespace.DIRECTION)
            bundle = self._read_bundle(self.layout.direction_bundle_path(direction_id), "direction-bundle")
            if bundle is None:
                raise _error(UNRESOLVED_REFERENCE, "field-map-entry", None, "/direction_refs", "linked Direction does not exist")
            result.append({"direction_id": direction_id, "direction_revision_id": bundle["active_revision_id"]})
        return result

    def _read_projection(
        self,
        target: Path,
        *,
        bundle_kind: str,
        child_field: str,
        entries: list[BundleEntry] | None = None,
    ) -> dict[str, Any]:
        bundle = self._read_bundle(target, bundle_kind)
        if bundle is None:
            raise _error(UNRESOLVED_REFERENCE, bundle_kind, None, "", "organization target does not exist")
        record = active_organization_record(bundle, child_field=child_field)
        if record is None:
            raise _error(UNRESOLVED_REFERENCE, bundle_kind, None, "/active_revision_id", "organization target has no active revision")
        entries = self.entries_loader(self.layout) if entries is None else entries
        record["links"] = project_links_with_freshness(record.get("links", []), entries)
        if child_field == "field_map_entry":
            projected_refs = []
            for ref in record.get("direction_refs", []):
                direction = self._read_bundle(
                    self.layout.direction_bundle_path(ref["direction_id"]),
                    "direction-bundle",
                )
                current = (
                    direction is not None
                    and direction.get("active_revision_id") == ref["direction_revision_id"]
                )
                projected_refs.append(
                    {
                        **ref,
                        "freshness": {
                            "status": "current" if current else "stale_upstream",
                            "reasons": [] if current else ["linked_direction_revision_unavailable"],
                        },
                    }
                )
            record["direction_refs"] = projected_refs
        record["revision_id"] = bundle["active_revision_id"]
        return record

    def _list_projections(self, root: Path, pattern: str, bundle_kind: str, child_field: str) -> list[dict[str, Any]]:
        if not root.exists():
            return []
        entries = self.entries_loader(self.layout)
        return [
            self._read_projection(
                path,
                bundle_kind=bundle_kind,
                child_field=child_field,
                entries=entries,
            )
            for path in sorted(root.glob(pattern))
        ]

    def _read_bundle(self, target: Path, bundle_kind: str) -> dict[str, Any] | None:
        if not target.is_file():
            return None
        bundle = read_json_document(target, record_kind=bundle_kind)
        diagnostics = validate_record(bundle_kind, bundle, actor="stored")
        target_id_field, child_field, _ = ORGANIZATION_BUNDLE_SPECS[bundle_kind]
        diagnostics.extend(
            organization_bundle_diagnostics(
                bundle,
                bundle_kind=bundle_kind,
                target_id_field=target_id_field,
                child_field=child_field,
            )
        )
        if diagnostics:
            raise ResearchKBError(diagnostics[0])
        return bundle

    def _write_bundle(
        self,
        target: Path,
        bundle: dict[str, Any],
        *,
        bundle_kind: str,
        target_store: str,
        target_id_field: str,
        child_field: str,
        expected_before: str | None,
    ) -> TransactionResult:
        def validate_temp(path: Path) -> None:
            candidate = read_json_document(path, record_kind=bundle_kind)
            diagnostics = validate_record(bundle_kind, candidate, actor="stored")
            diagnostics.extend(
                organization_bundle_diagnostics(
                    candidate,
                    bundle_kind=bundle_kind,
                    target_id_field=target_id_field,
                    child_field=child_field,
                )
            )
            if diagnostics:
                raise ResearchKBError(diagnostics[0])

        active = bundle["revisions"][-1]
        return self.transactions.promote_bytes(
            target=target,
            content=serialize_json(bundle),
            target_store=target_store,
            operation=f"{bundle_kind}_commit",
            actor="user",
            input_refs=[item for item in _approval_refs(active["approval"])],
            output_refs=[active["revision_id"]],
            validator=validate_temp,
            expected_before_sha256=expected_before,
        )

    @staticmethod
    def _require_approval(actor: str, approval: Mapping[str, Any]) -> None:
        if actor != "user" or approval.get("approved_by") != "user" or approval.get("origin") not in APPROVED_ORIGINS:
            raise _error(INVALID_AUTHORITY, "organization", None, "/approval", "organization promotion requires explicit user approval")
        common = {"approved_by", "approved_at", "origin"}
        origin_fields = (
            {"task_id", "task_result_digest"}
            if approval.get("origin") == "user_approved_agent_proposal"
            else {"receipt_id"}
        )
        required = common | origin_fields
        if set(approval) != required or not all(isinstance(approval[field], str) and approval[field] for field in required):
            raise _error(SCHEMA_VALIDATION_FAILED, "organization", None, "/approval", "approval receipt is incomplete")


def _source_revision(entries: list[BundleEntry], kind: str, paper_id: str) -> str | None:
    owners = [record for entry_kind, record in entries if entry_kind == kind and record.get("paper_id") == paper_id]
    if len(owners) > 1:
        raise _error(GROUNDING_MISMATCH, "organization-link", None, "/paper_id", "multiple active semantic authorities exist for the paper")
    return None if not owners else owners[0]["active_revision_id"]


def _link_key(link: Mapping[str, Any]) -> tuple[str, str, str]:
    return str(link.get("source_kind")), str(link.get("source_unit_id")), str(link.get("role"))


def _unique_strings(value: object, path: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise _error(SCHEMA_VALIDATION_FAILED, "organization", None, path, "value must be an array of non-empty strings")
    if len(value) != len(set(value)):
        raise _error(DUPLICATE_ID, "organization", None, path, "duplicate identifier")
    return sorted(value)


def _question_content_digest(question_mapping: Mapping[str, Any], background_links: list[dict[str, Any]]) -> str:
    mapping = {
        key: deepcopy(value)
        for key, value in question_mapping.items()
        if key not in {"created_at", "updated_at", "fixture_origin"}
    }
    return canonical_digest({"question_mapping": mapping, "background_links": background_links})


def _approval_refs(approval: Mapping[str, Any]) -> list[str]:
    task_id = approval.get("task_id")
    return [task_id] if isinstance(task_id, str) and task_id.startswith("task_") else []


def _project_question_freshness(
    mapping: Mapping[str, Any],
    entries: list[BundleEntry],
) -> dict[str, Any]:
    projected = deepcopy(dict(mapping))
    links = []
    for link in projected["paper_links"]:
        scoped = {**projected, "paper_links": [link]}
        reasons = sorted({item.message for item in mapping_freshness_diagnostics(scoped, entries)})
        links.append(
            {
                **link,
                "freshness": {
                    "status": "current" if not reasons else "stale_upstream",
                    "reasons": reasons,
                },
                "factual_support_eligible": not reasons,
            }
        )
    projected["paper_links"] = links
    return projected


def _error(code: str, kind: str, record_id: str | None, path: str, message: str) -> ResearchKBError:
    return ResearchKBError(Diagnostic(code, kind, record_id, path, message))


__all__ = ["ResearchOrganizationService"]
