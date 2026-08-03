from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from research_kb.bundle import load_workspace_entries, records_of_kind, validate_workspace_entries
from research_kb.catalog.models import canonical_digest
from research_kb.errors import INVALID_AUTHORITY, SCHEMA_VALIDATION_FAILED, UNRESOLVED_REFERENCE, Diagnostic, ResearchKBError
from research_kb.identity_corrections import project_registry_identity
from research_kb.services.question_screening import QuestionScreeningService
from research_kb.services.research_organization import ResearchOrganizationService
from research_kb.workspace import WorkspaceLayout


@dataclass(frozen=True, slots=True)
class ScreeningProposalContext:
    basis: dict[str, Any]
    payload: dict[str, Any]
    alias_to_criterion_id: dict[str, str]


class ScreeningProposalContextService:
    def __init__(self, layout: WorkspaceLayout):
        self.layout = layout

    def build_criteria(
        self,
        *,
        question_id: str,
        criteria_id: str | None,
        proposal_goal: str,
    ) -> ScreeningProposalContext:
        entries = load_workspace_entries(self.layout)
        validate_workspace_entries(entries)
        question = ResearchOrganizationService(self.layout).read_question(question_id)
        screening = QuestionScreeningService(self.layout)
        active = screening.list_criteria(question_id=question_id)
        if criteria_id is None and active:
            raise _error(INVALID_AUTHORITY, "/criteria_id", "Question already has active criteria; bind its exact identity for revision")
        criteria = None if criteria_id is None else screening.read_criteria(criteria_id)
        if criteria is not None and criteria["question_id"] != question_id:
            raise _error(INVALID_AUTHORITY, "/criteria_id", "criteria belongs to another Question")
        aliases = _criteria_aliases(criteria)
        payload = {
            "proposal_request": {
                "question_id": question_id,
                "criteria_id": criteria_id,
                "proposal_goal": proposal_goal,
            },
            "question_context": _question_payload(question),
            "current_criteria": None if criteria is None else _criteria_payload(criteria, aliases),
            "operational_context": {
                "task_kind": "question_screening_criteria_proposal",
                "criterion_aliases_are_task_local": True,
                "agent_allocates_canonical_ids": False,
                "agent_can_approve": False,
            },
        }
        basis = {
            "question_id": question_id,
            "criteria_id": criteria_id,
            "proposal_goal": proposal_goal,
            "question_snapshot": _question_snapshot(question),
            "criteria_snapshot": None if criteria is None else _criteria_snapshot(criteria),
            "payload_digest": canonical_digest(payload),
        }
        return ScreeningProposalContext(
            basis,
            payload,
            {alias: item["criterion_id"] for alias, item in aliases.items()},
        )

    def build_decision(
        self,
        *,
        question_id: str,
        paper_id: str,
        basis_scope: str,
        include_paper_card: bool,
        effective_content_classes: tuple[str, ...] | list[str],
    ) -> ScreeningProposalContext:
        entries = load_workspace_entries(self.layout)
        validate_workspace_entries(entries)
        question = ResearchOrganizationService(self.layout).read_question(question_id)
        papers = records_of_kind(entries, "registry-paper")
        corrections = records_of_kind(entries, "registry-identity-correction")
        identity = project_registry_identity(papers, corrections).get(paper_id)
        paper = next((item for item in papers if item["paper_id"] == paper_id), None)
        if paper is None or identity is None or identity.get("canonical_paper_id") != paper_id or identity.get("library_status") != "active":
            raise _error(UNRESOLVED_REFERENCE, "/paper_id", "Paper is unavailable or non-canonical")
        screening = QuestionScreeningService(self.layout)
        active = screening.list_criteria(question_id=question_id)
        if len(active) != 1:
            raise _error(UNRESOLVED_REFERENCE, "/question_id", "Question requires exactly one active criteria set")
        criteria = active[0]
        aliases = _criteria_aliases(criteria)
        primary_bundle = next((item for item in records_of_kind(entries, "primary-semantic-bundle") if item["paper_id"] == paper_id), None)
        primary_revision = _active_revision(primary_bundle)
        card = None if primary_revision is None else primary_revision.get("paper_card")
        classes = set(effective_content_classes)
        if include_paper_card and "paper_card_content" not in classes:
            raise _error(INVALID_AUTHORITY, "/include_paper_card", "Paper Card content is outside the effective privacy scope")
        if include_paper_card and card is None:
            raise _error(UNRESOLVED_REFERENCE, "/include_paper_card", "current Paper Card content is unavailable")
        if basis_scope == "metadata" and include_paper_card or basis_scope in {"paper_card", "mixed"} and not include_paper_card:
            raise _error(SCHEMA_VALIDATION_FAILED, "/basis_scope", "basis scope and Paper Card inclusion are inconsistent")
        decision_bundle = next((item for item in records_of_kind(entries, "screening-decision-bundle") if item["question_id"] == question_id and item["paper_id"] == paper_id), None)
        decision_revision = _active_revision(decision_bundle)
        payload = {
            "proposal_request": {
                "question_id": question_id,
                "paper_id": paper_id,
                "basis_scope": basis_scope,
            },
            "question_context": _question_payload(question),
            "criteria": _criteria_payload(criteria, aliases),
            "paper": {
                "paper_id": paper_id,
                "bibliography": paper["bibliography"],
                "paper_card": card if include_paper_card else None,
            },
            "current_decision": None if decision_revision is None else _decision_payload(decision_revision["decision"], aliases),
            "operational_context": {
                "task_kind": "question_screening_decision_proposal",
                "all_criterion_aliases_required": True,
                "agent_can_return_uncertain": True,
                "uncertain_can_be_approved": False,
                "agent_can_approve": False,
            },
        }
        basis = {
            "question_id": question_id,
            "paper_id": paper_id,
            "basis_scope": basis_scope,
            "include_paper_card": include_paper_card,
            "question_snapshot": _question_snapshot(question),
            "paper_snapshot": {
                "paper_digest": canonical_digest(paper),
                "primary_revision_id": None if primary_revision is None else primary_revision["revision_id"],
                "paper_card_digest": None if card is None else canonical_digest(card),
            },
            "criteria_snapshot": _criteria_snapshot(criteria),
            "decision_snapshot": None if decision_revision is None else {
                "decision_id": decision_bundle["decision_id"],
                "revision_id": decision_revision["revision_id"],
                "digest": canonical_digest(decision_revision),
            },
            "payload_digest": canonical_digest(payload),
        }
        return ScreeningProposalContext(
            basis,
            payload,
            {alias: item["criterion_id"] for alias, item in aliases.items()},
        )

    @staticmethod
    def validate_criteria_result(result: Mapping[str, Any], payload: Mapping[str, Any]) -> None:
        allowed = _alias_map(payload.get("current_criteria"))
        seen: set[str] = set()
        for field in ("inclusion_criteria", "exclusion_criteria"):
            for item in result[field]:
                alias = item["source_alias"]
                if alias is not None and (alias not in allowed or alias in seen):
                    raise _error(INVALID_AUTHORITY, f"/{field}/source_alias", "criteria candidate uses an unavailable or duplicate alias")
                if alias is not None and allowed[alias]["kind"] != field:
                    raise _error(INVALID_AUTHORITY, f"/{field}/source_alias", "criteria alias cannot move between inclusion and exclusion")
                if alias is not None:
                    seen.add(alias)

    @staticmethod
    def validate_decision_result(result: Mapping[str, Any], payload: Mapping[str, Any]) -> None:
        expected = set(_alias_map(payload["criteria"]))
        actual = [item["criterion_alias"] for item in result["criterion_dispositions"]]
        if len(actual) != len(set(actual)) or set(actual) != expected:
            raise _error(SCHEMA_VALIDATION_FAILED, "/criterion_dispositions", "decision candidate must close over every task-local criterion alias exactly once")

    @staticmethod
    def translate_criteria_result(
        result: Mapping[str, Any],
        payload: Mapping[str, Any],
        alias_to_criterion_id: Mapping[str, str],
    ) -> dict[str, Any]:
        current = payload.get("current_criteria")

        def translate(items: list[Mapping[str, Any]]) -> list[str | dict[str, str]]:
            return [
                item["text"]
                if item["source_alias"] is None
                else {"criterion_id": alias_to_criterion_id[item["source_alias"]], "text": item["text"]}
                for item in items
            ]

        return {
            "question_id": payload["proposal_request"]["question_id"],
            "title": result["title"],
            "scope": result["scope"],
            "inclusion_criteria": translate(result["inclusion_criteria"]),
            "exclusion_criteria": translate(result["exclusion_criteria"]),
            "notes": result["notes"],
            "status": "active" if current is None else current["status"],
        }

    @staticmethod
    def translate_decision_result(
        result: Mapping[str, Any],
        payload: Mapping[str, Any],
        alias_to_criterion_id: Mapping[str, str],
    ) -> dict[str, Any]:
        criteria = payload["criteria"]
        return {
            "question_id": payload["proposal_request"]["question_id"],
            "paper_id": payload["proposal_request"]["paper_id"],
            "outcome": result["outcome"],
            "criteria_revision_id": criteria["revision_id"],
            "criteria_digest": criteria["criteria_digest"],
            "criterion_dispositions": [
                {
                    "criterion_id": alias_to_criterion_id[item["criterion_alias"]],
                    "disposition": item["disposition"],
                    "rationale": item["rationale"],
                }
                for item in result["criterion_dispositions"]
            ],
            "basis_scope": payload["proposal_request"]["basis_scope"],
            "rationale": result["rationale"],
            "known_limitations": result["known_limitations"],
        }


def _question_payload(question: Mapping[str, Any]) -> dict[str, Any]:
    return {key: question.get(key) for key in ("question_id", "question_text", "scope", "mapping_status", "revision_id")}


def _question_snapshot(question: Mapping[str, Any]) -> dict[str, Any]:
    return {"revision_id": question.get("revision_id"), "digest": canonical_digest(question)}


def _criteria_snapshot(criteria: Mapping[str, Any]) -> dict[str, Any]:
    return {"criteria_id": criteria["criteria_id"], "revision_id": criteria["revision_id"], "digest": criteria["criteria_digest"]}


def _criteria_aliases(criteria: Mapping[str, Any] | None) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    index = 1
    if criteria is not None:
        for kind in ("inclusion_criteria", "exclusion_criteria"):
            for item in criteria[kind]:
                result[f"criterion_alias_{index:03d}"] = {"criterion_id": item["criterion_id"], "kind": kind, "text": item["text"]}
                index += 1
    return result


def _criteria_payload(criteria: Mapping[str, Any], aliases: Mapping[str, Mapping[str, str]]) -> dict[str, Any]:
    by_id = {item["criterion_id"]: alias for alias, item in aliases.items()}
    return {
        "criteria_id": criteria["criteria_id"],
        "revision_id": criteria["revision_id"],
        "criteria_digest": criteria["criteria_digest"],
        "title": criteria["title"],
        "scope": criteria["scope"],
        "inclusion_criteria": [{"alias": by_id[item["criterion_id"]], "text": item["text"]} for item in criteria["inclusion_criteria"]],
        "exclusion_criteria": [{"alias": by_id[item["criterion_id"]], "text": item["text"]} for item in criteria["exclusion_criteria"]],
        "notes": criteria["notes"],
        "status": criteria["status"],
    }


def _alias_map(criteria_payload: Mapping[str, Any] | None) -> dict[str, dict[str, str]]:
    if criteria_payload is None:
        return {}
    return {item["alias"]: {"kind": field, "text": item["text"]} for field in ("inclusion_criteria", "exclusion_criteria") for item in criteria_payload[field]}


def _decision_payload(
    decision: Mapping[str, Any],
    aliases: Mapping[str, Mapping[str, str]],
) -> dict[str, Any]:
    by_id = {item["criterion_id"]: alias for alias, item in aliases.items()}
    return {
        "outcome": decision["outcome"],
        "criterion_dispositions": [
            {
                "criterion_alias": by_id[item["criterion_id"]],
                "disposition": item["disposition"],
                "rationale": item["rationale"],
            }
            for item in decision["criterion_dispositions"]
        ],
        "basis_scope": decision["basis_scope"],
        "rationale": decision["rationale"],
        "known_limitations": decision["known_limitations"],
    }


def _active_revision(bundle: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if bundle is None:
        return None
    active_id = bundle.get("active_revision_id")
    return next((dict(item) for item in bundle.get("revisions", []) if item.get("revision_id") == active_id), None)


def _error(code: str, path: str, message: str) -> ResearchKBError:
    return ResearchKBError(Diagnostic(code, "screening-proposal-context", None, path, message))


__all__ = ["ScreeningProposalContext", "ScreeningProposalContextService"]
