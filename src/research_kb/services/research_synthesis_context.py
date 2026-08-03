from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from research_kb.bundle import load_workspace_entries, records_of_kind, validate_workspace_entries
from research_kb.catalog.models import canonical_digest
from research_kb.errors import SCHEMA_VALIDATION_FAILED, STEP7_BOUNDARY, Diagnostic, ResearchKBError
from research_kb.identifiers import Namespace, validate_id
from research_kb.mutation import MutationRequest
from research_kb.services.research_organization import ResearchOrganizationService
from research_kb.services.step7_candidate import Step7CandidateService
from research_kb.step7_support import STEP7_KIND_TO_TYPE, STEP7_RECORD_KINDS, candidate_freshness, derive_support_closure
from research_kb.workspace import WorkspaceLayout


MAX_CONTEXT_ITEMS = 512
TYPE_TO_KIND = {value: key for key, value in STEP7_KIND_TO_TYPE.items()}


@dataclass(frozen=True, slots=True)
class ResearchSynthesisContext:
    basis: dict[str, Any]
    payload: dict[str, Any]


class ResearchSynthesisContextService:
    def __init__(self, layout: WorkspaceLayout):
        self.layout = layout

    def build(
        self,
        *,
        question_id: str,
        candidate_type: str,
        maintenance_intent: str,
        target_candidate_id: str | None,
        maintenance_goal: str,
        include_review_background: bool,
        effective_content_classes: Iterable[str],
    ) -> ResearchSynthesisContext:
        question_id = validate_id(question_id, Namespace.QUESTION)
        if candidate_type not in TYPE_TO_KIND:
            raise _error("/candidate_type", "unsupported Research Synthesis candidate type")
        if maintenance_intent not in {"append", "replace"}:
            raise _error("/maintenance_intent", "maintenance intent must be append or replace")
        if not isinstance(maintenance_goal, str) or not maintenance_goal.strip() or len(maintenance_goal) > 2000:
            raise _error("/maintenance_goal", "maintenance goal must contain 1 through 2000 characters")
        if maintenance_intent == "append" and target_candidate_id is not None:
            raise _error("/target_candidate_id", "append intent cannot name a target candidate")
        if maintenance_intent == "replace" and target_candidate_id is None:
            raise _error("/target_candidate_id", "replace intent requires a target candidate")
        classes = frozenset(effective_content_classes)
        if "research_synthesis" not in classes:
            raise _error(
                "/effective_content_classes",
                "Research Synthesis drafting requires existing candidate context for duplicate comparison",
            )
        if include_review_background and "review_background" not in classes:
            raise _error("/include_review_background", "Review background was requested outside the effective privacy scope")

        entries = load_workspace_entries(self.layout)
        validate_workspace_entries(entries)
        question = ResearchOrganizationService(self.layout).read_question(question_id)
        if question.get("mapping_status") == "needs_resolution":
            raise _boundary("/question_id", "needs-resolution Question cannot admit Research Synthesis")
        paper_card_base = [
            {
                "paper_id": item["paper_id"],
                "card_unit_ids": list(item["selected_card_unit_ids"]),
            }
            for item in question["paper_links"]
        ]
        review_unit_ids = (
            [
                item["link"]["source_unit_id"]
                for item in question.get("background_links", [])
                if item["link"].get("source_kind") == "review_unit"
            ]
            if include_review_background
            else []
        )
        closure = derive_support_closure(
            entries,
            question_id=question_id,
            paper_card_base=paper_card_base,
            review_background_unit_ids=review_unit_ids,
            record_kind=TYPE_TO_KIND[candidate_type],
        )
        if candidate_type == "synthesis" and len(closure.paper_card_base) < 2:
            raise _boundary("/question_id", "Synthesis drafting requires at least two mapped Primary papers")
        support_count = (
            sum(len(item["card_unit_ids"]) for item in closure.paper_card_base)
            + len(closure.evidence_base)
            + sum(len(item["review_unit_ids"]) for item in closure.review_background_base)
        )
        if support_count > MAX_CONTEXT_ITEMS:
            raise _boundary("/question_id", "Research Synthesis support exceeds the bounded Task item budget")

        candidates = [
            item
            for kind, item in entries
            if kind in STEP7_RECORD_KINDS and item.get("question_id") == question_id
        ]
        candidates.sort(key=lambda item: item["candidate_id"])
        target = None
        if target_candidate_id is not None:
            target_candidate_id = validate_id(target_candidate_id, _candidate_namespace(candidate_type))
            target = next((item for item in candidates if item["candidate_id"] == target_candidate_id), None)
            if target is None or target.get("type") != candidate_type:
                raise _boundary("/target_candidate_id", "target candidate is unavailable or has another type")
        candidate_context = candidates[:MAX_CONTEXT_ITEMS]
        if target is not None and target not in candidate_context:
            candidate_context = sorted(
                [*candidate_context[: MAX_CONTEXT_ITEMS - 1], target],
                key=lambda item: item["candidate_id"],
            )

        cards = {item["paper_id"]: item for item in records_of_kind(entries, "paper-card")}
        evidence = {item["evidence_id"]: item for item in records_of_kind(entries, "evidence")}
        memories = {item["review_memory_id"]: item for item in records_of_kind(entries, "review-memory")}
        primary_support = []
        for item in closure.paper_card_base:
            card = cards[item["paper_id"]]
            selected = set(item["card_unit_ids"])
            primary_support.append(
                {
                    "paper_id": item["paper_id"],
                    "card_units": [
                        deepcopy(unit)
                        for section in card["sections"]
                        for unit in section["units"]
                        if unit["unit_id"] in selected
                    ],
                }
            )
        review_background = []
        for item in closure.review_background_base:
            memory = memories[item["review_memory_id"]]
            selected = set(item["review_unit_ids"])
            review_background.append(
                {
                    **deepcopy(item),
                    "background_only": True,
                    "review_units": [
                        deepcopy(unit)
                        for section in memory.get("sections", [])
                        for unit in section.get("units", [])
                        if unit.get("review_unit_id") in selected
                    ],
                }
            )
        payload = {
            "maintenance_request": {
                "question_id": question_id,
                "candidate_type": candidate_type,
                "maintenance_intent": maintenance_intent,
                "target_candidate_id": target_candidate_id,
                "maintenance_goal": maintenance_goal.strip(),
                "include_review_background": include_review_background,
            },
            "question": question,
            "primary_support": primary_support,
            "canonical_evidence": [
                _evidence_projection(evidence[item]) for item in closure.evidence_base
            ],
            "review_queue_boundaries": [
                {"queue_id": item} for item in closure.review_queue_refs
            ],
            "review_background": review_background,
            "existing_candidates": (
                [
                    {"candidate": deepcopy(item), "freshness": candidate_freshness(item, entries)}
                    for item in candidate_context
                ]
            ),
            "operational_context": {
                "task_kind": "research_synthesis_drafting",
                "agent_allocates_candidate_ids": False,
                "agent_can_approve": False,
                "review_background_is_evidence": False,
                "ordinary_query_writes_synthesis": False,
            },
        }
        basis = {
            **payload["maintenance_request"],
            "question_snapshot": {
                "revision_id": question.get("revision_id"),
                "digest": canonical_digest(question),
            },
            "target_snapshot": None if target is None else {
                "candidate_id": target["candidate_id"],
                "digest": canonical_digest(target),
            },
            "payload_digest": canonical_digest(payload),
        }
        return ResearchSynthesisContext(basis=basis, payload=payload)

    @staticmethod
    def validate_result(result: dict[str, Any], context: ResearchSynthesisContext) -> None:
        request = context.payload["maintenance_request"]
        for field in ("candidate_type", "maintenance_intent", "target_candidate_id"):
            if result.get(field) != request[field]:
                raise _error(f"/staged_result/{field}", "Research Synthesis result does not match the Task")
        if result.get("duplicate_disposition") == "updates_target" and request["maintenance_intent"] != "replace":
            raise _error("/staged_result/duplicate_disposition", "updates_target requires replace intent")
        if result.get("duplicate_disposition") == "distinct" and request["maintenance_intent"] != "append":
            raise _error("/staged_result/duplicate_disposition", "distinct disposition requires append intent")
        payload = result.get("payload")
        if not isinstance(payload, dict) or payload.get("question_id") != request["question_id"]:
            raise _error("/staged_result/payload/question_id", "candidate payload belongs to another Question")
        allowed_primary = {
            (paper["paper_id"], unit["unit_id"])
            for paper in context.payload["primary_support"]
            for unit in paper["card_units"]
        }
        for paper in payload.get("paper_card_base", []):
            if any((paper.get("paper_id"), unit_id) not in allowed_primary for unit_id in paper.get("card_unit_ids", [])):
                raise _boundary("/staged_result/payload/paper_card_base", "candidate references a Primary Unit outside the Task allowlist")
        allowed_review = {
            unit["review_unit_id"]
            for memory in context.payload["review_background"]
            for unit in memory["review_units"]
        }
        if any(item not in allowed_review for item in payload.get("review_background_unit_ids", [])):
            raise _boundary("/staged_result/payload/review_background_unit_ids", "candidate references Review background outside the Task allowlist")
        allowed_candidates = {
            item["candidate"]["candidate_id"] for item in context.payload["existing_candidates"]
        }
        if request["candidate_type"] == "cross_view" and any(
            item not in allowed_candidates for item in payload.get("source_views", [])
        ):
            raise _boundary("/staged_result/payload/source_views", "Cross-View references a candidate outside the Task allowlist")
        mutation = MutationRequest(
            operation=request["maintenance_intent"],
            record_kind=TYPE_TO_KIND[request["candidate_type"]],
            target_record_id=request["target_candidate_id"],
            paper_id=None,
            question_origin="existing_question",
            payload=payload,
        )
        Step7CandidateService._validate_request(mutation)
        Step7CandidateService._validate_payload(mutation)


def _candidate_namespace(candidate_type: str) -> Namespace:
    return {
        "synthesis": Namespace.SYNTHESIS,
        "review_angle": Namespace.REVIEW_ANGLE,
        "insight": Namespace.INSIGHT,
        "cross_view": Namespace.CROSS_VIEW,
    }[candidate_type]


def _evidence_projection(record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        field: deepcopy(record[field])
        for field in (
            "evidence_id",
            "paper_id",
            "claim",
            "evidence_type",
            "quote",
            "source_page",
            "locator",
            "support_scope",
            "what_it_does_not_support",
        )
    }


def _error(path: str, message: str) -> ResearchKBError:
    return ResearchKBError(Diagnostic(SCHEMA_VALIDATION_FAILED, "research-synthesis-request", None, path, message))


def _boundary(path: str, message: str) -> ResearchKBError:
    return ResearchKBError(Diagnostic(STEP7_BOUNDARY, "research-synthesis-request", None, path, message))


__all__ = ["MAX_CONTEXT_ITEMS", "ResearchSynthesisContext", "ResearchSynthesisContextService", "TYPE_TO_KIND"]
