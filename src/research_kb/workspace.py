from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from research_kb.config.loader import ConfigDocument, load_config, resolve_config_path
from research_kb.errors import PATH_ESCAPE, Diagnostic, ResearchKBError
from research_kb.paths import SourceRef, make_source_ref, resolve_source_ref


@dataclass(frozen=True, slots=True)
class WorkspaceLayout:
    config: ConfigDocument
    knowledge_root: Path
    domain_profile_path: Path
    source_roots: dict[str, Path]

    @classmethod
    def load(cls, config_path: Path) -> "WorkspaceLayout":
        document = load_config(config_path, "workspace")
        workspace = document.data["workspace"]
        knowledge_root = resolve_config_path(document, workspace["knowledge_root"])
        domain_profile_path = resolve_config_path(document, workspace["domain_profile"])
        roots = {
            item["root_id"]: resolve_config_path(document, item["path"])
            for item in workspace["source_roots"]
        }
        for root in roots.values():
            if knowledge_root == root or knowledge_root.is_relative_to(root) or root.is_relative_to(knowledge_root):
                raise ResearchKBError(
                    Diagnostic(PATH_ESCAPE, "workspace", workspace["id"], "/workspace/source_roots", "knowledge_root and source roots must not overlap")
                )
        return cls(document, knowledge_root, domain_profile_path, roots)

    @property
    def workspace_id(self) -> str:
        return self.config.data["workspace"]["id"]

    @property
    def registry_path(self) -> Path:
        return self.knowledge_root / "registry" / "papers.jsonl"

    @property
    def review_queue_path(self) -> Path:
        return self.knowledge_root / "review_queue" / "items.jsonl"

    @property
    def process_events_path(self) -> Path:
        return self.knowledge_root / "process" / "events.jsonl"

    @property
    def guardian_reports_path(self) -> Path:
        return self.knowledge_root / "guardian" / "reports.jsonl"

    @property
    def lock_path(self) -> Path:
        return self.knowledge_root / ".research-kb" / "locks" / "workspace.lock"

    @property
    def transactions_root(self) -> Path:
        return self.knowledge_root / ".research-kb" / "transactions"

    def parse_path(self, paper_id: str) -> Path:
        return self.knowledge_root / "parse" / "by_paper" / f"{paper_id}.pages.jsonl"

    def paper_card_path(self, paper_id: str) -> Path:
        return self.knowledge_root / "paper_cards" / "by_paper" / f"{paper_id}.card.json"

    def evidence_path(self, paper_id: str) -> Path:
        return self.knowledge_root / "evidence" / "by_paper" / f"{paper_id}.evidence.jsonl"

    def journal_path(self, event_id: str) -> Path:
        return self.transactions_root / f"{event_id}.json"

    def ensure_writable_target(self, path: Path) -> Path:
        resolved = path.resolve()
        if not resolved.is_relative_to(self.knowledge_root):
            raise ResearchKBError(
                Diagnostic(PATH_ESCAPE, "workspace", self.workspace_id, "", "write target is outside knowledge_root")
            )
        if any(resolved == root or resolved.is_relative_to(root) for root in self.source_roots.values()):
            raise ResearchKBError(
                Diagnostic(PATH_ESCAPE, "workspace", self.workspace_id, "", "write target overlaps a source root")
            )
        return resolved

    def target_relative_path(self, path: Path) -> str:
        return self.ensure_writable_target(path).relative_to(self.knowledge_root).as_posix()

    def resolve_source(self, root_id: str, relative_path: str) -> tuple[SourceRef, Path]:
        if root_id not in self.source_roots:
            raise ResearchKBError(
                Diagnostic(PATH_ESCAPE, "source-ref", None, "/root_id", "source root is not declared by workspace")
            )
        source_ref = make_source_ref(root_id, relative_path)
        return source_ref, resolve_source_ref(self.source_roots[root_id], source_ref)
