from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from research_kb.bundle import BundleEntry, records_of_kind
from research_kb.catalog.models import canonical_digest
from research_kb.errors import GROUNDING_MISMATCH, Diagnostic, ResearchKBError
from research_kb.parser_profiles import parser_profile_descriptor
from research_kb.source_assets import current_source_asset_heads
from research_kb.source_resolution import inspect_source_ref, observe_paper_source
from research_kb.storage.json_io import file_sha256
from research_kb.workspace import WorkspaceLayout


OPERATION_REGISTRY_VERSION = "p3c-v1"
ASSESSMENT_RULE_VERSION = "p3c-v1"
CAPABILITIES = (
    "basic_paper_understanding",
    "complete_reading",
    "continuous_text_citation",
    "figure_table_evidence_extraction",
    "formula_or_layout_sensitive_analysis",
    "supplementary_material_analysis",
)
MAIN_PARSE_CAPABILITIES = frozenset(CAPABILITIES[:-1])
SUPPLEMENT_CAPABILITIES = frozenset({"supplementary_material_analysis"})
OPERATION_CAPABILITY = {
    "basic_paper_card": "basic_paper_understanding",
    "basic_review_memory": "basic_paper_understanding",
    "complete_reading": "complete_reading",
    "continuous_text_evidence": "continuous_text_citation",
    "figure_table_evidence": "figure_table_evidence_extraction",
    "formula_layout_analysis": "formula_or_layout_sensitive_analysis",
    "supplementary_analysis": "supplementary_material_analysis",
}


@dataclass(frozen=True, slots=True)
class SourceSnapshotState:
    snapshots: tuple[dict[str, Any], ...]
    digest_matches_by_role: Mapping[str, bool]
    available_by_role: Mapping[str, bool]


@dataclass(frozen=True, slots=True)
class ParseSnapshotState:
    snapshot: dict[str, Any]
    descriptor: Mapping[str, str]
    pages: tuple[dict[str, Any], ...]


def collect_source_snapshot_state(
    layout: WorkspaceLayout,
    entries: list[BundleEntry],
    paper: dict[str, Any],
) -> SourceSnapshotState:
    states = records_of_kind(entries, "source-asset-state")
    heads = [
        item
        for item in current_source_asset_heads(states)
        if item.get("paper_id") == paper["paper_id"]
    ]
    snapshots: list[dict[str, Any]] = []
    digest_matches: dict[str, bool] = {}
    available: dict[str, bool] = {}
    explicit_main = any(item["asset_role"] == "main_pdf" for item in heads)

    if not explicit_main:
        observation = observe_paper_source(layout, entries, paper)
        snapshots.append(
            {
                "source_asset_id": None,
                "source_asset_state_id": None,
                "role": "main_pdf",
                "source_ref": dict(observation.source_ref),
                "manifestation_id": f"sha256:{observation.expected_sha256}",
                "availability": _public_availability(observation.state),
            }
        )
        available["main_pdf"] = observation.state in {"current", "fingerprint_mismatch"}
        digest_matches["main_pdf"] = observation.state == "current"

    for head in heads:
        source_ref = head["source_ref"]
        inspected = inspect_source_ref(
            layout,
            root_id=source_ref["root_id"],
            relative_path=source_ref["relative_path"],
        )
        role = head["asset_role"]
        role_available = (
            head["availability"] == "available"
            and head["manifestation_status"] == "active"
            and inspected.availability == "available"
        )
        role_matches = role_available and inspected.live_sha256 == head["source_fingerprint"]["value"]
        snapshots.append(
            {
                "source_asset_id": head["source_asset_id"],
                "source_asset_state_id": head["source_asset_state_id"],
                "role": role,
                "source_ref": dict(source_ref),
                "manifestation_id": head["manifestation_id"],
                "availability": head["availability"],
            }
        )
        available[role] = available.get(role, False) or role_available
        digest_matches[role] = digest_matches.get(role, False) or role_matches

    return SourceSnapshotState(
        tuple(sorted(snapshots, key=_source_snapshot_key)),
        digest_matches,
        available,
    )


def collect_parse_snapshot_state(
    layout: WorkspaceLayout,
    pages: Iterable[dict[str, Any]],
) -> ParseSnapshotState:
    ordered = tuple(sorted(pages, key=lambda item: item["pdf_page"]))
    if not ordered:
        raise ResearchKBError(
            Diagnostic(
                GROUNDING_MISMATCH,
                "source-adequacy-profile",
                None,
                "/parse_snapshot",
                "Source Adequacy requires an active parse",
            )
        )
    run_ids = {item["parse_run_id"] for item in ordered}
    parsers = {(item["parser"]["adapter"], item["parser"]["version"]) for item in ordered}
    if len(run_ids) != 1 or len(parsers) != 1:
        raise ResearchKBError(
            Diagnostic(
                GROUNDING_MISMATCH,
                "source-adequacy-profile",
                None,
                "/parse_snapshot",
                "active parsed pages do not share one parse identity",
            )
        )
    adapter_id, version = next(iter(parsers))
    descriptor = parser_profile_descriptor(adapter_id, version)
    output_digest = file_sha256(layout.parse_path(ordered[0]["paper_id"]))
    if output_digest is None:
        raise ResearchKBError(
            Diagnostic(
                GROUNDING_MISMATCH,
                "source-adequacy-profile",
                None,
                "/parse_snapshot/output_bundle_digest",
                "active parse output is unavailable",
            )
        )
    snapshot = {
        "active_parse_ref": next(iter(run_ids)),
        "parser_identity": {
            "adapter_id": adapter_id,
            "version": version,
            "profile_digest": canonical_digest(descriptor),
        },
        "output_bundle_digest": output_digest,
        "page_count": len(ordered),
    }
    return ParseSnapshotState(snapshot, descriptor, ordered)


def build_machine_assessment(
    source_state: SourceSnapshotState,
    parse_state: ParseSnapshotState,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], list[str], list[str]]:
    pages = parse_state.pages
    descriptor = parse_state.descriptor
    page_numbers = [item["pdf_page"] for item in pages]
    page_coverage = page_numbers == list(range(1, len(pages) + 1))
    text_present = any(item["text"].strip() for item in pages)
    all_pages_have_text = all(item["text"].strip() for item in pages)
    locators = [item["locator"] for item in pages]
    locator_reproducible = len(locators) == len(set(locators)) and all(
        item["locator"].startswith(f"page:{item['pdf_page']}:") for item in pages
    )
    main_available = source_state.available_by_role.get("main_pdf", False)
    main_matches = source_state.digest_matches_by_role.get("main_pdf", False)
    supplement_available = source_state.available_by_role.get("supplement", False)
    supplement_matches = source_state.digest_matches_by_role.get("supplement", False)

    observations = [
        _observation("source_readable", "pass" if main_available else "fail", not main_available, CAPABILITIES, "The active main source is readable." if main_available else "The active main source is not readable."),
        _observation("source_digest_matches", "pass" if main_matches else "fail", not main_matches, CAPABILITIES, "The active main source digest matches." if main_matches else "The active main source digest does not match."),
        _observation("source_set_complete", "pass", False, MAIN_PARSE_CAPABILITIES, "The main source required for main-text operations is present."),
        _observation("parse_run_consistent", "pass", False, MAIN_PARSE_CAPABILITIES, "All active pages share one parse run."),
        _observation("parser_identity_matches", "uncertain" if descriptor["settings"] == "unregistered-profile" else "pass", False, MAIN_PARSE_CAPABILITIES, "The parser profile is not registered." if descriptor["settings"] == "unregistered-profile" else "The parser profile is registered and reproducible."),
        _observation("text_presence", "pass" if text_present else "fail", not text_present, ("basic_paper_understanding", "complete_reading", "continuous_text_citation"), "Extracted text is present." if text_present else "No extracted text is present."),
        _observation("page_coverage", "pass" if page_coverage and all_pages_have_text else "uncertain", False, ("basic_paper_understanding", "complete_reading", "continuous_text_citation"), "Parsed pages are contiguous and contain text." if page_coverage and all_pages_have_text else "Page coverage or text coverage is incomplete."),
        _observation("locator_reproducibility", "pass" if locator_reproducible else "fail", not locator_reproducible, MAIN_PARSE_CAPABILITIES, "Page locators are unique and reproducible." if locator_reproducible else "One or more page locators are not reproducible."),
        _observation("reading_order_reliability", "pass" if descriptor["reading_order"] == "reliable" else "uncertain", False, ("complete_reading", "continuous_text_citation"), "The parser profile guarantees deterministic reading order." if descriptor["reading_order"] == "reliable" else "Reading order requires review or a layout-aware parse."),
        _observation("figure_table_context", "pass" if descriptor["figure_table_context"] == "supported" else ("uncertain" if descriptor["figure_table_context"] == "uncertain" else "fail"), False, ("complete_reading", "figure_table_evidence_extraction"), "Figure/table context is available." if descriptor["figure_table_context"] == "supported" else "The active parser does not establish figure/table context."),
        _observation("formula_layout_context", "pass" if descriptor["formula_layout_context"] == "supported" else ("uncertain" if descriptor["formula_layout_context"] == "uncertain" else "fail"), False, ("complete_reading", "formula_or_layout_sensitive_analysis"), "Formula/layout context is available." if descriptor["formula_layout_context"] == "supported" else "The active parser does not establish formula/layout context."),
        _observation("supplement_available", "pass" if supplement_available and supplement_matches else "fail", False, SUPPLEMENT_CAPABILITIES, "A current supplementary source is available." if supplement_available and supplement_matches else "No current supplementary source is available."),
        _observation("supplement_parse_available", "pass" if descriptor["supplement_parse"] == "supported" else "fail", False, SUPPLEMENT_CAPABILITIES, "Supplementary parsed content is available." if descriptor["supplement_parse"] == "supported" else "The active parse does not include supplementary content."),
    ]
    capabilities = {
        capability: _capability_result(capability, observations, descriptor)
        for capability in CAPABILITIES
    }
    limitations = sorted(
        {
            item["reason"]
            for item in observations
            if item["status"] in {"fail", "uncertain"}
        }
    )
    actions = _recommended_actions(capabilities)
    return observations, capabilities, limitations, actions


def apply_user_decision(
    capabilities: Mapping[str, Mapping[str, Any]],
    observations: Iterable[Mapping[str, Any]],
    decision: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    selected = set(decision["capabilities"])
    hard_blocked = {
        capability
        for item in observations
        if item["hard_failure"] and item["status"] == "fail"
        for capability in item["affected_capabilities"]
    }
    result = {key: dict(value) for key, value in capabilities.items()}
    public_reason = (
        "User accepted the recorded non-hard uncertainty."
        if decision["decision"] == "accept_uncertainty"
        else "User requires remediation before this capability can be used."
    )
    for capability in selected:
        current = result[capability]
        if decision["decision"] == "accept_uncertainty":
            if capability in hard_blocked or current["status"] == "no":
                raise ResearchKBError(
                    Diagnostic(
                        GROUNDING_MISMATCH,
                        "source-adequacy-profile",
                        None,
                        f"/user_decision/capabilities/{capability}",
                        "user decision cannot override a hard or inadequate capability",
                    )
                )
            if current["status"] == "uncertain":
                current["status"] = "yes"
        else:
            current["status"] = "no"
        current["reasons"] = [*current["reasons"], public_reason]
        current["authority_layers"] = [*current["authority_layers"], "user"]
    return result


def profile_freshness(
    layout: WorkspaceLayout,
    entries: list[BundleEntry],
    profile: Mapping[str, Any],
) -> dict[str, Any]:
    reasons: set[str] = set()
    affected: set[str] = set()
    if profile["operation_registry_version"] != OPERATION_REGISTRY_VERSION:
        reasons.add("operation_registry_changed")
        affected.update(CAPABILITIES)
    if profile["assessment_rule_version"] != ASSESSMENT_RULE_VERSION:
        reasons.add("assessment_rule_changed")
        affected.update(CAPABILITIES)
    paper = next(
        (item for item in records_of_kind(entries, "registry-paper") if item["paper_id"] == profile["paper_id"]),
        None,
    )
    if paper is None:
        reasons.add("paper_unavailable")
        affected.update(CAPABILITIES)
        return _freshness(reasons, affected)

    source_state = collect_source_snapshot_state(layout, entries, paper)
    recorded_sources = {_source_identity(item): item for item in profile["source_snapshots"]}
    current_sources = {_source_identity(item): item for item in source_state.snapshots}
    if set(recorded_sources) != set(current_sources):
        changed_roles = {
            item[1] for item in set(recorded_sources).symmetric_difference(current_sources)
        }
        reasons.add("source_asset_set_changed")
        affected.update(_capabilities_for_roles(changed_roles))
    for key in set(recorded_sources).intersection(current_sources):
        if recorded_sources[key]["manifestation_id"] != current_sources[key]["manifestation_id"]:
            reasons.add("source_manifestation_changed")
            affected.update(_capabilities_for_roles({key[1]}))
    for role, is_available in source_state.available_by_role.items():
        if not is_available or not source_state.digest_matches_by_role.get(role, False):
            reasons.add("source_unavailable_or_changed")
            affected.update(_capabilities_for_roles({role}))

    pages = [
        item
        for item in records_of_kind(entries, "parsed-page")
        if item["paper_id"] == profile["paper_id"]
    ]
    try:
        current_parse = collect_parse_snapshot_state(layout, pages).snapshot
    except ResearchKBError:
        reasons.add("active_parse_unavailable")
        affected.update(MAIN_PARSE_CAPABILITIES)
    else:
        recorded_parse = profile["parse_snapshot"]
        if current_parse["active_parse_ref"] != recorded_parse["active_parse_ref"]:
            reasons.add("active_parse_changed")
            affected.update(MAIN_PARSE_CAPABILITIES)
        if current_parse["parser_identity"] != recorded_parse["parser_identity"]:
            reasons.add("parser_profile_changed")
            affected.update(MAIN_PARSE_CAPABILITIES)
        if current_parse["output_bundle_digest"] != recorded_parse["output_bundle_digest"]:
            reasons.add("parse_output_changed")
            affected.update(MAIN_PARSE_CAPABILITIES)
    return _freshness(reasons, affected)


def required_capability(requested_operation: str) -> str:
    try:
        return OPERATION_CAPABILITY[requested_operation]
    except KeyError as error:
        raise ValueError(f"unknown Source Adequacy requested operation: {requested_operation}") from error


def _capability_result(
    capability: str,
    observations: Iterable[Mapping[str, Any]],
    descriptor: Mapping[str, str],
) -> dict[str, Any]:
    relevant = [item for item in observations if capability in item["affected_capabilities"]]
    hard_failure = any(item["hard_failure"] and item["status"] == "fail" for item in relevant)
    statuses = {item["status"] for item in relevant}
    if hard_failure:
        status = "no"
    elif capability == "figure_table_evidence_extraction":
        status = _descriptor_status(descriptor["figure_table_context"])
    elif capability == "formula_or_layout_sensitive_analysis":
        status = _descriptor_status(descriptor["formula_layout_context"])
    elif capability == "supplementary_material_analysis":
        status = "yes" if all(item["status"] == "pass" for item in relevant) else "no"
    elif capability == "complete_reading":
        status = "yes" if descriptor["complete_reading"] == "supported" and statuses <= {"pass"} else ("no" if "fail" in statuses else "uncertain")
    elif capability == "continuous_text_citation":
        status = "no" if "fail" in statuses else ("uncertain" if "uncertain" in statuses else "yes")
    else:
        status = "no" if "fail" in statuses else ("uncertain" if "uncertain" in statuses else "yes")
    reasons = [item["reason"] for item in relevant if item["status"] != "pass"]
    if not reasons:
        reasons = ["All deterministic checks for this capability passed."]
    return {"status": status, "reasons": reasons, "authority_layers": ["machine"]}


def _descriptor_status(value: str) -> str:
    if value == "supported":
        return "yes"
    return "uncertain" if value == "uncertain" else "no"


def _recommended_actions(capabilities: Mapping[str, Mapping[str, Any]]) -> list[str]:
    actions: set[str] = set()
    if capabilities["supplementary_material_analysis"]["status"] != "yes":
        actions.add("acquire_or_parse_supplement")
    if capabilities["figure_table_evidence_extraction"]["status"] != "yes":
        actions.add("run_layout_aware_parse")
    if capabilities["formula_or_layout_sensitive_analysis"]["status"] != "yes":
        actions.add("run_layout_aware_parse")
    if capabilities["continuous_text_citation"]["status"] == "uncertain":
        actions.add("review_reading_order")
    return sorted(actions)


def _observation(
    code: str,
    status: str,
    hard_failure: bool,
    affected_capabilities: Iterable[str],
    reason: str,
) -> dict[str, Any]:
    return {
        "code": code,
        "status": status,
        "hard_failure": hard_failure,
        "affected_capabilities": sorted(affected_capabilities),
        "reason": reason,
    }


def _source_snapshot_key(item: Mapping[str, Any]) -> tuple[str, str]:
    return item["role"], item["source_asset_id"] or ""


def _source_identity(item: Mapping[str, Any]) -> tuple[str, str]:
    return item["source_asset_id"] or "implicit-main", item["role"]


def _capabilities_for_roles(roles: Iterable[str]) -> set[str]:
    result: set[str] = set()
    for role in roles:
        result.update(SUPPLEMENT_CAPABILITIES if role == "supplement" else MAIN_PARSE_CAPABILITIES)
    return result


def _freshness(reasons: set[str], affected: set[str]) -> dict[str, Any]:
    return {
        "state": "stale_upstream" if reasons else "current",
        "reasons": sorted(reasons),
        "affected_capabilities": sorted(affected),
    }


def _public_availability(state: str) -> str:
    if state in {"current", "fingerprint_mismatch"}:
        return "available"
    if state in {"missing", "inaccessible", "relink_required"}:
        return state
    return "relink_required"


__all__ = [
    "ASSESSMENT_RULE_VERSION",
    "CAPABILITIES",
    "OPERATION_CAPABILITY",
    "OPERATION_REGISTRY_VERSION",
    "ParseSnapshotState",
    "SourceSnapshotState",
    "apply_user_decision",
    "build_machine_assessment",
    "collect_parse_snapshot_state",
    "collect_source_snapshot_state",
    "parser_profile_descriptor",
    "profile_freshness",
    "required_capability",
]
