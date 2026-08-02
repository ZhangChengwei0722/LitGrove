from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from research_kb.bundle import load_workspace_entries, records_of_kind, validate_workspace_entries
from research_kb.catalog.models import canonical_digest
from research_kb.errors import DUPLICATE_ID, SCHEMA_VALIDATION_FAILED, UNRESOLVED_REFERENCE, Diagnostic, ResearchKBError
from research_kb.identifiers import Namespace, validate_id
from research_kb.services.research_organization import ResearchOrganizationService
from research_kb.workspace import WorkspaceLayout


TARGET_KINDS = frozenset({"direction", "field_map_entry", "question"})
MAX_PAPERS = 25
MAX_CONTEXT_TARGETS = 100
_PRIMARY_STATUSES = frozenset({"grounded", "revised", "interpretive", "background_only"})


@dataclass(frozen=True, slots=True)
class OrganizationProposalContext:
    basis: dict[str, Any]
    payload: dict[str, Any]


class OrganizationProposalContextService:
    def __init__(self, layout: WorkspaceLayout):
        self.layout = layout

    def build(
        self,
        *,
        target_kind: str,
        target_id: str | None,
        proposal_goal: str,
        paper_ids: Iterable[str],
        include_review_background: bool,
        effective_content_classes: Iterable[str],
    ) -> OrganizationProposalContext:
        kind, normalized_target, goal, selected = _normalize_request(
            target_kind,
            target_id,
            proposal_goal,
            paper_ids,
        )
        classes = frozenset(effective_content_classes)
        if include_review_background and "review_background" not in classes:
            raise _error(
                "/include_review_background",
                "Review background was requested outside the effective privacy scope",
            )

        entries = load_workspace_entries(self.layout)
        validate_workspace_entries(entries)
        papers = {item["paper_id"]: item for item in records_of_kind(entries, "registry-paper")}
        missing = [paper_id for paper_id in selected if paper_id not in papers]
        if missing:
            raise ResearchKBError(
                Diagnostic(
                    UNRESOLVED_REFERENCE,
                    "registry-paper",
                    missing[0],
                    "/paper_ids",
                    "organization proposal paper is not registered",
                )
            )

        primary_bundles = {
            item["paper_id"]: item for item in records_of_kind(entries, "primary-semantic-bundle")
        }
        review_bundles = {
            item["paper_id"]: item for item in records_of_kind(entries, "review-semantic-bundle")
        }
        primary_payload: list[dict[str, Any]] = []
        review_payload: list[dict[str, Any]] = []
        snapshots: list[dict[str, Any]] = []
        for paper_id in selected:
            primary_revision = _active_revision(primary_bundles.get(paper_id))
            review_revision = _active_revision(review_bundles.get(paper_id))
            card = primary_revision.get("paper_card") if primary_revision is not None else None
            memory = review_revision.get("review_memory") if review_revision is not None else None
            evidence = (
                list(primary_revision.get("evidence", []))
                if primary_revision is not None
                else []
            )
            snapshots.append(
                {
                    "paper_id": paper_id,
                    "paper_record_digest": canonical_digest(papers[paper_id]),
                    "primary_revision_id": None if primary_revision is None else primary_revision["revision_id"],
                    "primary_revision_digest": None if primary_revision is None else canonical_digest(primary_revision),
                    "paper_card_digest": None if card is None else canonical_digest(card),
                    "evidence_digests": [
                        {"evidence_id": item["evidence_id"], "evidence_digest": canonical_digest(item)}
                        for item in sorted(evidence, key=lambda item: item["evidence_id"])
                    ],
                    "review_revision_id": None if review_revision is None else review_revision["revision_id"],
                    "review_revision_digest": None if review_revision is None else canonical_digest(review_revision),
                    "review_memory_digest": None if memory is None else canonical_digest(memory),
                }
            )
            primary_payload.append(
                _primary_payload(
                    papers[paper_id],
                    card,
                    evidence,
                    revision_id=None if primary_revision is None else primary_revision["revision_id"],
                    include_metadata="metadata" in classes,
                    include_evidence="canonical_evidence" in classes,
                )
            )
            if include_review_background and memory is not None:
                review_payload.append(
                    _review_payload(
                        papers[paper_id],
                        memory,
                        revision_id=None if review_revision is None else review_revision["revision_id"],
                        include_metadata="metadata" in classes,
                    )
                )

        if not any(item["card_units"] for item in primary_payload) and not any(
            item["review_units"] for item in review_payload
        ):
            raise _error(
                "/paper_ids",
                "organization proposal requires at least one current admissible semantic Unit",
            )

        organization = ResearchOrganizationService(self.layout)
        target = _target_projection(organization, kind, normalized_target)
        organization_context = _organization_context(organization, selected, target)
        target_snapshot = None
        if target is not None:
            target_snapshot = {
                "target_id": normalized_target,
                "revision_id": target.get("revision_id"),
                "target_digest": canonical_digest(target),
            }
        payload = {
            "proposal_request": {
                "target_kind": kind,
                "target_id": normalized_target,
                "proposal_goal": goal,
                "paper_ids": selected,
                "include_review_background": include_review_background,
            },
            "primary_papers": primary_payload,
            "review_background": review_payload,
            "organization_context": organization_context,
            "operational_context": {
                "task_kind": "organization_proposal",
                "one_target_only": True,
                "agent_allocates_canonical_ids": False,
                "agent_can_approve": False,
                "review_content_policy": "background_only",
                "unresolved_conflicts_block_approval": True,
            },
        }
        basis = {
            "target_kind": kind,
            "target_id": normalized_target,
            "proposal_goal": goal,
            "paper_ids": selected,
            "include_review_background": include_review_background,
            "target_snapshot": target_snapshot,
            "paper_snapshots": snapshots,
            "organization_context_digest": canonical_digest(organization_context),
            "payload_digest": canonical_digest(payload),
        }
        return OrganizationProposalContext(basis=basis, payload=payload)

    @staticmethod
    def validate_result(result: dict[str, Any], payload: dict[str, Any]) -> None:
        request = payload["proposal_request"]
        if result.get("target_kind") != request["target_kind"] or result.get("target_id") != request["target_id"]:
            raise _error("/staged_result/target_kind", "organization result target does not match the Task")
        primary = {
            (item["paper_id"], unit["unit_id"])
            for item in payload["primary_papers"]
            for unit in item["card_units"]
        }
        review = {
            (item["paper_id"], item["review_memory_id"], unit["review_unit_id"])
            for item in payload["review_background"]
            for unit in item["review_units"]
        }
        proposal = result.get("proposal", {})
        links = list(proposal.get("unit_links", [])) + list(proposal.get("background_links", []))
        for index, link in enumerate(links):
            if link.get("source_kind") == "primary":
                allowed = (link.get("paper_id"), link.get("unit_id")) in primary
            else:
                allowed = (
                    link.get("paper_id"),
                    link.get("review_memory_id"),
                    link.get("unit_id"),
                ) in review
            if not allowed:
                raise _error(
                    f"/staged_result/proposal/links/{index}",
                    "organization result references a Unit outside the Task allowlist",
                )
        for index, factual in enumerate(proposal.get("factual_links", [])):
            paper_id = factual.get("paper_id")
            if any((paper_id, unit_id) not in primary for unit_id in factual.get("selected_card_unit_ids", [])):
                raise _error(
                    f"/staged_result/proposal/factual_links/{index}",
                    "Question result references a Card Unit outside the Task allowlist",
                )
        direction_ids = {
            item["direction_id"] for item in payload["organization_context"]["available_directions"]
        }
        if any(item not in direction_ids for item in proposal.get("direction_refs", [])):
            raise _error(
                "/staged_result/proposal/direction_refs",
                "Field Map result references a Direction outside the Task allowlist",
            )


def _normalize_request(
    target_kind: object,
    target_id: object,
    proposal_goal: object,
    paper_ids: Iterable[str],
) -> tuple[str, str | None, str, list[str]]:
    if target_kind not in TARGET_KINDS:
        raise _error("/target_kind", "unsupported organization target kind")
    kind = str(target_kind)
    namespace = {
        "direction": Namespace.DIRECTION,
        "field_map_entry": Namespace.FIELD_MAP,
        "question": Namespace.QUESTION,
    }[kind]
    normalized_target = None if target_id is None else validate_id(str(target_id), namespace)
    if not isinstance(proposal_goal, str) or not proposal_goal.strip() or len(proposal_goal) > 2000:
        raise _error("/proposal_goal", "proposal goal must contain 1 through 2000 characters")
    selected = list(paper_ids)
    if not 1 <= len(selected) <= MAX_PAPERS or not all(isinstance(item, str) for item in selected):
        raise _error("/paper_ids", f"organization proposal requires 1 through {MAX_PAPERS} paper IDs")
    if len(selected) != len(set(selected)):
        raise ResearchKBError(
            Diagnostic(DUPLICATE_ID, "organization-proposal-request", None, "/paper_ids", "paper IDs must be unique")
        )
    for paper_id in selected:
        validate_id(paper_id, Namespace.PAPER)
    return kind, normalized_target, proposal_goal.strip(), selected


def _target_projection(
    service: ResearchOrganizationService,
    target_kind: str,
    target_id: str | None,
) -> dict[str, Any] | None:
    if target_id is None:
        return None
    if target_kind == "direction":
        return service.read_direction(target_id)
    if target_kind == "field_map_entry":
        return service.read_field_map_entry(target_id)
    return service.read_question(target_id)


def _organization_context(
    service: ResearchOrganizationService,
    paper_ids: list[str],
    target: dict[str, Any] | None,
) -> dict[str, Any]:
    selected = set(paper_ids)
    directions = service.list_directions()
    fields = service.list_field_map_entries()
    related_directions = [item for item in directions if any(link.get("paper_id") in selected for link in item.get("links", []))]
    related_fields = [item for item in fields if any(link.get("paper_id") in selected for link in item.get("links", []))]
    required_direction_ids = {
        item["direction_id"] for item in (target or {}).get("direction_refs", [])
    }
    prioritized_directions = [
        *[item for item in directions if item["direction_id"] in required_direction_ids],
        *[item for item in directions if item["direction_id"] not in required_direction_ids],
    ]
    return {
        "current_target": target,
        "available_directions": [
            {key: item[key] for key in ("direction_id", "name", "scope", "status", "revision_id")}
            for item in prioritized_directions[:MAX_CONTEXT_TARGETS]
        ],
        "related_directions": related_directions[:MAX_CONTEXT_TARGETS],
        "related_field_map_entries": related_fields[:MAX_CONTEXT_TARGETS],
        "context_truncated": any(
            len(items) > MAX_CONTEXT_TARGETS
            for items in (directions, related_directions, related_fields)
        ),
    }


def _primary_payload(
    paper: dict[str, Any],
    card: dict[str, Any] | None,
    evidence: list[dict[str, Any]],
    *,
    revision_id: str | None,
    include_metadata: bool,
    include_evidence: bool,
) -> dict[str, Any]:
    units = []
    if card is not None:
        units = [
            {
                key: unit[key]
                for key in (
                    "unit_id",
                    "section_id",
                    "statement",
                    "statement_type",
                    "grounding_status",
                    "evidence_ids",
                    "boundary_refs",
                )
            }
            for section in card.get("sections", [])
            for unit in section.get("units", [])
            if unit.get("grounding_status") in _PRIMARY_STATUSES
        ]
    result = {
        "paper_id": paper["paper_id"],
        "revision_id": revision_id,
        "card_units": units,
        "evidence": [
            {
                key: item[key]
                for key in ("evidence_id", "claim", "evidence_type", "support_scope")
            }
            for item in evidence
            if item.get("canonical") is True
        ] if include_evidence else [],
    }
    if include_metadata:
        result["bibliography"] = paper.get("bibliography", {})
    return result


def _review_payload(
    paper: dict[str, Any],
    memory: dict[str, Any],
    *,
    revision_id: str | None,
    include_metadata: bool,
) -> dict[str, Any]:
    result = {
        "paper_id": paper["paper_id"],
        "review_memory_id": memory["review_memory_id"],
        "revision_id": revision_id,
        "background_only": True,
        "review_units": [
            {
                "review_unit_id": unit["review_unit_id"],
                "section_id": unit["section_id"],
                "content": unit["content"],
                "unit_type": unit["unit_type"],
                "background_only": True,
            }
            for section in memory.get("sections", [])
            for unit in section.get("units", [])
            if unit.get("background_only") is True
            and unit.get("can_enter_canonical_evidence") is False
            and unit.get("not_fact") is True
        ],
    }
    if include_metadata:
        result["bibliography"] = paper.get("bibliography", {})
    return result


def _active_revision(bundle: dict[str, Any] | None) -> dict[str, Any] | None:
    if bundle is None:
        return None
    return next(
        (item for item in bundle.get("revisions", []) if item.get("revision_id") == bundle.get("active_revision_id")),
        None,
    )


def _error(path: str, message: str) -> ResearchKBError:
    return ResearchKBError(
        Diagnostic(SCHEMA_VALIDATION_FAILED, "organization-proposal-request", None, path, message)
    )


__all__ = [
    "MAX_PAPERS",
    "OrganizationProposalContext",
    "OrganizationProposalContextService",
    "TARGET_KINDS",
]
