from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from research_kb.contracts.registry import SchemaRegistry
from research_kb.contracts.versions import require_supported
from research_kb.errors import (
    DUPLICATE_ID,
    DUPLICATE_PAPER_CARD,
    GROUNDING_MISMATCH,
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
from research_kb.paths import normalize_relative_path, validate_config_relative_path


CONFIG_KINDS = {"workspace", "domain-profile", "mutation-request"}
HUMAN_ONLY_REVIEW_STATES = {"human_checked", "verified"}
NON_SUPPORTING_UNIT_STATES = {"interpretive", "background_only", "needs_resolution"}


def validate_record(
    kind: str,
    record: dict[str, Any],
    *,
    registry: SchemaRegistry | None = None,
    actor: str = "agent",
) -> list[Diagnostic]:
    schema_registry = registry or SchemaRegistry()
    if kind not in schema_registry.kinds:
        return [Diagnostic(UNKNOWN_SCHEMA_KIND, kind, None, "", f"unknown record schema kind: {kind}")]
    try:
        schema = schema_registry.schema(kind)
    except ResearchKBError as error:
        return [error.diagnostic]

    version_field = "contract_version" if kind in CONFIG_KINDS else "schema_version"
    try:
        require_supported(record.get(version_field))
    except ResearchKBError as error:
        diagnostic = error.diagnostic
        return [
            Diagnostic(
                diagnostic.code,
                kind,
                _record_id(kind, record),
                f"/{version_field}",
                diagnostic.message,
                diagnostic.severity,
            )
        ]

    validator = Draft202012Validator(
        schema,
        registry=schema_registry.referencing_registry(),
        format_checker=FormatChecker(),
    )
    diagnostics = [
        Diagnostic(
            SCHEMA_VALIDATION_FAILED,
            kind,
            _record_id(kind, record),
            json_pointer(error.absolute_path),
            error.message,
        )
        for error in sorted(validator.iter_errors(record), key=lambda item: (list(item.absolute_path), item.message))
    ]
    diagnostics.extend(_local_semantic_diagnostics(kind, record))
    diagnostics.extend(_authority_diagnostics(kind, record, actor))
    return diagnostics


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
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict) or not isinstance(entry.get("kind"), str) or not isinstance(entry.get("record"), dict):
            diagnostics.append(
                Diagnostic(SCHEMA_VALIDATION_FAILED, "bundle", None, f"/records/{index}", "bundle entry must contain kind and record")
            )
            continue
        kind = entry["kind"]
        record = entry["record"]
        record_diagnostics = validate_record(kind, record, registry=schema_registry, actor=actor)
        diagnostics.extend(record_diagnostics)
        if not any(item.code in {UNSUPPORTED_VERSION, SCHEMA_VALIDATION_FAILED, UNKNOWN_SCHEMA_KIND} for item in record_diagnostics):
            normalized.append((kind, record))
    diagnostics.extend(_cross_record_diagnostics(normalized))
    return _deduplicate_diagnostics(diagnostics)


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
    elif kind == "registry-paper" and isinstance(record.get("source_ref"), dict):
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
    if kind.startswith("step7-") and isinstance(record.get("evidence_base"), list):
        for value in record["evidence_base"]:
            if isinstance(value, str) and value.startswith("queue_"):
                diagnostics.append(Diagnostic(QUEUE_AS_EVIDENCE, kind, _record_id(kind, record), "/evidence_base", "review queue record used as evidence"))
    return diagnostics


def _cross_record_diagnostics(entries: list[tuple[str, dict[str, Any]]]) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    workspaces: set[str] = set()
    profiles: set[str] = set()
    papers: set[str] = set()
    evidence: set[str] = set()
    queues: set[str] = set()
    units: set[str] = set()
    questions: set[str] = set()
    candidates: set[str] = set()
    events: set[str] = set()
    source_roots: set[str] = set()
    unit_paper: dict[str, str] = {}
    unit_evidence: dict[str, set[str]] = {}
    unit_status: dict[str, str] = {}
    evidence_paper: dict[str, str] = {}
    queue_paper: dict[str, str] = {}
    paper_fingerprint: dict[str, dict[str, Any]] = {}
    profile_sections: dict[str, list[str]] = {}
    defined: dict[str, list[str]] = defaultdict(list)
    paper_cards: dict[str, int] = defaultdict(int)

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
        elif kind == "paper-card":
            paper_id = record.get("paper_id", "")
            paper_cards[paper_id] += 1
            for section in record.get("sections", []):
                for unit in section.get("units", []):
                    unit_id = unit.get("unit_id", "")
                    units.add(unit_id)
                    defined["unit"].append(unit_id)
                    unit_paper[unit_id] = paper_id
                    unit_evidence[unit_id] = set(unit.get("evidence_ids", []))
                    unit_status[unit_id] = unit.get("grounding_status", "")
        elif kind == "evidence":
            evidence_id = record.get("evidence_id", "")
            evidence.add(evidence_id)
            defined["evidence"].append(evidence_id)
            evidence_paper[evidence_id] = record.get("paper_id", "")
        elif kind == "review-queue":
            queue_id = record.get("queue_id", "")
            queues.add(queue_id)
            defined["queue"].append(queue_id)
            queue_paper[queue_id] = record.get("paper_id", "")
        elif kind == "question-mapping":
            question_id = record.get("question_id", "")
            questions.add(question_id)
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
                defined[namespace].append(candidate_id)
        elif kind == "process-event":
            event_id = record.get("event_id", "")
            events.add(event_id)
            defined["event"].append(event_id)
        elif kind == "guardian-report":
            defined["guardian"].append(record.get("guardian_report_id", ""))

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

    all_object_ids = {value for values in defined.values() for value in values if value}

    for kind, record in entries:
        record_id = _record_id(kind, record)
        if kind in {"parsed-page", "paper-card", "evidence", "review-queue"}:
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
        elif kind == "question-mapping":
            _require_ref(diagnostics, kind, record_id, "/domain_profile_id", record.get("domain_profile_id"), profiles, "domain profile")
            for link_index, link in enumerate(record.get("paper_links", [])):
                base = f"/paper_links/{link_index}"
                paper_id = link.get("paper_id")
                _require_ref(diagnostics, kind, record_id, base + "/paper_id", paper_id, papers, "paper")
                for value in link.get("selected_card_unit_ids", []):
                    _require_ref(diagnostics, kind, record_id, base + "/selected_card_unit_ids", value, units, "Card Unit")
                    if value in unit_paper and unit_paper[value] != paper_id:
                        diagnostics.append(Diagnostic(GROUNDING_MISMATCH, kind, record_id, base + "/selected_card_unit_ids", "selected Card Unit belongs to another paper"))
                for value in link.get("evidence_ids", []):
                    _require_ref(diagnostics, kind, record_id, base + "/evidence_ids", value, evidence, "evidence")
                    if value in evidence_paper and evidence_paper[value] != paper_id:
                        diagnostics.append(Diagnostic(GROUNDING_MISMATCH, kind, record_id, base + "/evidence_ids", "question-link evidence belongs to another paper"))
                for value in link.get("boundary_refs", []):
                    _require_ref(diagnostics, kind, record_id, base + "/boundary_refs", value, queues, "review queue")
                    if value in queue_paper and queue_paper[value] != paper_id:
                        diagnostics.append(Diagnostic(GROUNDING_MISMATCH, kind, record_id, base + "/boundary_refs", "question-link boundary belongs to another paper"))
        elif kind.startswith("step7-"):
            _require_ref(diagnostics, kind, record_id, "/question_id", record.get("question_id"), questions, "question")
            base_units: list[str] = []
            base_papers: set[str] = set()
            expanded_evidence: set[str] = set()
            for base_index, item in enumerate(record.get("paper_card_base", [])):
                paper_id = item.get("paper_id")
                if isinstance(paper_id, str):
                    base_papers.add(paper_id)
                _require_ref(diagnostics, kind, record_id, f"/paper_card_base/{base_index}/paper_id", paper_id, papers, "paper")
                for value in item.get("card_unit_ids", []):
                    base_units.append(value)
                    _require_ref(diagnostics, kind, record_id, f"/paper_card_base/{base_index}/card_unit_ids", value, units, "Card Unit")
                    if value in unit_paper and unit_paper[value] != paper_id:
                        diagnostics.append(Diagnostic(STEP7_BOUNDARY, kind, record_id, f"/paper_card_base/{base_index}/card_unit_ids", "Card Unit belongs to another paper"))
                    if value in unit_status and unit_status[value] not in {"grounded", "revised"}:
                        diagnostics.append(Diagnostic(STEP7_BOUNDARY, kind, record_id, f"/paper_card_base/{base_index}/card_unit_ids", "non-factual Card Unit cannot enter Step 7 support"))
                    expanded_evidence.update(unit_evidence.get(value, set()))
            for value in record.get("evidence_base", []):
                if value in queues:
                    diagnostics.append(Diagnostic(QUEUE_AS_EVIDENCE, kind, record_id, "/evidence_base", "review queue record used as evidence"))
                _require_ref(diagnostics, kind, record_id, "/evidence_base", value, evidence, "evidence")
            for value in record.get("review_queue_refs", []):
                _require_ref(diagnostics, kind, record_id, "/review_queue_refs", value, queues, "review queue")
                if value in queue_paper and queue_paper[value] not in base_papers:
                    diagnostics.append(Diagnostic(STEP7_BOUNDARY, kind, record_id, "/review_queue_refs", "review queue boundary belongs to a paper outside paper_card_base"))
            if expanded_evidence != set(record.get("evidence_base", [])):
                diagnostics.append(Diagnostic(SNAPSHOT_MISMATCH, kind, record_id, "/evidence_base", "evidence_base does not equal selected Card Unit evidence expansion"))
            if kind == "step7-synthesis" and len(base_papers) < 2:
                diagnostics.append(Diagnostic(STEP7_BOUNDARY, kind, record_id, "/paper_card_base", "Synthesis requires at least two distinct papers"))
            if kind == "step7-cross-view":
                for value in record.get("source_views", []):
                    _require_ref(diagnostics, kind, record_id, "/source_views", value, candidates, "Step 7 candidate")
                    if value == record_id:
                        diagnostics.append(Diagnostic(STEP7_BOUNDARY, kind, record_id, "/source_views", "Cross-View cannot reference itself"))
            snapshot = record.get("input_snapshot")
            if isinstance(snapshot, dict):
                if set(snapshot.get("card_unit_ids", [])) != set(base_units):
                    diagnostics.append(Diagnostic(SNAPSHOT_MISMATCH, kind, record_id, "/input_snapshot/card_unit_ids", "snapshot Card Units do not match paper_card_base"))
                if set(snapshot.get("evidence_ids", [])) != set(record.get("evidence_base", [])):
                    diagnostics.append(Diagnostic(SNAPSHOT_MISMATCH, kind, record_id, "/input_snapshot/evidence_ids", "snapshot evidence does not match evidence_base"))
                if set(snapshot.get("review_queue_ids", [])) != set(record.get("review_queue_refs", [])):
                    diagnostics.append(Diagnostic(SNAPSHOT_MISMATCH, kind, record_id, "/input_snapshot/review_queue_ids", "snapshot review queue does not match review_queue_refs"))
        elif kind == "process-event":
            for field in ("input_refs", "output_refs"):
                for value in record.get(field, []):
                    _require_ref(diagnostics, kind, record_id, f"/{field}", value, all_object_ids, "record")
        elif kind == "guardian-report":
            _require_ref(diagnostics, kind, record_id, "/workspace_id", record.get("workspace_id"), workspaces, "workspace")
            for index, finding in enumerate(record.get("findings", [])):
                value = finding.get("record_ref")
                if value is not None:
                    _require_ref(diagnostics, kind, record_id, f"/findings/{index}/record_ref", value, all_object_ids, "record")
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


def _record_id(kind: str, record: dict[str, Any]) -> str | None:
    fields = {
        "workspace-marker": "workspace_id",
        "registry-paper": "paper_id",
        "paper-card": "paper_id",
        "evidence": "evidence_id",
        "review-queue": "queue_id",
        "question-mapping": "question_id",
        "step7-synthesis": "candidate_id",
        "step7-review-angle": "candidate_id",
        "step7-insight": "candidate_id",
        "step7-cross-view": "candidate_id",
        "process-event": "event_id",
        "guardian-report": "guardian_report_id",
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
