from __future__ import annotations

from typing import Any

from research_kb.bundle import load_workspace_entries, records_of_kind, validate_workspace_entries
from research_kb.errors import UNRESOLVED_REFERENCE, Diagnostic, ResearchKBError
from research_kb.identifiers import Namespace, validate_id
from research_kb.services.question_view import QuestionReadingViewService
from research_kb.workspace import WorkspaceLayout


class QuestionQueryService:
    def __init__(self, layout: WorkspaceLayout):
        self.layout = layout

    def list(self) -> dict[str, Any]:
        entries = load_workspace_entries(self.layout)
        validate_workspace_entries(entries)
        questions = sorted(
            records_of_kind(entries, "question-mapping"),
            key=lambda item: item["question_id"],
        )
        return {
            "status": "success",
            "questions": [
                {
                    "question_id": item["question_id"],
                    "question_text": item["question_text"],
                    "scope": item["scope"],
                    "mapping_status": item["mapping_status"],
                    "linked_paper_count": len(item["paper_links"]),
                    "updated_at": item["updated_at"],
                }
                for item in questions
            ],
        }

    def show(self, question_id: str) -> dict[str, Any]:
        normalized_id = validate_id(question_id, Namespace.QUESTION)
        entries = load_workspace_entries(self.layout)
        validate_workspace_entries(entries)
        question = next(
            (
                item
                for item in records_of_kind(entries, "question-mapping")
                if item["question_id"] == normalized_id
            ),
            None,
        )
        if question is None:
            raise ResearchKBError(
                Diagnostic(
                    UNRESOLVED_REFERENCE,
                    "question-mapping",
                    normalized_id,
                    "/question_id",
                    "question mapping does not exist",
                )
            )
        return {"status": "success", "question": question}


class WorkspaceQuestionReadingViewService:
    def __init__(self, layout: WorkspaceLayout):
        self.layout = layout

    def render(self, question_id: str) -> bytes:
        entries = load_workspace_entries(self.layout)
        return QuestionReadingViewService(entries).render(question_id)
