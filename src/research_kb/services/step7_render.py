from __future__ import annotations

from research_kb.bundle import load_workspace_entries
from research_kb.services.step7_view import Step7ReadingViewService
from research_kb.workspace import WorkspaceLayout


class WorkspaceStep7ReadingViewService:
    def __init__(self, layout: WorkspaceLayout):
        self.layout = layout

    def render(self, question_id: str) -> bytes:
        entries = load_workspace_entries(self.layout)
        return Step7ReadingViewService(entries).render(question_id)
