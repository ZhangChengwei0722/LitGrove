from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime
from typing import Any
from urllib.parse import urldefrag

from jsonschema import Draft202012Validator, FormatChecker

from research_kb.catalog.models import canonical_digest
from research_kb.agent_tasks import agent_task_chain_diagnostics
from research_kb.contracts.registry import SchemaRegistry
from research_kb.contracts.versions import require_supported
from research_kb.errors import (
    DUPLICATE_ID,
    DUPLICATE_PAPER_CARD,
    DUPLICATE_REVIEW_MEMORY,
    GROUNDING_MISMATCH,
    INCOMPLETE_TRANSACTION,
    INVALID_AUTHORITY,
    PATH_ESCAPE,
    QUEUE_AS_EVIDENCE,
    SCHEMA_VALIDATION_FAILED,
    SNAPSHOT_MISMATCH,
    STEP7_BOUNDARY,
    UNRESOLVED_REFERENCE,
    UNSUPPORTED_VERSION,
    UNKNOWN_SCHEMA_KIND,
    Diagnostic,
    ResearchKBError,
    json_pointer,
)
from research_kb.evidence_provenance import index_active_pages, parse_locator, validate_evidence_against_pages
from research_kb.guardian_dispositions import guardian_disposition_diagnostics
from research_kb.identity_corrections import identity_correction_diagnostics
from research_kb.parser_profiles import parser_profile_descriptor
from research_kb.paths import normalize_relative_path, validate_config_relative_path
from research_kb.pipeline_jobs import (
    TERMINAL_STATUSES,
    current_pipeline_states,
    pipeline_job_chain_diagnostics,
    validate_wait_state,
)
from research_kb.primary_bundles import (
    expand_active_primary_entries,
    mixed_primary_authority_diagnostics,
    primary_bundle_diagnostics,
)
from research_kb.review_memory_provenance import (
    build_active_parse_index,
    review_memory_freshness,
    validate_review_memory_provenance,
)
from research_kb.review_bundles import (
    expand_active_review_entries,
    mixed_review_authority_diagnostics,
    review_bundle_diagnostics,
)
from research_kb.source_assets import (
    current_source_asset_heads,
    source_asset_chain_diagnostics,
)


CONFIG_KINDS = {"workspace", "domain-profile", "mutation-request"}
RESULT_CONTRACT_KINDS = {
    "document-route-decision",
    "primary-semantic-candidate",
    "review-semantic-candidate",
}
HUMAN_ONLY_REVIEW_STATES = {"human_checked", "verified"}
NON_SUPPORTING_UNIT_STATES = {"interpretive", "background_only", "needs_resolution"}


class RecordValidationSession:
    def __init__(
        self,
        kind: str,
        *,
        registry: SchemaRegistry | None = None,
        actor: str = "agent",
    ):
        self.kind = kind
        self.registry = registry or SchemaRegistry()
        self.actor = actor
        if kind not in self.registry.kinds:
            raise ResearchKBError(
                Diagnostic(
                    UNKNOWN_SCHEMA_KIND,
                    kind,
                    None,
                    "",
                    f"unknown record schema kind: {kind}",
                )
            )
        self.validator = Draft202012Validator(
            _validation_schema(self.registry, kind),
            registry=self.registry.referencing_registry(),
            format_checker=FormatChecker(),
        )

    def validate(self, record: dict[str, Any]) -> list[Diagnostic]:
        version_field = "contract_version" if self.kind in CONFIG_KINDS else "schema_version"
        if self.kind not in RESULT_CONTRACT_KINDS:
            try:
                require_supported(record.get(version_field))
            except ResearchKBError as error:
                diagnostic = error.diagnostic
                return [
                    Diagnostic(
                        diagnostic.code,
                        self.kind,
                        _record_id(self.kind, record),
                        f"/{version_field}",
                        diagnostic.message,
                        diagnostic.severity,
                    )
                ]

        diagnostics = [
            Diagnostic(
                SCHEMA_VALIDATION_FAILED,
                self.kind,
                _record_id(self.kind, record),
                json_pointer(error.absolute_path),
                error.message,
            )
            for error in sorted(
                self.validator.iter_errors(record),
                key=lambda item: (list(item.absolute_path), item.message),
            )
        ]
        diagnostics.extend(_local_semantic_diagnostics(self.kind, record))
        diagnostics.extend(_authority_diagnostics(self.kind, record, self.actor))
        return diagnostics


def validate_record(
    kind: str,
    record: dict[str, Any],
    *,
    registry: SchemaRegistry | None = None,
    actor: str = "agent",
) -> list[Diagnostic]:
    try:
        session = RecordValidationSession(kind, registry=registry, actor=actor)
    except ResearchKBError as error:
        return [error.diagnostic]
    return session.validate(record)


def validate_bundle(
    bundle: dict[str, Any] | Iterable[dict[str, Any]],
    *,
    registry: SchemaRegistry | None = None,
    actor: str = "cli",
) -> list[Diagnostic]:
    entries = list(bundle.get("records", [])) if isinstance(bundle, dict) else list(bundle)
    schema_registry = registry or SchemaRegistry()
    diagnostics: list[Diagnostic] = []
    normalized: list[tuple[str, dict[str, Any]]] = []
    sessions: dict[str, RecordValidationSession] = {}
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict) or not isinstance(entry.get("kind"), str) or not isinstance(entry.get("record"), dict):
            diagnostics.append(
                Diagnostic(SCHEMA_VALIDATION_FAILED, "bundle", None, f"/records/{index}", "bundle entry must contain kind and record")
            )
            continue
        kind = entry["kind"]
        record = entry["record"]
        try:
            session = sessions.get(kind)
            if session is None:
                session = RecordValidationSession(kind, registry=schema_registry, actor=actor)
                sessions[kind] = session
            record_diagnostics = session.validate(record)
        except ResearchKBError as error:
            record_diagnostics = [error.diagnostic]
        diagnostics.extend(record_diagnostics)
        if not any(item.code in {UNSUPPORTED_VERSION, SCHEMA_VALIDATION_FAILED, UNKNOWN_SCHEMA_KIND} for item in record_diagnostics):
            normalized.append((kind, record))
    diagnostics.extend(mixed_primary_authority_diagnostics(normalized))
    diagnostics.extend(mixed_review_authority_diagnostics(normalized))
    expanded = expand_active_primary_entries(normalized)
    expanded = expand_active_review_entries(expanded)
    for kind, record in expanded[len(normalized):]:
        diagnostics.extend(validate_record(kind, record, registry=schema_registry, actor=actor))
    diagnostics.extend(_cross_record_diagnostics(expanded))
    return _deduplicate_diagnostics(diagnostics)


def _validation_schema(registry: SchemaRegistry, kind: str) -> dict[str, Any]:
    schemas = registry.schemas()
    roots_by_id = {schema["$id"]: schema for schema in schemas.values()}
    root = schemas[kind]
    try:
        return _inline_schema_references(root, root, roots_by_id, ())
    except (KeyError, TypeError, ValueError):
        return root


def _inline_schema_references(
    node: Any,
    resource_root: dict[str, Any],
    roots_by_id: dict[str, dict[str, Any]],
    stack: tuple[tuple[str, str], ...],
) -> Any:
    if isinstance(node, list):
        return [
            _inline_schema_references(item, resource_root, roots_by_id, stack)
            for item in node
        ]
    if not isinstance(node, dict):
        return node
    if set(node) == {"$ref"}:
        uri, fragment = urldefrag(node["$ref"])
        target_root = resource_root if not uri else roots_by_id[uri]
        identity = (target_root["$id"], fragment)
        if identity in stack:
            raise ValueError("recursive schema reference cannot be inlined")
        target: Any = target_root
        if fragment:
            if not fragment.startswith("/"):
                raise ValueError("unsupported schema fragment")
            for component in fragment[1:].split("/"):
                key = component.replace("~1", "/").replace("~0", "~")
                target = target[int(key)] if isinstance(target, list) else target[key]
        return _inline_schema_references(
            target,
            target_root,
            roots_by_id,
            (*stack, identity),
        )
    return {
        key: _inline_schema_references(value, resource_root, roots_by_id, stack)
        for key, value in node.items()
    }


def _authority_diagnostics(kind: str, record: dict[str, Any], actor: str) -> list[Diagnostic]:
    if actor == "stored":
        return []
    diagnostics: list[Diagnostic] = []
    review_status = record.get("review_status")
    if actor != "user" and review_status in HUMAN_ONLY_REVIEW_STATES:
        diagnostics.append(
            Diagnostic(INVALID_AUTHORITY, kind, _record_id(kind, record), "/review_status", "human-only review state")
        )
    if actor == "agent" and record.get("automation_status") not in {None, "pending"}:
        diagnostics.append(
            Diagnostic(INVALID_AUTHORITY, kind, _record_id(kind, record), "/automation_status", "Agent input cannot assign automation result")
        )
    if kind == "registry-paper" and actor != "user" and record.get("screening_status") in {"included", "excluded"}:
        diagnostics.append(
            Diagnostic(INVALID_AUTHORITY, kind, _record_id(kind, record), "/screening_status", "final screening state is user-only")
        )
    return diagnostics


def _local_semantic_diagnostics(kind: str, record: dict[str, Any]) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    if kind == "workspace" and isinstance(record.get("workspace"), dict):
        for field in ("knowledge_root", "local_inbox", "domain_profile"):
            value = record["workspace"].get(field)
            if not isinstance(value, str):
                continue
            try:
                validate_config_relative_path(value)
            except ResearchKBError as error:
                source = error.diagnostic
                diagnostics.append(
                    Diagnostic(source.code, kind, _record_id(kind, record), f"/workspace/{field}", source.message)
                )
    elif kind in {"registry-paper", "source-asset-state"} and isinstance(record.get("source_ref"), dict):
        value = record["source_ref"].get("relative_path")
        if isinstance(value, str):
            try:
                normalize_relative_path(value)
            except ResearchKBError as error:
                source = error.diagnostic
                diagnostics.append(
                    Diagnostic(source.code, kind, _record_id(kind, record), "/source_ref/relative_path", source.message)
                )
    elif kind == "paper-card" and isinstance(record.get("sections"), list):
        for section_index, section in enumerate(record["sections"]):
            if not isinstance(section, dict) or not isinstance(section.get("units"), list):
                continue
            for unit_index, unit in enumerate(section["units"]):
                if not isinstance(unit, dict):
                    continue
                status = unit.get("grounding_status")
                evidence_ids = unit.get("evidence_ids")
                base = f"/sections/{section_index}/units/{unit_index}/evidence_ids"
                if status in {"grounded", "revised"} and evidence_ids == []:
                    diagnostics.append(Diagnostic(GROUNDING_MISMATCH, kind, unit.get("unit_id"), base, "grounded/revised unit requires evidence"))
                if status in NON_SUPPORTING_UNIT_STATES and isinstance(evidence_ids, list) and evidence_ids:
                    diagnostics.append(Diagnostic(GROUNDING_MISMATCH, kind, unit.get("unit_id"), base, "non-supporting unit cannot expose supporting evidence"))
    elif kind == "review-memory" and isinstance(record.get("sections"), list):
        unit_count = 0
        signatures: set[tuple[str, str, str]] = set()
        for section_index, section in enumerate(record["sections"]):
            if not isinstance(section, dict) or not isinstance(section.get("units"), list):
                continue
            section_id = section.get("section_id")
            for unit_index, unit in enumerate(section["units"]):
                if not isinstance(unit, dict):
                    continue
                unit_count += 1
                base = f"/sections/{section_index}/units/{unit_index}"
                unit_id = unit.get("review_unit_id")
                if unit.get("section_id") != section_id:
                    diagnostics.append(
                        Diagnostic(
                            GROUNDING_MISMATCH,
                            kind,
                            unit_id,
                            base + "/section_id",
                            "Review Unit section does not match parent section",
                        )
                    )
                signature = (
                    str(unit.get("unit_type", "")),
                    str(unit.get("content", "")),
                    json.dumps(
                        unit.get("workflow_impacts", []),
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                )
                if signature in signatures:
                    diagnostics.append(
                        Diagnostic(
                            GROUNDING_MISMATCH,
                            kind,
                            unit_id,
                            base,
                            "Review Memory contains an exact duplicate reusable Unit",
                        )
                    )
                signatures.add(signature)
                for note_index, note in enumerate(unit.get("source_notes", [])):
                    if not isinstance(note, dict) or note.get("note_type") != "quote_excerpt":
                        continue
                    try:
                        locator = parse_locator(note.get("locator"))
                    except ValueError:
                        diagnostics.append(
                            Diagnostic(
                                GROUNDING_MISMATCH,
                                kind,
                                unit_id,
                                base + f"/source_notes/{note_index}/locator",
                                "Review Memory quote excerpt requires a valid character locator",
                            )
                        )
                    else:
                        if locator.kind != "char":
                            diagnostics.append(
                                Diagnostic(
                                    GROUNDING_MISMATCH,
                                    kind,
                                    unit_id,
                                    base + f"/source_notes/{note_index}/locator",
                                    "Review Memory quote excerpt requires a character locator",
                                )
                            )
                        elif locator.page != note.get("pdf_page"):
                            diagnostics.append(
                                Diagnostic(
                                    GROUNDING_MISMATCH,
                                    kind,
                                    unit_id,
                                    base + f"/source_notes/{note_index}/locator",
                                    "Review Memory locator page does not match source-note PDF page",
                                )
                            )
        memory_value = record.get("memory_value", {})
        value_status = memory_value.get("status") if isinstance(memory_value, dict) else None
        if value_status == "reusable" and unit_count == 0:
            diagnostics.append(
                Diagnostic(
                    GROUNDING_MISMATCH,
                    kind,
                    _record_id(kind, record),
                    "/memory_value/status",
                    "reusable Review Memory requires at least one reusable Unit",
                )
            )
        elif value_status in {"low_value", "redundant", "outdated", "outside_scope"} and unit_count:
            diagnostics.append(
                Diagnostic(
                    GROUNDING_MISMATCH,
                    kind,
                    _record_id(kind, record),
                    "/memory_value/status",
                    "Review Memory with reusable Units must use reusable memory value status",
                )
            )
    elif kind == "discovery-candidate" and isinstance(record.get("selection_contexts"), list):
        record_id = _record_id(kind, record)
        ordered_fields = (
            ("/publication_types", record.get("publication_types", [])),
            ("/possible_duplicate_result_keys", record.get("possible_duplicate_result_keys", [])),
            ("/target_question_ids", record.get("target_question_ids", [])),
        )
        for path, values in ordered_fields:
            if isinstance(values, list) and values != sorted(values):
                diagnostics.append(
                    Diagnostic(SNAPSHOT_MISMATCH, kind, record_id, path, "discovery candidate array is not deterministically ordered")
                )
        sources = record.get("discovery_sources", [])
        if isinstance(sources, list) and all(isinstance(item, dict) for item in sources):
            ordered_sources = sorted(
                sources,
                key=lambda item: (
                    str(item.get("provider", "")),
                    str(item.get("source", "")),
                    str(item.get("record_id", "")),
                ),
            )
            if sources != ordered_sources:
                diagnostics.append(
                    Diagnostic(SNAPSHOT_MISMATCH, kind, record_id, "/discovery_sources", "discovery sources are not deterministically ordered")
                )
        contexts = record["selection_contexts"]
        if all(isinstance(item, dict) for item in contexts):
            context_ids = [item.get("selection_context_id", "") for item in contexts]
            if context_ids != sorted(context_ids):
                diagnostics.append(
                    Diagnostic(SNAPSHOT_MISMATCH, kind, record_id, "/selection_contexts", "selection contexts are not deterministically ordered")
                )
            for index, context in enumerate(contexts):
                questions = context.get("target_question_ids", [])
                if isinstance(questions, list) and questions != sorted(questions):
                    diagnostics.append(
                        Diagnostic(
                            SNAPSHOT_MISMATCH,
                            kind,
                            record_id,
                            f"/selection_contexts/{index}/target_question_ids",
                            "selection-context question IDs are not deterministically ordered",
                        )
                    )
                if isinstance(context.get("query"), dict) and isinstance(questions, list):
                    identity = {
                        "provider": context.get("provider"),
                        "result_key": record.get("result_key"),
                        "query": context["query"],
                        "target_question_ids": questions,
                    }
                    canonical = (
                        json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
                    ).encode("utf-8")
                    expected = "selection_sha256_" + hashlib.sha256(canonical).hexdigest()
                    if context.get("selection_context_id") != expected:
                        diagnostics.append(
                            Diagnostic(
                                SNAPSHOT_MISMATCH,
                                kind,
                                record_id,
                                f"/selection_contexts/{index}/selection_context_id",
                                "selection context ID does not match its canonical intent",
                            )
                        )
    elif kind == "pipeline-job-state":
        try:
            validate_wait_state(
                str(record.get("status")),
                record.get("wait_reason"),
                record.get("recovery_action"),
            )
        except ResearchKBError as error:
            diagnostics.append(
                Diagnostic(
                    SCHEMA_VALIDATION_FAILED,
                    kind,
                    _record_id(kind, record),
                    error.diagnostic.json_path,
                    error.diagnostic.message,
                )
            )
        if record.get("revision") == 1:
            root_requirements = (
                ("status", "created", "Pipeline Job root status must be created"),
                ("retry_count", 0, "Pipeline Job root retry count must be zero"),
                ("output_refs", [], "Pipeline Job root cannot contain outputs"),
            )
            for field, expected, message in root_requirements:
                if record.get(field) != expected:
                    diagnostics.append(
                        Diagnostic(
                            SCHEMA_VALIDATION_FAILED,
                            kind,
                            _record_id(kind, record),
                            f"/{field}",
                            message,
                        )
                    )
            if record.get("updated_at") != record.get("created_at"):
                diagnostics.append(
                    Diagnostic(
                        SCHEMA_VALIDATION_FAILED,
                        kind,
                        _record_id(kind, record),
                        "/updated_at",
                        "Pipeline Job root timestamps must match",
                    )
                )
    if kind.startswith("step7-") and isinstance(record.get("evidence_base"), list):
        for value in record["evidence_base"]:
            if isinstance(value, str) and value.startswith("queue_"):
                diagnostics.append(Diagnostic(QUEUE_AS_EVIDENCE, kind, _record_id(kind, record), "/evidence_base", "review queue record used as evidence"))
        if record.get("candidate_status") != "rejected" and record.get("rejection_rationale") is not None:
            diagnostics.append(
                Diagnostic(
                    STEP7_BOUNDARY,
                    kind,
                    _record_id(kind, record),
                    "/rejection_rationale",
                    "non-rejected candidate cannot retain a rejection rationale",
                )
            )
    if kind == "primary-semantic-bundle":
        diagnostics.extend(primary_bundle_diagnostics(record))
    elif kind == "review-semantic-bundle":
        diagnostics.extend(review_bundle_diagnostics(record))
    return diagnostics


def _cross_record_diagnostics(entries: list[tuple[str, dict[str, Any]]]) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    workspaces: set[str] = set()
    profiles: set[str] = set()
    papers: set[str] = set()
    evidence: set[str] = set()
    queues: set[str] = set()
    units: set[str] = set()
    historical_evidence: set[str] = set()
    historical_queues: set[str] = set()
    historical_units: set[str] = set()
    questions: set[str] = set()
    candidates: set[str] = set()
    events: set[str] = set()
    process_event_records: dict[str, dict[str, Any]] = {}
    source_roots: set[str] = set()
    unit_paper: dict[str, str] = {}
    unit_evidence: dict[str, set[str]] = {}
    unit_boundaries: dict[str, set[str]] = {}
    unit_status: dict[str, str] = {}
    card_updated_at: dict[str, str] = {}
    evidence_paper: dict[str, str] = {}
    evidence_updated_at: dict[str, str] = {}
    queue_paper: dict[str, str] = {}
    queue_updated_at: dict[str, str] = {}
    historical_unit_paper: dict[str, str] = {}
    historical_unit_evidence: dict[str, set[str]] = {}
    historical_unit_boundaries: dict[str, set[str]] = {}
    historical_unit_status: dict[str, str] = {}
    historical_evidence_paper: dict[str, str] = {}
    historical_queue_paper: dict[str, str] = {}
    paper_fingerprint: dict[str, dict[str, Any]] = {}
    registry_papers: dict[str, dict[str, Any]] = {}
    profile_sections: dict[str, list[str]] = {}
    question_records: dict[str, dict[str, Any]] = {}
    candidate_records: dict[str, dict[str, Any]] = {}
    discovery_result_keys: dict[str, str] = {}
    discovery_context_ids: dict[str, str] = {}
    defined: dict[str, list[str]] = defaultdict(list)
    paper_cards: dict[str, int] = defaultdict(int)
    review_memories: dict[str, int] = defaultdict(int)
    jobs: set[str] = set()
    source_assets: set[str] = set()
    pipeline_states: list[dict[str, Any]] = []
    guardian_reports: list[dict[str, Any]] = []
    guardian_dispositions: list[dict[str, Any]] = []
    source_asset_states: list[dict[str, Any]] = []
    source_asset_state_records: dict[str, dict[str, Any]] = {}
    identity_corrections: list[dict[str, Any]] = []
    adequacy_profiles: dict[str, dict[str, Any]] = {}
    parse_run_papers: dict[str, set[str]] = defaultdict(set)
    parse_run_pages: dict[str, list[dict[str, Any]]] = defaultdict(list)
    agent_task_states: list[dict[str, Any]] = []
    agent_tasks: set[str] = set()

    page_index, provenance_failures = index_active_pages(
        record for kind, record in entries if kind == "parsed-page"
    )
    diagnostics.extend(
        Diagnostic(
            failure.code,
            failure.record_kind,
            failure.record_id,
            failure.json_path,
            failure.message,
        )
        for failure in provenance_failures
    )
    active_review_parses, review_parse_failures = build_active_parse_index(
        record for kind, record in entries if kind == "parsed-page"
    )
    diagnostics.extend(
        Diagnostic(
            failure.code,
            failure.record_kind,
            failure.record_id,
            failure.json_path,
            failure.message,
        )
        for failure in review_parse_failures
    )
    for kind, record in entries:
        if kind != "evidence":
            continue
        diagnostics.extend(
            Diagnostic(
                failure.code,
                failure.record_kind,
                failure.record_id,
                failure.json_path,
                failure.message,
            )
            for failure in validate_evidence_against_pages(record, page_index)
        )
    for kind, record in entries:
        if kind != "review-memory":
            continue
        if review_memory_freshness(record, active_review_parses) != "stale_parse":
            diagnostics.extend(
                Diagnostic(
                    failure.code,
                    failure.record_kind,
                    failure.record_id,
                    failure.json_path,
                    failure.message,
                )
                for failure in validate_review_memory_provenance(record, active_review_parses)
            )

    for kind, record in entries:
        if kind == "workspace":
            workspace_id = record.get("workspace", {}).get("id", "")
            workspaces.add(workspace_id)
            defined["workspace"].append(workspace_id)
            source_roots.update(
                item.get("root_id", "") for item in record.get("workspace", {}).get("source_roots", [])
            )
        elif kind == "domain-profile":
            profile_id = record.get("domain_profile", {}).get("id", "")
            profiles.add(profile_id)
            profile_sections[profile_id] = [
                item.get("section_id", "") for item in record.get("paper_card_sections", [])
            ]
        elif kind == "registry-paper":
            paper_id = record.get("paper_id", "")
            papers.add(paper_id)
            defined["paper"].append(paper_id)
            paper_fingerprint[paper_id] = record.get("source_fingerprint", {})
            registry_papers[paper_id] = record
        elif kind == "source-asset-state":
            source_asset_states.append(record)
            source_assets.add(record.get("source_asset_id", ""))
            source_asset_state_id = record.get("source_asset_state_id", "")
            source_asset_state_records[source_asset_state_id] = record
            defined["sourceassetstate"].append(source_asset_state_id)
        elif kind == "registry-identity-correction":
            identity_corrections.append(record)
            defined["identitycorr"].append(record.get("correction_id", ""))
        elif kind == "parsed-page":
            parse_run_id = record.get("parse_run_id", "")
            parse_run_papers[parse_run_id].add(record.get("paper_id", ""))
            parse_run_pages[parse_run_id].append(record)
        elif kind == "source-adequacy-profile":
            profile_id = record.get("profile_id", "")
            adequacy_profiles[profile_id] = record
            defined["adequacy"].append(profile_id)
        elif kind == "paper-card":
            paper_id = record.get("paper_id", "")
            paper_cards[paper_id] += 1
            card_updated_at[paper_id] = record.get("updated_at", "")
            for section in record.get("sections", []):
                for unit in section.get("units", []):
                    unit_id = unit.get("unit_id", "")
                    units.add(unit_id)
                    defined["unit"].append(unit_id)
                    unit_paper[unit_id] = paper_id
                    unit_evidence[unit_id] = set(unit.get("evidence_ids", []))
                    unit_boundaries[unit_id] = set(unit.get("boundary_refs", []))
                    unit_status[unit_id] = unit.get("grounding_status", "")
        elif kind == "review-memory":
            review_memory_id = record.get("review_memory_id", "")
            paper_id = record.get("paper_id", "")
            review_memories[paper_id] += 1
            defined["reviewmem"].append(review_memory_id)
            for section in record.get("sections", []):
                for unit in section.get("units", []):
                    defined["reviewunit"].append(unit.get("review_unit_id", ""))
        elif kind == "evidence":
            evidence_id = record.get("evidence_id", "")
            evidence.add(evidence_id)
            defined["evidence"].append(evidence_id)
            evidence_paper[evidence_id] = record.get("paper_id", "")
            evidence_updated_at[evidence_id] = record.get("updated_at", "")
        elif kind == "review-queue":
            queue_id = record.get("queue_id", "")
            queues.add(queue_id)
            defined["queue"].append(queue_id)
            queue_paper[queue_id] = record.get("paper_id", "")
            queue_updated_at[queue_id] = record.get("updated_at", "")
        elif kind == "question-mapping":
            question_id = record.get("question_id", "")
            questions.add(question_id)
            question_records[question_id] = record
            defined["question"].append(question_id)
            defined["qlink"].extend(link.get("question_link_id", "") for link in record.get("paper_links", []))
        elif kind.startswith("step7-"):
            namespace = {
                "step7-synthesis": "synthesis",
                "step7-review-angle": "angle",
                "step7-insight": "insight",
                "step7-cross-view": "crossview",
            }.get(kind)
            if namespace:
                candidate_id = record.get("candidate_id", "")
                candidates.add(candidate_id)
                candidate_records[candidate_id] = record
                defined[namespace].append(candidate_id)
        elif kind == "discovery-candidate":
            candidate_id = record.get("candidate_id", "")
            defined["discovery"].append(candidate_id)
            result_key = record.get("result_key", "")
            if result_key in discovery_result_keys:
                diagnostics.append(
                    Diagnostic(
                        DUPLICATE_ID,
                        kind,
                        candidate_id,
                        "/result_key",
                        "discovery result key is already represented by another candidate",
                    )
                )
            elif result_key:
                discovery_result_keys[result_key] = candidate_id
            for context in record.get("selection_contexts", []):
                context_id = context.get("selection_context_id", "")
                if context_id in discovery_context_ids:
                    diagnostics.append(
                        Diagnostic(
                            DUPLICATE_ID,
                            kind,
                            candidate_id,
                            "/selection_contexts",
                            "selection context ID is already represented",
                        )
                    )
                elif context_id:
                    discovery_context_ids[context_id] = candidate_id
        elif kind == "process-event":
            event_id = record.get("event_id", "")
            events.add(event_id)
            process_event_records[event_id] = record
            defined["event"].append(event_id)
        elif kind == "pipeline-job-state":
            pipeline_states.append(record)
            jobs.add(record.get("job_id", ""))
            defined["jobstate"].append(record.get("state_id", ""))
        elif kind == "agent-task-state":
            agent_task_states.append(record)
            agent_tasks.add(record.get("task_id", ""))
            defined["taskstate"].append(record.get("state_id", ""))
        elif kind == "primary-semantic-bundle":
            defined["primaryrev"].extend(
                revision.get("revision_id", "")
                for revision in record.get("revisions", [])
            )
            active_revision_id = record.get("active_revision_id")
            for revision in record.get("revisions", []):
                if revision.get("revision_id") == active_revision_id:
                    continue
                paper_id = record.get("paper_id", "")
                for section in revision.get("paper_card", {}).get("sections", []):
                    for unit in section.get("units", []):
                        unit_id = unit.get("unit_id", "")
                        historical_units.add(unit_id)
                        defined["unit"].append(unit_id)
                        historical_unit_paper[unit_id] = paper_id
                        historical_unit_evidence[unit_id] = set(unit.get("evidence_ids", []))
                        historical_unit_boundaries[unit_id] = set(unit.get("boundary_refs", []))
                        historical_unit_status[unit_id] = unit.get("grounding_status", "")
                for item in revision.get("evidence", []):
                    evidence_id = item.get("evidence_id", "")
                    historical_evidence.add(evidence_id)
                    defined["evidence"].append(evidence_id)
                    historical_evidence_paper[evidence_id] = paper_id
                for item in revision.get("review_queue", []):
                    queue_id = item.get("queue_id", "")
                    historical_queues.add(queue_id)
                    defined["queue"].append(queue_id)
                    historical_queue_paper[queue_id] = paper_id
        elif kind == "review-semantic-bundle":
            defined["reviewrev"].extend(
                revision.get("revision_id", "")
                for revision in record.get("revisions", [])
            )
            active_revision_id = record.get("active_revision_id")
            for revision in record.get("revisions", []):
                if revision.get("revision_id") == active_revision_id:
                    continue
                memory = revision.get("review_memory", {})
                defined["reviewmem"].append(memory.get("review_memory_id", ""))
                for section in memory.get("sections", []):
                    defined["reviewunit"].extend(
                        unit.get("review_unit_id", "")
                        for unit in section.get("units", [])
                    )
        elif kind == "guardian-report":
            guardian_reports.append(record)
            defined["guardian"].append(record.get("guardian_report_id", ""))
        elif kind == "guardian-finding-disposition":
            guardian_dispositions.append(record)
            defined["gdisp"].append(record.get("disposition_id", ""))

    defined["job"].extend(sorted(filter(None, jobs)))
    defined["task"].extend(sorted(filter(None, agent_tasks)))
    defined["sourceasset"].extend(sorted(filter(None, source_assets)))
    referencable_units = units | historical_units
    referencable_evidence = evidence | historical_evidence
    referencable_queues = queues | historical_queues
    referencable_unit_paper = {**historical_unit_paper, **unit_paper}
    referencable_unit_evidence = {**historical_unit_evidence, **unit_evidence}
    referencable_unit_boundaries = {**historical_unit_boundaries, **unit_boundaries}
    referencable_unit_status = {**historical_unit_status, **unit_status}
    referencable_evidence_paper = {**historical_evidence_paper, **evidence_paper}
    referencable_queue_paper = {**historical_queue_paper, **queue_paper}
    pipeline_diagnostics = pipeline_job_chain_diagnostics(pipeline_states)
    agent_task_diagnostics = agent_task_chain_diagnostics(agent_task_states)
    source_diagnostics = source_asset_chain_diagnostics(source_asset_states)
    diagnostics.extend(pipeline_diagnostics)
    diagnostics.extend(agent_task_diagnostics)
    diagnostics.extend(source_diagnostics)
    if not pipeline_diagnostics and not source_diagnostics:
        job_heads = {
            item["job_id"]: item for item in current_pipeline_states(pipeline_states)
        }
        source_asset_roots = {
            item["source_asset_id"]: item
            for item in source_asset_states
            if item.get("revision") == 1
        }
        successful_terminal_statuses = TERMINAL_STATUSES - {"failed", "cancelled"}
        source_heads = current_source_asset_heads(source_asset_states)
        for head in source_heads:
            if head.get("paper_id") is not None:
                continue
            root = source_asset_roots.get(head["source_asset_id"])
            owning_job = None if root is None else job_heads.get(root.get("job_id"))
            if owning_job is not None and owning_job.get("status") in successful_terminal_statuses:
                diagnostics.append(
                    Diagnostic(
                        INCOMPLETE_TRANSACTION,
                        "source-asset-state",
                        head["source_asset_state_id"],
                        "/paper_id",
                        "successfully completed source intake still has an unassociated Source Asset",
                    )
                )
        for head in source_heads:
            paper_id = head.get("paper_id")
            registered_fingerprint = paper_fingerprint.get(paper_id)
            active_state = next(
                (
                    state
                    for state in reversed(
                        sorted(
                            (
                                item
                                for item in source_asset_states
                                if item["source_asset_id"] == head["source_asset_id"]
                            ),
                            key=lambda item: item["revision"],
                        )
                    )
                    if state["manifestation_status"] == "active"
                ),
                None,
            )
            if (
                head.get("asset_role") == "main_pdf"
                and registered_fingerprint is not None
                and active_state is not None
                and active_state.get("source_fingerprint") != registered_fingerprint
            ):
                diagnostics.append(
                    Diagnostic(
                        GROUNDING_MISMATCH,
                        "source-asset-state",
                        head["source_asset_state_id"],
                        "/source_fingerprint",
                        "main PDF manifestation does not match the Registry paper fingerprint",
                    )
                )
    diagnostics.extend(
        identity_correction_diagnostics(
            identity_corrections,
            [record for kind, record in entries if kind == "registry-paper"],
        )
    )
    diagnostics.extend(
        guardian_disposition_diagnostics(guardian_dispositions, guardian_reports)
    )

    for namespace, values in defined.items():
        seen: set[str] = set()
        for value in filter(None, values):
            if value in seen:
                diagnostics.append(Diagnostic(DUPLICATE_ID, namespace, value, "", "duplicate canonical object ID"))
            seen.add(value)

    for paper_id, count in paper_cards.items():
        if paper_id and count > 1:
            diagnostics.append(
                Diagnostic(DUPLICATE_PAPER_CARD, "paper-card", paper_id, "/paper_id", "more than one Paper Card Core for paper")
            )
    for paper_id, count in review_memories.items():
        if paper_id and count > 1:
            diagnostics.append(
                Diagnostic(
                    DUPLICATE_REVIEW_MEMORY,
                    "review-memory",
                    paper_id,
                    "/paper_id",
                    "more than one Review Memory exists for paper",
                )
            )

    all_object_ids = {value for values in defined.values() for value in values if value}
    primary_route_papers = set(paper_cards) | set(evidence_paper.values())

    for kind, record in entries:
        record_id = _record_id(kind, record)
        if kind in {
            "parsed-page",
            "paper-card",
            "evidence",
            "review-queue",
            "review-memory",
            "source-adequacy-profile",
        }:
            _require_ref(diagnostics, kind, record_id, "/paper_id", record.get("paper_id"), papers, "paper")
        if kind == "workspace":
            roots = [item.get("root_id", "") for item in record.get("workspace", {}).get("source_roots", [])]
            if len(roots) != len(set(roots)):
                diagnostics.append(Diagnostic(DUPLICATE_ID, kind, record.get("workspace", {}).get("id"), "/workspace/source_roots", "duplicate source root ID"))
        elif kind == "domain-profile":
            section_ids = [item.get("section_id", "") for item in record.get("paper_card_sections", [])]
            if len(section_ids) != len(set(section_ids)):
                diagnostics.append(Diagnostic(DUPLICATE_ID, kind, record.get("domain_profile", {}).get("id"), "/paper_card_sections", "duplicate section ID"))
        elif kind == "registry-paper":
            root_id = record.get("source_ref", {}).get("root_id")
            if isinstance(root_id, str) and root_id and root_id not in source_roots:
                diagnostics.append(
                    Diagnostic(PATH_ESCAPE, kind, record_id, "/source_ref/root_id", "source_ref root_id is not declared by the workspace")
                )
            for value in record.get("duplicate_candidate_ids", []):
                _require_ref(diagnostics, kind, record_id, "/duplicate_candidate_ids", value, papers, "paper")
        elif kind == "source-asset-state":
            _require_ref(diagnostics, kind, record_id, "/workspace_id", record.get("workspace_id"), workspaces, "workspace")
            if record.get("paper_id") is not None:
                _require_ref(diagnostics, kind, record_id, "/paper_id", record.get("paper_id"), papers, "paper")
            root_id = record.get("source_ref", {}).get("root_id")
            if isinstance(root_id, str) and root_id and root_id not in source_roots:
                diagnostics.append(
                    Diagnostic(PATH_ESCAPE, kind, record_id, "/source_ref/root_id", "source_ref root_id is not declared by the workspace")
                )
            _require_ref(diagnostics, kind, record_id, "/job_id", record.get("job_id"), jobs, "Pipeline Job")
        elif kind == "registry-identity-correction":
            _require_ref(diagnostics, kind, record_id, "/workspace_id", record.get("workspace_id"), workspaces, "workspace")
            _require_ref(diagnostics, kind, record_id, "/job_id", record.get("job_id"), jobs, "Pipeline Job")
        elif kind == "source-adequacy-profile":
            _require_ref(
                diagnostics,
                kind,
                record_id,
                "/workspace_id",
                record.get("workspace_id"),
                workspaces,
                "workspace",
            )
            _require_ref(
                diagnostics,
                kind,
                record_id,
                "/job_id",
                record.get("job_id"),
                jobs,
                "Pipeline Job",
            )
            basis = record.get("basis_profile")
            if isinstance(basis, dict):
                basis_id = basis.get("profile_id")
                _require_ref(
                    diagnostics,
                    kind,
                    record_id,
                    "/basis_profile/profile_id",
                    basis_id,
                    set(adequacy_profiles),
                    "Source Adequacy profile",
                )
                if basis_id == record_id:
                    diagnostics.append(
                        Diagnostic(
                            GROUNDING_MISMATCH,
                            kind,
                            record_id,
                            "/basis_profile/profile_id",
                            "Source Adequacy profile cannot reference itself as its basis",
                        )
                    )
                predecessor = adequacy_profiles.get(basis_id)
                if predecessor is not None:
                    if basis.get("profile_digest") != canonical_digest(predecessor):
                        diagnostics.append(
                            Diagnostic(
                                SNAPSHOT_MISMATCH,
                                kind,
                                record_id,
                                "/basis_profile/profile_digest",
                                "basis profile digest does not match the referenced profile",
                            )
                        )
                    for field in (
                        "workspace_id",
                        "paper_id",
                        "requested_operation",
                        "source_snapshots",
                        "parse_snapshot",
                    ):
                        if record.get(field) != predecessor.get(field):
                            diagnostics.append(
                                Diagnostic(
                                    GROUNDING_MISMATCH,
                                    kind,
                                    record_id,
                                    f"/{field}",
                                    f"successor {field} does not match its basis profile",
                                )
                            )
            source_state_ids = set(source_asset_state_records)
            for index, snapshot in enumerate(record.get("source_snapshots", [])):
                if not isinstance(snapshot, dict):
                    continue
                base = f"/source_snapshots/{index}"
                root_id = snapshot.get("source_ref", {}).get("root_id")
                if isinstance(root_id, str) and root_id and root_id not in source_roots:
                    diagnostics.append(
                        Diagnostic(
                            PATH_ESCAPE,
                            kind,
                            record_id,
                            base + "/source_ref/root_id",
                            "source snapshot root_id is not declared by the workspace",
                        )
                    )
                source_asset_id = snapshot.get("source_asset_id")
                source_asset_state_id = snapshot.get("source_asset_state_id")
                if source_asset_id is None:
                    paper = registry_papers.get(record.get("paper_id"))
                    if paper is None:
                        continue
                    expected_manifestation = f"sha256:{paper.get('source_fingerprint', {}).get('value', '')}"
                    expected_fields = {
                        "source_asset_state_id": None,
                        "role": "main_pdf",
                        "source_ref": paper.get("source_ref"),
                        "manifestation_id": expected_manifestation,
                    }
                    for field, expected in expected_fields.items():
                        if snapshot.get(field) != expected:
                            diagnostics.append(
                                Diagnostic(
                                    SNAPSHOT_MISMATCH,
                                    kind,
                                    record_id,
                                    base + f"/{field}",
                                    f"implicit main source snapshot {field} does not match the Registry paper",
                                )
                            )
                    continue
                _require_ref(
                    diagnostics,
                    kind,
                    record_id,
                    base + "/source_asset_id",
                    source_asset_id,
                    source_assets,
                    "Source Asset",
                )
                _require_ref(
                    diagnostics,
                    kind,
                    record_id,
                    base + "/source_asset_state_id",
                    source_asset_state_id,
                    source_state_ids,
                    "Source Asset state",
                )
                source_state = source_asset_state_records.get(source_asset_state_id)
                if source_state is None:
                    continue
                expected_fields = {
                    "source_asset_id": "source_asset_id",
                    "paper_id": "paper_id",
                    "role": "asset_role",
                    "source_ref": "source_ref",
                    "manifestation_id": "manifestation_id",
                    "availability": "availability",
                }
                for snapshot_field, state_field in expected_fields.items():
                    expected = record.get("paper_id") if snapshot_field == "paper_id" else snapshot.get(snapshot_field)
                    if expected != source_state.get(state_field):
                        diagnostics.append(
                            Diagnostic(
                                SNAPSHOT_MISMATCH,
                                kind,
                                record_id,
                                base + f"/{snapshot_field}",
                                f"source snapshot {snapshot_field} does not match the referenced Source Asset state",
                            )
                        )
            parse_ref = record.get("parse_snapshot", {}).get("active_parse_ref")
            _require_ref(
                diagnostics,
                kind,
                record_id,
                "/parse_snapshot/active_parse_ref",
                parse_ref,
                events,
                "parse event",
            )
            parse_event = process_event_records.get(parse_ref)
            if parse_event is not None and (
                parse_event.get("operation") != "parse_run"
                or parse_event.get("result") != "success"
            ):
                diagnostics.append(
                    Diagnostic(
                        GROUNDING_MISMATCH,
                        kind,
                        record_id,
                        "/parse_snapshot/active_parse_ref",
                        "parse snapshot must reference a successful parse_run event",
                    )
                )
            referenced_pages = parse_run_pages.get(parse_ref, [])
            if referenced_pages:
                if record.get("paper_id") not in parse_run_papers.get(parse_ref, set()):
                    diagnostics.append(
                        Diagnostic(
                            GROUNDING_MISMATCH,
                            kind,
                            record_id,
                            "/parse_snapshot/active_parse_ref",
                            "active parse belongs to another paper",
                        )
                    )
                else:
                    paper_pages = [
                        item for item in referenced_pages if item.get("paper_id") == record.get("paper_id")
                    ]
                    parse_snapshot = record.get("parse_snapshot", {})
                    if parse_snapshot.get("page_count") != len(paper_pages):
                        diagnostics.append(
                            Diagnostic(
                                SNAPSHOT_MISMATCH,
                                kind,
                                record_id,
                                "/parse_snapshot/page_count",
                                "parse snapshot page count does not match the referenced parsed pages",
                            )
                        )
                    parser_identity = parse_snapshot.get("parser_identity", {})
                    actual_parsers = {
                        (item.get("parser", {}).get("adapter"), item.get("parser", {}).get("version"))
                        for item in paper_pages
                    }
                    expected_parser = (
                        parser_identity.get("adapter_id"),
                        parser_identity.get("version"),
                    )
                    if actual_parsers != {expected_parser}:
                        diagnostics.append(
                            Diagnostic(
                                SNAPSHOT_MISMATCH,
                                kind,
                                record_id,
                                "/parse_snapshot/parser_identity",
                                "parse snapshot parser identity does not match the referenced parsed pages",
                            )
                        )
            parser_identity = record.get("parse_snapshot", {}).get("parser_identity", {})
            expected_profile_digest = canonical_digest(
                parser_profile_descriptor(
                    str(parser_identity.get("adapter_id", "")),
                    str(parser_identity.get("version", "")),
                )
            )
            if parser_identity.get("profile_digest") != expected_profile_digest:
                diagnostics.append(
                    Diagnostic(
                        SNAPSHOT_MISMATCH,
                        kind,
                        record_id,
                        "/parse_snapshot/parser_identity/profile_digest",
                        "parser profile digest does not match the registered adapter descriptor",
                    )
                )
            for observation_index, observation in enumerate(record.get("machine_observations", [])):
                if not isinstance(observation, dict) or not (
                    observation.get("hard_failure") and observation.get("status") == "fail"
                ):
                    continue
                for capability in observation.get("affected_capabilities", []):
                    if record.get("capabilities", {}).get(capability, {}).get("status") == "yes":
                        diagnostics.append(
                            Diagnostic(
                                GROUNDING_MISMATCH,
                                kind,
                                record_id,
                                f"/capabilities/{capability}/status",
                                f"hard machine failure at observation {observation_index} cannot produce an adequate capability",
                            )
                        )
            if record.get("agent_assessment") is not None:
                diagnostics.append(
                    Diagnostic(
                        INVALID_AUTHORITY,
                        kind,
                        record_id,
                        "/agent_assessment",
                        "P3 Source Adequacy profiles cannot contain an Agent assessment",
                    )
                )
            user_decision = record.get("user_decision")
            if (user_decision is None) != (record.get("assessed_by") == "cli"):
                diagnostics.append(
                    Diagnostic(
                        INVALID_AUTHORITY,
                        kind,
                        record_id,
                        "/assessed_by",
                        "assessed_by must be user exactly when a user decision is present",
                    )
                )
            if user_decision is not None and not isinstance(basis, dict):
                diagnostics.append(
                    Diagnostic(
                        GROUNDING_MISMATCH,
                        kind,
                        record_id,
                        "/basis_profile",
                        "a user decision requires a predecessor Source Adequacy profile",
                    )
                )
        elif kind == "parsed-page":
            _require_ref(diagnostics, kind, record_id, "/parse_run_id", record.get("parse_run_id"), events, "process event")
        elif kind == "evidence":
            paper_id = record.get("paper_id", "")
            expected_fingerprint = paper_fingerprint.get(paper_id)
            if expected_fingerprint is not None and record.get("source_fingerprint") != expected_fingerprint:
                diagnostics.append(
                    Diagnostic(GROUNDING_MISMATCH, kind, record_id, "/source_fingerprint", "evidence fingerprint does not match the registered paper source")
                )
        elif kind == "paper-card":
            profile_id = record.get("domain_profile_id")
            _require_ref(diagnostics, kind, record_id, "/domain_profile_id", profile_id, profiles, "domain profile")
            paper_id = record.get("paper_id", "")
            actual_sections = [section.get("section_id", "") for section in record.get("sections", [])]
            if profile_id in profile_sections and actual_sections != profile_sections[profile_id]:
                diagnostics.append(
                    Diagnostic(GROUNDING_MISMATCH, kind, record_id, "/sections", "Paper Card sections do not match the linked domain profile order and membership")
                )
            for section_index, section in enumerate(record.get("sections", [])):
                section_id = section.get("section_id")
                for unit_index, unit in enumerate(section.get("units", [])):
                    base = f"/sections/{section_index}/units/{unit_index}"
                    if unit.get("section_id") != section_id:
                        diagnostics.append(Diagnostic(GROUNDING_MISMATCH, kind, unit.get("unit_id"), base + "/section_id", "Card Unit section does not match parent section"))
                    status = unit.get("grounding_status")
                    evidence_ids = unit.get("evidence_ids", [])
                    if status in {"grounded", "revised"} and not evidence_ids:
                        diagnostics.append(Diagnostic(GROUNDING_MISMATCH, kind, unit.get("unit_id"), base + "/evidence_ids", "grounded/revised unit requires evidence"))
                    if status in NON_SUPPORTING_UNIT_STATES and evidence_ids:
                        diagnostics.append(Diagnostic(GROUNDING_MISMATCH, kind, unit.get("unit_id"), base + "/evidence_ids", "non-supporting unit cannot expose supporting evidence"))
                    for value in evidence_ids:
                        _require_ref(diagnostics, kind, unit.get("unit_id"), base + "/evidence_ids", value, evidence, "evidence")
                        if value in evidence_paper and evidence_paper[value] != paper_id:
                            diagnostics.append(Diagnostic(GROUNDING_MISMATCH, kind, unit.get("unit_id"), base + "/evidence_ids", "Card Unit evidence belongs to another paper"))
                    for value in unit.get("boundary_refs", []):
                        _require_ref(diagnostics, kind, unit.get("unit_id"), base + "/boundary_refs", value, queues, "review queue")
                        if value in queue_paper and queue_paper[value] != paper_id:
                            diagnostics.append(Diagnostic(GROUNDING_MISMATCH, kind, unit.get("unit_id"), base + "/boundary_refs", "Card Unit boundary belongs to another paper"))
        elif kind == "review-memory":
            paper_id = record.get("paper_id", "")
            expected_fingerprint = paper_fingerprint.get(paper_id)
            if expected_fingerprint is not None and record.get("source_fingerprint") != expected_fingerprint:
                diagnostics.append(
                    Diagnostic(
                        GROUNDING_MISMATCH,
                        kind,
                        record_id,
                        "/source_fingerprint",
                        "Review Memory fingerprint does not match the registered paper source",
                    )
                )
            snapshot = record.get("parse_snapshot")
            if isinstance(snapshot, dict):
                _require_ref(
                    diagnostics,
                    kind,
                    record_id,
                    "/parse_snapshot/parse_run_id",
                    snapshot.get("parse_run_id"),
                    events,
                    "process event",
                )
            if paper_id in primary_route_papers:
                diagnostics.append(
                    Diagnostic(
                        GROUNDING_MISMATCH,
                        kind,
                        record_id,
                        "/paper_id",
                        "primary research and Review Memory routes are mutually exclusive",
                    )
                )
        elif kind == "question-mapping":
            _require_ref(diagnostics, kind, record_id, "/domain_profile_id", record.get("domain_profile_id"), profiles, "domain profile")
            linked_papers = [link.get("paper_id") for link in record.get("paper_links", [])]
            if len(linked_papers) != len(set(linked_papers)):
                diagnostics.append(
                    Diagnostic(DUPLICATE_ID, kind, record_id, "/paper_links", "question contains duplicate paper links")
                )
            for link_index, link in enumerate(record.get("paper_links", [])):
                base = f"/paper_links/{link_index}"
                paper_id = link.get("paper_id")
                _require_ref(diagnostics, kind, record_id, base + "/paper_id", paper_id, papers, "paper")
                mapping_updated_at = record.get("updated_at", "")
                upstream_is_newer = _timestamp_is_after(
                    card_updated_at.get(paper_id, ""),
                    mapping_updated_at,
                ) or any(
                    _timestamp_is_after(updated_at, mapping_updated_at)
                    for updated_at in (
                        *(evidence_updated_at.get(value, "") for value in link.get("evidence_ids", [])),
                        *(queue_updated_at.get(value, "") for value in link.get("boundary_refs", [])),
                    )
                ) or any(
                    value not in units
                    for value in link.get("selected_card_unit_ids", [])
                ) or any(
                    value not in evidence
                    for value in link.get("evidence_ids", [])
                ) or any(
                    value not in queues
                    for value in link.get("boundary_refs", [])
                )
                expanded_evidence: set[str] = set()
                required_boundaries: set[str] = set()
                selected_needs_resolution = False
                for value in link.get("selected_card_unit_ids", []):
                    _require_ref(diagnostics, kind, record_id, base + "/selected_card_unit_ids", value, referencable_units, "Card Unit")
                    if value in referencable_unit_paper and referencable_unit_paper[value] != paper_id:
                        diagnostics.append(Diagnostic(GROUNDING_MISMATCH, kind, record_id, base + "/selected_card_unit_ids", "selected Card Unit belongs to another paper"))
                    expanded_evidence.update(referencable_unit_evidence.get(value, set()))
                    required_boundaries.update(referencable_unit_boundaries.get(value, set()))
                    selected_needs_resolution = selected_needs_resolution or referencable_unit_status.get(value) == "needs_resolution"
                for value in link.get("evidence_ids", []):
                    _require_ref(diagnostics, kind, record_id, base + "/evidence_ids", value, referencable_evidence, "evidence")
                    if value in referencable_evidence_paper and referencable_evidence_paper[value] != paper_id:
                        diagnostics.append(Diagnostic(GROUNDING_MISMATCH, kind, record_id, base + "/evidence_ids", "question-link evidence belongs to another paper"))
                for value in link.get("boundary_refs", []):
                    _require_ref(diagnostics, kind, record_id, base + "/boundary_refs", value, referencable_queues, "review queue")
                    if value in referencable_queue_paper and referencable_queue_paper[value] != paper_id:
                        diagnostics.append(Diagnostic(GROUNDING_MISMATCH, kind, record_id, base + "/boundary_refs", "question-link boundary belongs to another paper"))
                if not upstream_is_newer and expanded_evidence != set(link.get("evidence_ids", [])):
                    diagnostics.append(
                        Diagnostic(
                            SNAPSHOT_MISMATCH,
                            kind,
                            record_id,
                            base + "/evidence_ids",
                            "question-link evidence does not equal selected Card Unit evidence expansion",
                        )
                    )
                if not upstream_is_newer and not required_boundaries.issubset(set(link.get("boundary_refs", []))):
                    diagnostics.append(
                        Diagnostic(
                            SNAPSHOT_MISMATCH,
                            kind,
                            record_id,
                            base + "/boundary_refs",
                            "question-link omits a selected Card Unit boundary",
                        )
                    )
                if (
                    not upstream_is_newer
                    and selected_needs_resolution
                    and record.get("mapping_status") != "needs_resolution"
                ):
                    diagnostics.append(
                        Diagnostic(
                            GROUNDING_MISMATCH,
                            kind,
                            record_id,
                            "/mapping_status",
                            "a selected needs-resolution Card Unit requires needs_resolution mapping status",
                        )
                    )
        elif kind == "discovery-candidate":
            _require_ref(
                diagnostics,
                kind,
                record_id,
                "/workspace_id",
                record.get("workspace_id"),
                workspaces,
                "workspace",
            )
            _require_ref(
                diagnostics,
                kind,
                record_id,
                "/domain_profile_id",
                record.get("domain_profile_id"),
                profiles,
                "domain profile",
            )
            context_question_ids: set[str] = set()
            for context_index, context in enumerate(record.get("selection_contexts", [])):
                for value in context.get("target_question_ids", []):
                    context_question_ids.add(value)
                    _require_ref(
                        diagnostics,
                        kind,
                        record_id,
                        f"/selection_contexts/{context_index}/target_question_ids",
                        value,
                        questions,
                        "question",
                    )
            target_question_ids = set(record.get("target_question_ids", []))
            for value in target_question_ids:
                _require_ref(
                    diagnostics,
                    kind,
                    record_id,
                    "/target_question_ids",
                    value,
                    questions,
                    "question",
                )
            if context_question_ids != target_question_ids:
                diagnostics.append(
                    Diagnostic(
                        SNAPSHOT_MISMATCH,
                        kind,
                        record_id,
                        "/target_question_ids",
                        "target question IDs do not equal the union of selection contexts",
                    )
                )
        elif kind.startswith("step7-"):
            _require_ref(diagnostics, kind, record_id, "/question_id", record.get("question_id"), questions, "question")
            base_units: list[str] = []
            base_papers: set[str] = set()
            expanded_evidence: set[str] = set()
            expanded_boundaries: set[str] = set()
            seen_papers: set[str] = set()
            seen_units: set[str] = set()
            candidate_updated_at = record.get("updated_at", "")
            for base_index, item in enumerate(record.get("paper_card_base", [])):
                paper_id = item.get("paper_id")
                if isinstance(paper_id, str):
                    base_papers.add(paper_id)
                    if paper_id in seen_papers:
                        diagnostics.append(Diagnostic(DUPLICATE_ID, kind, record_id, "/paper_card_base", "duplicate paper in paper_card_base"))
                    seen_papers.add(paper_id)
                _require_ref(diagnostics, kind, record_id, f"/paper_card_base/{base_index}/paper_id", paper_id, papers, "paper")
                for value in item.get("card_unit_ids", []):
                    base_units.append(value)
                    if value in seen_units:
                        diagnostics.append(Diagnostic(DUPLICATE_ID, kind, record_id, f"/paper_card_base/{base_index}/card_unit_ids", "Card Unit appears more than once"))
                    seen_units.add(value)
                    _require_ref(diagnostics, kind, record_id, f"/paper_card_base/{base_index}/card_unit_ids", value, referencable_units, "Card Unit")
                    if value in referencable_unit_paper and referencable_unit_paper[value] != paper_id:
                        diagnostics.append(Diagnostic(STEP7_BOUNDARY, kind, record_id, f"/paper_card_base/{base_index}/card_unit_ids", "Card Unit belongs to another paper"))
                    if (
                        value in referencable_unit_status
                        and referencable_unit_status[value] not in {"grounded", "revised"}
                        and not _timestamp_is_after(card_updated_at.get(paper_id, ""), candidate_updated_at)
                        and value in units
                    ):
                        diagnostics.append(Diagnostic(STEP7_BOUNDARY, kind, record_id, f"/paper_card_base/{base_index}/card_unit_ids", "non-factual Card Unit cannot enter Step 7 support"))
                    expanded_evidence.update(referencable_unit_evidence.get(value, set()))
                    expanded_boundaries.update(referencable_unit_boundaries.get(value, set()))
            for value in record.get("evidence_base", []):
                if value in referencable_queues:
                    diagnostics.append(Diagnostic(QUEUE_AS_EVIDENCE, kind, record_id, "/evidence_base", "review queue record used as evidence"))
                _require_ref(diagnostics, kind, record_id, "/evidence_base", value, referencable_evidence, "evidence")
                if value in referencable_evidence_paper and referencable_evidence_paper[value] not in base_papers:
                    diagnostics.append(Diagnostic(STEP7_BOUNDARY, kind, record_id, "/evidence_base", "evidence belongs to a paper outside paper_card_base"))
            for value in record.get("review_queue_refs", []):
                _require_ref(diagnostics, kind, record_id, "/review_queue_refs", value, referencable_queues, "review queue")
                if value in referencable_queue_paper and referencable_queue_paper[value] not in base_papers:
                    diagnostics.append(Diagnostic(STEP7_BOUNDARY, kind, record_id, "/review_queue_refs", "review queue boundary belongs to a paper outside paper_card_base"))
            card_is_newer = any(
                _timestamp_is_after(card_updated_at.get(paper_id, ""), candidate_updated_at)
                for paper_id in base_papers
            ) or any(value not in units for value in base_units)
            if expanded_evidence != set(record.get("evidence_base", [])) and not card_is_newer:
                diagnostics.append(Diagnostic(SNAPSHOT_MISMATCH, kind, record_id, "/evidence_base", "evidence_base does not equal selected Card Unit evidence expansion"))
            if expanded_boundaries != set(record.get("review_queue_refs", [])) and not card_is_newer:
                diagnostics.append(Diagnostic(SNAPSHOT_MISMATCH, kind, record_id, "/review_queue_refs", "review_queue_refs does not equal selected Card Unit boundary expansion"))

            mapping = question_records.get(record.get("question_id", ""))
            if mapping is not None:
                mapping_units = {
                    (link.get("paper_id"), value)
                    for link in mapping.get("paper_links", [])
                    for value in link.get("selected_card_unit_ids", [])
                }
                selected_pairs = {
                    (item.get("paper_id"), value)
                    for item in record.get("paper_card_base", [])
                    for value in item.get("card_unit_ids", [])
                }
                membership_changed = (
                    mapping.get("mapping_status") == "needs_resolution"
                    or not selected_pairs.issubset(mapping_units)
                )
                if membership_changed and not _timestamp_is_after(mapping.get("updated_at", ""), candidate_updated_at):
                    diagnostics.append(Diagnostic(STEP7_BOUNDARY, kind, record_id, "/paper_card_base", "Step 7 support is outside the current Question Mapping without a newer mapping"))
            if kind == "step7-synthesis" and len(base_papers) < 2:
                diagnostics.append(Diagnostic(STEP7_BOUNDARY, kind, record_id, "/paper_card_base", "Synthesis requires at least two distinct papers"))
            if kind == "step7-cross-view":
                for value in record.get("source_views", []):
                    _require_ref(diagnostics, kind, record_id, "/source_views", value, candidates, "Step 7 candidate")
                    if value == record_id:
                        diagnostics.append(Diagnostic(STEP7_BOUNDARY, kind, record_id, "/source_views", "Cross-View cannot reference itself"))
                    source = candidate_records.get(value)
                    if source is not None and source.get("question_id") != record.get("question_id"):
                        diagnostics.append(Diagnostic(STEP7_BOUNDARY, kind, record_id, "/source_views", "Cross-View source belongs to another question"))
                    if (
                        source is not None
                        and source.get("candidate_status") in {"rejected", "needs_resolution"}
                        and not _timestamp_is_after(source.get("updated_at", ""), candidate_updated_at)
                    ):
                        diagnostics.append(Diagnostic(STEP7_BOUNDARY, kind, record_id, "/source_views", "Cross-View source is not admissible without a newer source revision"))
            snapshot = record.get("input_snapshot")
            if isinstance(snapshot, dict):
                if set(snapshot.get("card_unit_ids", [])) != set(base_units):
                    diagnostics.append(Diagnostic(SNAPSHOT_MISMATCH, kind, record_id, "/input_snapshot/card_unit_ids", "snapshot Card Units do not match paper_card_base"))
                if set(snapshot.get("evidence_ids", [])) != set(record.get("evidence_base", [])):
                    diagnostics.append(Diagnostic(SNAPSHOT_MISMATCH, kind, record_id, "/input_snapshot/evidence_ids", "snapshot evidence does not match evidence_base"))
                if set(snapshot.get("review_queue_ids", [])) != set(record.get("review_queue_refs", [])):
                    diagnostics.append(Diagnostic(SNAPSHOT_MISMATCH, kind, record_id, "/input_snapshot/review_queue_ids", "snapshot review queue does not match review_queue_refs"))
        elif kind == "process-event":
            if record.get("job_id") is not None and record.get("result") == "success":
                _require_ref(diagnostics, kind, record_id, "/job_id", record.get("job_id"), jobs, "Pipeline Job")
            for field in ("input_refs", "output_refs"):
                for value in record.get(field, []):
                    _require_ref(diagnostics, kind, record_id, f"/{field}", value, all_object_ids, "record")
        elif kind == "pipeline-job-state":
            _require_ref(diagnostics, kind, record_id, "/workspace_id", record.get("workspace_id"), workspaces, "workspace")
            for field in ("input_refs", "output_refs"):
                for value in record.get(field, []):
                    _require_ref(diagnostics, kind, record_id, f"/{field}", value, all_object_ids, "record")
        elif kind == "agent-task-state":
            _require_ref(diagnostics, kind, record_id, "/workspace_id", record.get("workspace_id"), workspaces, "workspace")
            basis = record.get("input_basis", {})
            _require_ref(diagnostics, kind, record_id, "/input_basis/paper_id", basis.get("paper_id"), papers, "paper")
            _require_ref(diagnostics, kind, record_id, "/input_basis/job_id", basis.get("job_id"), jobs, "Pipeline Job")
            _require_ref(
                diagnostics,
                kind,
                record_id,
                "/input_basis/job_state_id",
                basis.get("job_state_id"),
                set(defined["jobstate"]),
                "Pipeline Job state",
            )
            _require_ref(
                diagnostics,
                kind,
                record_id,
                "/input_basis/parse_run_id",
                basis.get("parse_run_id"),
                events,
                "parse process event",
            )
            _require_ref(
                diagnostics,
                kind,
                record_id,
                "/input_basis/adequacy_profile_id",
                basis.get("adequacy_profile_id"),
                set(adequacy_profiles),
                "Source Adequacy profile",
            )
            if basis.get("origin_job_id") is not None:
                _require_ref(
                    diagnostics,
                    kind,
                    record_id,
                    "/input_basis/origin_job_id",
                    basis.get("origin_job_id"),
                    jobs,
                    "Pipeline Job",
                )
            for index, snapshot in enumerate(basis.get("adequacy_profiles", [])):
                _require_ref(
                    diagnostics,
                    kind,
                    record_id,
                    f"/input_basis/adequacy_profiles/{index}/profile_id",
                    snapshot.get("profile_id"),
                    set(adequacy_profiles),
                    "Source Adequacy profile",
                )
            predecessor = record.get("predecessor")
            if isinstance(predecessor, dict):
                _require_ref(
                    diagnostics,
                    kind,
                    record_id,
                    "/predecessor/state_id",
                    predecessor.get("state_id"),
                    set(defined["taskstate"]),
                    "Agent Task state",
                )
            lineage = record.get("lineage")
            if isinstance(lineage, dict):
                _require_ref(
                    diagnostics,
                    kind,
                    record_id,
                    "/lineage/predecessor_task_id",
                    lineage.get("predecessor_task_id"),
                    agent_tasks,
                    "Agent Task",
                )
            decision = record.get("decision")
            if isinstance(decision, dict):
                if decision.get("successor_task_id") is not None:
                    _require_ref(
                        diagnostics,
                        kind,
                        record_id,
                        "/decision/successor_task_id",
                        decision.get("successor_task_id"),
                        agent_tasks,
                        "Agent Task",
                    )
                if decision.get("applied_job_state_id") is not None:
                    _require_ref(
                        diagnostics,
                        kind,
                        record_id,
                        "/decision/applied_job_state_id",
                        decision.get("applied_job_state_id"),
                        set(defined["jobstate"]),
                        "Pipeline Job state",
                    )
        elif kind == "primary-semantic-bundle":
            for index, revision in enumerate(record.get("revisions", [])):
                _require_ref(
                    diagnostics,
                    kind,
                    record_id,
                    f"/revisions/{index}/approval/task_id",
                    revision.get("approval", {}).get("task_id"),
                    agent_tasks,
                    "Agent Task",
                )
                for profile_index, snapshot in enumerate(
                    revision.get("input_snapshot", {}).get("adequacy_profiles", [])
                ):
                    _require_ref(
                        diagnostics,
                        kind,
                        record_id,
                        f"/revisions/{index}/input_snapshot/adequacy_profiles/{profile_index}/profile_id",
                        snapshot.get("profile_id"),
                        set(adequacy_profiles),
                        "Source Adequacy profile",
                    )
        elif kind == "review-semantic-bundle":
            for index, revision in enumerate(record.get("revisions", [])):
                _require_ref(
                    diagnostics,
                    kind,
                    record_id,
                    f"/revisions/{index}/approval/task_id",
                    revision.get("approval", {}).get("task_id"),
                    agent_tasks,
                    "Agent Task",
                )
                for profile_index, snapshot in enumerate(
                    revision.get("input_snapshot", {}).get("adequacy_profiles", [])
                ):
                    _require_ref(
                        diagnostics,
                        kind,
                        record_id,
                        f"/revisions/{index}/input_snapshot/adequacy_profiles/{profile_index}/profile_id",
                        snapshot.get("profile_id"),
                        set(adequacy_profiles),
                        "Source Adequacy profile",
                    )
                for binding_index, binding in enumerate(revision.get("provenance_bindings", [])):
                    _require_ref(
                        diagnostics,
                        kind,
                        record_id,
                        f"/revisions/{index}/provenance_bindings/{binding_index}/profile_id",
                        binding.get("profile_id"),
                        set(adequacy_profiles),
                        "Source Adequacy profile",
                    )
        elif kind == "guardian-report":
            _require_ref(diagnostics, kind, record_id, "/workspace_id", record.get("workspace_id"), workspaces, "workspace")
            for index, finding in enumerate(record.get("findings", [])):
                value = finding.get("record_ref")
                if value is not None:
                    _require_ref(diagnostics, kind, record_id, f"/findings/{index}/record_ref", value, all_object_ids, "record")
        elif kind == "guardian-finding-disposition":
            _require_ref(diagnostics, kind, record_id, "/workspace_id", record.get("workspace_id"), workspaces, "workspace")
            _require_ref(diagnostics, kind, record_id, "/guardian_report_id", record.get("guardian_report_id"), {item.get("guardian_report_id", "") for item in guardian_reports}, "Guardian report")
            if record.get("previous_disposition_id") is not None:
                _require_ref(diagnostics, kind, record_id, "/previous_disposition_id", record.get("previous_disposition_id"), {item.get("disposition_id", "") for item in guardian_dispositions}, "Guardian disposition")
    return diagnostics


def _require_ref(
    diagnostics: list[Diagnostic],
    kind: str,
    record_id: str | None,
    path: str,
    value: object,
    available: set[str],
    target: str,
) -> None:
    if isinstance(value, str) and value and value not in available:
        diagnostics.append(Diagnostic(UNRESOLVED_REFERENCE, kind, record_id, path, f"unresolved {target} reference: {value}"))


def _timestamp_is_after(candidate: object, baseline: object) -> bool:
    if not isinstance(candidate, str) or not candidate or not isinstance(baseline, str) or not baseline:
        return False
    return datetime.fromisoformat(candidate.replace("Z", "+00:00")) > datetime.fromisoformat(
        baseline.replace("Z", "+00:00")
    )


def _record_id(kind: str, record: dict[str, Any]) -> str | None:
    fields = {
        "workspace-marker": "workspace_id",
        "compatibility-difference": "difference_id",
        "registry-paper": "paper_id",
        "paper-card": "paper_id",
        "review-memory": "review_memory_id",
        "evidence": "evidence_id",
        "review-queue": "queue_id",
        "question-mapping": "question_id",
        "step7-synthesis": "candidate_id",
        "step7-review-angle": "candidate_id",
        "step7-insight": "candidate_id",
        "step7-cross-view": "candidate_id",
        "discovery-candidate": "candidate_id",
        "process-event": "event_id",
        "guardian-report": "guardian_report_id",
        "pipeline-job-state": "state_id",
        "guardian-finding-disposition": "disposition_id",
        "source-asset-state": "source_asset_state_id",
        "registry-identity-correction": "correction_id",
        "source-adequacy-profile": "profile_id",
        "agent-task-state": "state_id",
        "primary-semantic-bundle": "paper_id",
        "review-semantic-bundle": "paper_id",
    }
    field = fields.get(kind)
    value = record.get(field) if field else None
    return value if isinstance(value, str) else None


def _deduplicate_diagnostics(diagnostics: list[Diagnostic]) -> list[Diagnostic]:
    seen: set[tuple[str, str, str | None, str, str]] = set()
    result: list[Diagnostic] = []
    for diagnostic in diagnostics:
        key = (diagnostic.code, diagnostic.record_kind, diagnostic.record_id, diagnostic.json_path, diagnostic.message)
        if key not in seen:
            seen.add(key)
            result.append(diagnostic)
    return result
