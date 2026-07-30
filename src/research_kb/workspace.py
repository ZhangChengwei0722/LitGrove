from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from research_kb.config.loader import ConfigDocument
from research_kb.errors import PATH_ESCAPE, Diagnostic, ResearchKBError
from research_kb.paths import SourceRef, make_source_ref, resolve_source_ref
from research_kb.workspace_validation import WorkspaceContext, validate_initialized_workspace


@dataclass(frozen=True, slots=True)
class WorkspaceLayout:
    config: ConfigDocument
    knowledge_root: Path
    domain_profile_path: Path
    source_roots: dict[str, Path]
    local_inbox: Path

    @classmethod
    def load(cls, config_path: Path) -> "WorkspaceLayout":
        context = validate_initialized_workspace(config_path).require_valid()
        return cls._from_context(context)

    @classmethod
    def _from_context(cls, context: WorkspaceContext) -> "WorkspaceLayout":
        return cls(
            context.config,
            context.knowledge_root,
            context.domain_profile.path,
            context.source_roots,
            context.local_inbox,
        )

    @property
    def workspace_id(self) -> str:
        return self.config.data["workspace"]["id"]

    @property
    def registry_path(self) -> Path:
        return self.knowledge_root / "registry" / "papers.jsonl"

    @property
    def source_assets_path(self) -> Path:
        return self.knowledge_root / "registry" / "source_assets.jsonl"

    @property
    def identity_corrections_path(self) -> Path:
        return self.knowledge_root / "registry" / "identity_corrections.jsonl"

    @property
    def review_queue_path(self) -> Path:
        return self.knowledge_root / "review_queue" / "items.jsonl"

    @property
    def process_events_path(self) -> Path:
        return self.knowledge_root / "process" / "events.jsonl"

    @property
    def pipeline_jobs_path(self) -> Path:
        return self.knowledge_root / "process" / "jobs.jsonl"

    @property
    def source_adequacy_path(self) -> Path:
        return self.knowledge_root / "process" / "source_adequacy.jsonl"

    @property
    def guardian_reports_path(self) -> Path:
        return self.knowledge_root / "guardian" / "reports.jsonl"

    @property
    def guardian_finding_dispositions_path(self) -> Path:
        return self.knowledge_root / "guardian" / "finding_dispositions.jsonl"

    @property
    def question_mappings_path(self) -> Path:
        return self.knowledge_root / "questions" / "mappings.jsonl"

    @property
    def discovery_candidates_path(self) -> Path:
        return self.knowledge_root / "discovery" / "candidates.jsonl"

    @property
    def lock_path(self) -> Path:
        return self.knowledge_root / ".research-kb" / "locks" / "workspace.lock"

    @property
    def marker_path(self) -> Path:
        return self.knowledge_root / ".research-kb" / "workspace.json"

    @property
    def transactions_root(self) -> Path:
        return self.knowledge_root / ".research-kb" / "transactions"

    def parse_path(self, paper_id: str) -> Path:
        return self.knowledge_root / "parse" / "by_paper" / f"{paper_id}.pages.jsonl"

    def paper_card_path(self, paper_id: str) -> Path:
        return self.knowledge_root / "paper_cards" / "by_paper" / f"{paper_id}.card.json"

    def evidence_path(self, paper_id: str) -> Path:
        return self.knowledge_root / "evidence" / "by_paper" / f"{paper_id}.evidence.jsonl"

    def review_memory_path(self, paper_id: str) -> Path:
        return self.knowledge_root / "review_memories" / "by_paper" / f"{paper_id}.review.json"

    def step7_store_path(self, record_kind: str) -> Path:
        filenames = {
            "step7-synthesis": "synthesis.jsonl",
            "step7-review-angle": "review_angles.jsonl",
            "step7-insight": "insights.jsonl",
            "step7-cross-view": "cross_views.jsonl",
        }
        try:
            filename = filenames[record_kind]
        except KeyError as error:
            raise ValueError(f"unsupported Step 7 record kind: {record_kind}") from error
        return self.knowledge_root / "step7" / filename

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
