from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from copy import deepcopy
from typing import Any

from research_kb.application import APPLICATION_SERVICE_INTERFACE_VERSION
from research_kb.bundle import BundleEntry, load_workspace_entries, records_of_kind, validate_workspace_entries
from research_kb.catalog.models import canonical_digest
from research_kb.errors import (
    DUPLICATE_ID,
    SCHEMA_VALIDATION_FAILED,
    UNRESOLVED_REFERENCE,
    Diagnostic,
    ResearchKBError,
)
from research_kb.identifiers import Namespace, validate_id
from research_kb.services.question_mapping import mapping_freshness_diagnostics
from research_kb.services.workspace_session import WorkspaceSession
from research_kb.source_adequacy import profile_freshness, required_capability
from research_kb.source_resolution import inspect_source_ref
from research_kb.workspace import WorkspaceLayout


MAX_COMPARE_PAPERS = 4
FACTUAL_UNIT_STATUSES = frozenset({"grounded", "revised"})


class ReadingApplicationService:
    def show_paper(self, session: WorkspaceSession, paper_id: str) -> dict[str, Any]:
        layout = _session_layout(session)
        normalized_id = validate_id(paper_id, Namespace.PAPER)
        entries = load_workspace_entries(layout)
        validate_workspace_entries(entries)
        paper = _paper(entries, normalized_id)

        primary_bundle = _single_paper_record(entries, "primary-semantic-bundle", normalized_id)
        review_bundle = _single_paper_record(entries, "review-semantic-bundle", normalized_id)
        legacy_card = _single_paper_record(entries, "paper-card", normalized_id) if primary_bundle is None else None
        legacy_review = _single_paper_record(entries, "review-memory", normalized_id) if review_bundle is None else None

        primary: dict[str, Any] | None = None
        review: dict[str, Any] | None = None
        bound_parse_run_id: str | None = None
        expected_fingerprint = paper["source_fingerprint"]["value"]

        if primary_bundle is not None:
            revision = _active_revision(primary_bundle, "Primary")
            expected_fingerprint = revision["input_snapshot"]["source_fingerprint"]["value"]
            bound_parse_run_id = revision["input_snapshot"]["parse_run_id"]
            card = deepcopy(revision["paper_card"])
            primary = {
                "authority_mode": "revisioned_bundle",
                "revision_id": revision["revision_id"],
                "revision_number": revision["revision_number"],
                "revision_status": "active",
                "paper_card": card,
                "unit_admissibility": _unit_admissibility(card),
            }
            document_route = "primary"
            adequacy = _revision_adequacy(layout, entries, revision)
        elif review_bundle is not None:
            revision = _active_revision(review_bundle, "Review")
            expected_fingerprint = revision["input_snapshot"]["source_fingerprint"]["value"]
            bound_parse_run_id = revision["input_snapshot"]["parse_run_id"]
            review = {
                "authority_mode": "revisioned_bundle",
                "revision_id": revision["revision_id"],
                "revision_number": revision["revision_number"],
                "revision_status": "active",
                "review_memory": _safe_review_memory(revision["review_memory"]),
                "factual_support_eligible": False,
            }
            document_route = "review"
            adequacy = _revision_adequacy(layout, entries, revision)
        elif legacy_card is not None:
            bound_parse_run_id = _materialized_parse_run(entries, normalized_id)
            primary = {
                "authority_mode": "legacy_unversioned",
                "revision_id": None,
                "revision_number": None,
                "revision_status": "active",
                "paper_card": deepcopy(legacy_card),
                "unit_admissibility": _unit_admissibility(legacy_card),
            }
            document_route = "primary"
            adequacy = _legacy_adequacy(layout, entries, normalized_id)
        elif legacy_review is not None:
            bound_parse_run_id = legacy_review.get("parse_snapshot", {}).get("parse_run_id")
            review = {
                "authority_mode": "legacy_unversioned",
                "revision_id": None,
                "revision_number": None,
                "revision_status": "active",
                "review_memory": _safe_review_memory(legacy_review),
                "factual_support_eligible": False,
            }
            document_route = "review"
            adequacy = _legacy_adequacy(layout, entries, normalized_id)
        else:
            document_route = "unprocessed"
            adequacy = _legacy_adequacy(layout, entries, normalized_id)

        source = _stable_source_projection(layout, entries, paper, expected_fingerprint)
        return {
            "status": "success",
            "interface_version": "1.0",
            "application_service_interface_version": APPLICATION_SERVICE_INTERFACE_VERSION,
            "paper": _safe_paper(paper),
            "document_route": document_route,
            "primary": primary,
            "review": review,
            "source": source,
            "parse": _parse_projection(entries, normalized_id, bound_parse_run_id),
            "adequacy": adequacy,
            "questions": _question_context(entries, normalized_id),
            "persistent_writes": 0,
            "canonical_scientific_write": False,
        }

    def compare_papers(
        self,
        session: WorkspaceSession,
        paper_ids: Sequence[str],
    ) -> dict[str, Any]:
        if isinstance(paper_ids, (str, bytes)) or not isinstance(paper_ids, Sequence):
            raise _request_error("/paper_ids", "paper IDs must be an ordered array")
        if not 2 <= len(paper_ids) <= MAX_COMPARE_PAPERS:
            raise _request_error("/paper_ids", "comparison requires two to four papers")
        if not all(isinstance(item, str) for item in paper_ids):
            raise _request_error("/paper_ids", "paper IDs must be strings")
        if len(set(paper_ids)) != len(paper_ids):
            raise _request_error("/paper_ids", "comparison paper IDs must be unique")
        papers = [self.show_paper(session, paper_id) for paper_id in paper_ids]
        return {
            "status": "success",
            "interface_version": "1.0",
            "application_service_interface_version": APPLICATION_SERVICE_INTERFACE_VERSION,
            "papers": papers,
            "semantic_comparison": None,
            "persistent_writes": 0,
            "canonical_scientific_write": False,
        }

    def trace_evidence(self, session: WorkspaceSession, evidence_id: str) -> dict[str, Any]:
        layout = _session_layout(session)
        normalized_id = validate_id(evidence_id, Namespace.EVIDENCE)
        entries = load_workspace_entries(layout)
        validate_workspace_entries(entries)
        matches = _evidence_matches(entries, normalized_id)
        if not matches:
            raise ResearchKBError(
                Diagnostic(
                    UNRESOLVED_REFERENCE,
                    "evidence",
                    normalized_id,
                    "/evidence_id",
                    "evidence does not exist",
                )
            )
        if len(matches) != 1:
            raise ResearchKBError(
                Diagnostic(
                    DUPLICATE_ID,
                    "evidence",
                    normalized_id,
                    "/evidence_id",
                    "evidence ID resolves to more than one provenance owner",
                )
            )

        evidence, revision, bundle = matches[0]
        paper = _paper(entries, evidence["paper_id"])
        if revision is None:
            expected_fingerprint = evidence["source_fingerprint"]["value"]
            bound_parse_run_id = _materialized_parse_run(entries, evidence["paper_id"])
            revision_projection = {
                "authority_mode": "legacy_unversioned",
                "revision_id": None,
                "revision_number": None,
                "revision_status": "active",
            }
            active = True
        else:
            expected_fingerprint = revision["input_snapshot"]["source_fingerprint"]["value"]
            bound_parse_run_id = revision["input_snapshot"]["parse_run_id"]
            active = bundle is not None and revision["revision_id"] == bundle["active_revision_id"]
            revision_projection = {
                "authority_mode": "revisioned_bundle",
                "revision_id": revision["revision_id"],
                "revision_number": revision["revision_number"],
                "revision_status": "active" if active else "historical",
            }

        source = _stable_source_projection(layout, entries, paper, expected_fingerprint)
        parse = _parse_projection(entries, evidence["paper_id"], bound_parse_run_id)
        return {
            "status": "success",
            "interface_version": "1.0",
            "application_service_interface_version": APPLICATION_SERVICE_INTERFACE_VERSION,
            "evidence": _safe_evidence(evidence),
            "primary_revision": revision_projection,
            "source": source,
            "parse": parse,
            "factual_support_eligible": (
                active
                and source["trace_back_available"]
                and source["source_currentness"] == "current"
            ),
            "persistent_writes": 0,
            "canonical_scientific_write": False,
        }


def _paper(entries: list[BundleEntry], paper_id: str) -> dict[str, Any]:
    paper = next(
        (item for item in records_of_kind(entries, "registry-paper") if item["paper_id"] == paper_id),
        None,
    )
    if paper is None:
        raise ResearchKBError(
            Diagnostic(
                UNRESOLVED_REFERENCE,
                "registry-paper",
                paper_id,
                "/paper_id",
                "paper is not registered",
            )
        )
    return paper


def _single_paper_record(
    entries: Iterable[BundleEntry],
    kind: str,
    paper_id: str,
) -> dict[str, Any] | None:
    matches = [record for entry_kind, record in entries if entry_kind == kind and record.get("paper_id") == paper_id]
    if len(matches) > 1:
        raise ResearchKBError(
            Diagnostic(DUPLICATE_ID, kind, paper_id, "/paper_id", f"paper has more than one {kind} record")
        )
    return matches[0] if matches else None


def _active_revision(bundle: Mapping[str, Any], label: str) -> dict[str, Any]:
    revision = next(
        (
            item
            for item in bundle["revisions"]
            if item["revision_id"] == bundle["active_revision_id"]
        ),
        None,
    )
    if revision is None:
        raise ResearchKBError(
            Diagnostic(
                UNRESOLVED_REFERENCE,
                f"{label.lower()}-semantic-bundle",
                bundle.get("paper_id"),
                "/active_revision_id",
                f"{label} active revision does not exist",
            )
        )
    return revision


def _safe_paper(paper: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: deepcopy(paper[key])
        for key in (
            "paper_id",
            "bibliography",
            "screening_status",
            "review_status",
            "automation_status",
            "created_at",
            "updated_at",
        )
    }


def _safe_review_memory(memory: Mapping[str, Any]) -> dict[str, Any]:
    result = deepcopy(dict(memory))
    result.pop("source_fingerprint", None)
    return result


def _safe_evidence(evidence: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: deepcopy(evidence[key])
        for key in (
            "evidence_id",
            "paper_id",
            "claim",
            "evidence_type",
            "quote",
            "source_page",
            "locator",
            "support_scope",
            "what_it_does_not_support",
            "review_status",
            "automation_status",
            "created_at",
            "updated_at",
        )
    }


def _unit_admissibility(card: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "unit_id": unit["unit_id"],
            "section_id": section["section_id"],
            "grounding_status": unit["grounding_status"],
            "factual_support_eligible": unit["grounding_status"] in FACTUAL_UNIT_STATUSES,
            "evidence_ids": list(unit["evidence_ids"]),
            "boundary_refs": list(unit["boundary_refs"]),
        }
        for section in card["sections"]
        for unit in section["units"]
    ]


def _revision_adequacy(
    layout: WorkspaceLayout,
    entries: list[BundleEntry],
    revision: Mapping[str, Any],
) -> list[dict[str, Any]]:
    profiles = {
        item["profile_id"]: item
        for item in records_of_kind(entries, "source-adequacy-profile")
    }
    result = []
    for snapshot in revision["input_snapshot"]["adequacy_profiles"]:
        profile = profiles.get(snapshot["profile_id"])
        if profile is None or canonical_digest(profile) != snapshot["profile_digest"]:
            result.append(
                {
                    "requested_operation": snapshot["requested_operation"],
                    "freshness": "snapshot_unavailable",
                    "capability_status": None,
                }
            )
            continue
        capability = required_capability(snapshot["requested_operation"])
        freshness = profile_freshness(layout, entries, profile)
        result.append(
            {
                "requested_operation": snapshot["requested_operation"],
                "freshness": freshness["state"],
                "capability_status": profile["capabilities"][capability]["status"],
            }
        )
    return result


def _legacy_adequacy(
    layout: WorkspaceLayout,
    entries: list[BundleEntry],
    paper_id: str,
) -> list[dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for profile in records_of_kind(entries, "source-adequacy-profile"):
        if profile["paper_id"] != paper_id:
            continue
        operation = profile["requested_operation"]
        if operation not in latest or profile["assessed_at"] > latest[operation]["assessed_at"]:
            latest[operation] = profile
    return [
        {
            "requested_operation": operation,
            "freshness": profile_freshness(layout, entries, profile)["state"],
            "capability_status": profile["capabilities"][required_capability(operation)]["status"],
        }
        for operation, profile in sorted(latest.items())
    ]


def _stable_source_projection(
    layout: WorkspaceLayout,
    entries: list[BundleEntry],
    paper: Mapping[str, Any],
    expected_fingerprint: str,
) -> dict[str, Any]:
    first = _source_projection(layout, entries, paper, expected_fingerprint)
    second = _source_projection(layout, entries, paper, expected_fingerprint)
    if first == second:
        return first
    return {
        "source_availability": "inaccessible",
        "source_currentness": "changed_during_read",
        "trace_back_available": False,
    }


def _source_projection(
    layout: WorkspaceLayout,
    entries: list[BundleEntry],
    paper: Mapping[str, Any],
    expected_fingerprint: str,
) -> dict[str, Any]:
    candidates: list[tuple[str, str]] = []
    paper_assets = [
        item
        for item in records_of_kind(entries, "source-asset-state")
        if item.get("paper_id") == paper["paper_id"]
        and item.get("asset_role") == "main_pdf"
    ]
    states = sorted(
        (
            item
            for item in paper_assets
            if item.get("source_fingerprint", {}).get("value") == expected_fingerprint
        ),
        key=lambda item: item["revision"],
        reverse=True,
    )
    candidates.extend(
        (item["source_ref"]["root_id"], item["source_ref"]["relative_path"])
        for item in states
    )
    if not paper_assets and paper["source_fingerprint"]["value"] == expected_fingerprint:
        candidates.append((paper["source_ref"]["root_id"], paper["source_ref"]["relative_path"]))
    unique_candidates = list(dict.fromkeys(candidates))
    if not unique_candidates:
        return {
            "source_availability": "missing",
            "source_currentness": "historical_manifestation_unresolved",
            "trace_back_available": False,
        }

    observations = [
        inspect_source_ref(layout, root_id=root_id, relative_path=relative_path)
        for root_id, relative_path in unique_candidates
    ]
    observations_by_ref = {
        (item.source_ref.root_id, item.source_ref.relative_path): item
        for item in observations
    }
    exact_available = any(
        item.availability == "available" and item.live_sha256 == expected_fingerprint
        for item in observations
    )
    if exact_available:
        heads: dict[str, dict[str, Any]] = {}
        for item in paper_assets:
            existing = heads.get(item["source_asset_id"])
            if existing is None or item["revision"] > existing["revision"]:
                heads[item["source_asset_id"]] = item
        current_head_matches = any(
            item["source_fingerprint"]["value"] == expected_fingerprint
            and item["manifestation_status"] == "active"
            and item["availability"] == "available"
            and (
                observation := observations_by_ref.get(
                    (item["source_ref"]["root_id"], item["source_ref"]["relative_path"])
                )
            ) is not None
            and observation.availability == "available"
            and observation.live_sha256 == expected_fingerprint
            for item in heads.values()
        )
        has_changed_head = any(
            item["manifestation_status"] == "change_candidate"
            or (
                item["source_fingerprint"]["value"] == expected_fingerprint
                and item["manifestation_status"] == "active"
                and (
                    observation := observations_by_ref.get(
                        (item["source_ref"]["root_id"], item["source_ref"]["relative_path"])
                    )
                ) is not None
                and observation.availability == "available"
                and observation.live_sha256 != expected_fingerprint
            )
            for item in heads.values()
        )
        if not paper_assets or current_head_matches:
            currentness = "current"
        elif has_changed_head:
            currentness = "stale_source"
        else:
            currentness = "historical_exact"
        return {
            "source_availability": "available",
            "source_currentness": currentness,
            "trace_back_available": True,
        }
    if any(item.availability == "available" for item in observations):
        return {
            "source_availability": "available",
            "source_currentness": "fingerprint_mismatch",
            "trace_back_available": False,
        }
    availability = _public_availability(observations[0].availability)
    return {
        "source_availability": availability,
        "source_currentness": "unavailable",
        "trace_back_available": False,
    }


def _public_availability(value: str) -> str:
    return value if value in {"missing", "inaccessible", "relink_required"} else "inaccessible"


def _materialized_parse_run(entries: list[BundleEntry], paper_id: str) -> str | None:
    pages = records_of_kind(entries, "parsed-page")
    return next((item["parse_run_id"] for item in pages if item["paper_id"] == paper_id), None)


def _parse_projection(
    entries: list[BundleEntry],
    paper_id: str,
    bound_parse_run_id: str | None,
) -> dict[str, Any]:
    pages = [item for item in records_of_kind(entries, "parsed-page") if item["paper_id"] == paper_id]
    materialized = pages[0]["parse_run_id"] if pages else None
    if bound_parse_run_id is None:
        state = "unbound" if materialized is None else "current_unbound"
    elif materialized == bound_parse_run_id:
        state = "current"
    elif materialized is None:
        state = "unavailable"
    else:
        state = "historical_not_materialized"
    return {
        "bound_parse_run_id": bound_parse_run_id,
        "materialized_parse_run_id": materialized,
        "binding_state": state,
        "materialized_page_count": len(pages),
        "materialized_parser": None if not pages else deepcopy(pages[0]["parser"]),
    }


def _question_context(entries: list[BundleEntry], paper_id: str) -> list[dict[str, Any]]:
    result = []
    for mapping in sorted(records_of_kind(entries, "question-mapping"), key=lambda item: item["question_id"]):
        link = next((item for item in mapping["paper_links"] if item["paper_id"] == paper_id), None)
        if link is None:
            continue
        result.append(
            {
                "question_id": mapping["question_id"],
                "question_text": mapping["question_text"],
                "scope": mapping["scope"],
                "mapping_status": mapping["mapping_status"],
                "freshness": "stale_upstream" if mapping_freshness_diagnostics(mapping, entries) else "current",
                "link": deepcopy(link),
            }
        )
    return result


def _evidence_matches(
    entries: list[BundleEntry],
    evidence_id: str,
) -> list[tuple[dict[str, Any], dict[str, Any] | None, dict[str, Any] | None]]:
    matches = []
    for kind, bundle in entries:
        if kind != "primary-semantic-bundle":
            continue
        for revision in bundle["revisions"]:
            matches.extend(
                (evidence, revision, bundle)
                for evidence in revision["evidence"]
                if evidence["evidence_id"] == evidence_id
            )
    matches.extend(
        (record, None, None)
        for kind, record in entries
        if kind == "evidence" and record["evidence_id"] == evidence_id
    )
    return matches


def _session_layout(session: WorkspaceSession) -> WorkspaceLayout:
    if not isinstance(session, WorkspaceSession):
        raise _request_error("/session", "a Core-owned WorkspaceSession is required")
    return session._layout


def _request_error(path: str, message: str) -> ResearchKBError:
    return ResearchKBError(
        Diagnostic(SCHEMA_VALIDATION_FAILED, "reading-request", None, path, message)
    )


__all__ = ["MAX_COMPARE_PAPERS", "ReadingApplicationService"]
