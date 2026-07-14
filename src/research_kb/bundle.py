from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from research_kb.config.loader import load_config
from research_kb.contracts.validator import validate_bundle
from research_kb.errors import ResearchKBError
from research_kb.storage.json_io import read_json_document, read_jsonl
from research_kb.workspace import WorkspaceLayout


BundleEntry = tuple[str, dict[str, Any]]


def load_workspace_entries(
    layout: WorkspaceLayout,
    *,
    overrides: Mapping[Path, Iterable[BundleEntry]] | None = None,
    extra_entries: Iterable[BundleEntry] = (),
) -> list[BundleEntry]:
    resolved_overrides = {
        path.resolve(): list(entries)
        for path, entries in (overrides or {}).items()
    }
    entries: list[BundleEntry] = [
        ("workspace", layout.config.data),
        ("domain-profile", load_config(layout.domain_profile_path, "domain-profile").data),
    ]
    consumed: set[Path] = set()

    def add_jsonl(path: Path, kind: str, id_field: str | None = None) -> None:
        resolved = path.resolve()
        if resolved in resolved_overrides:
            entries.extend(resolved_overrides[resolved])
            consumed.add(resolved)
            return
        entries.extend((kind, record) for record in read_jsonl(path, record_kind=kind, id_field=id_field))

    def add_json(path: Path, kind: str) -> None:
        resolved = path.resolve()
        if resolved in resolved_overrides:
            entries.extend(resolved_overrides[resolved])
            consumed.add(resolved)
            return
        if path.is_file():
            entries.append((kind, read_json_document(path, record_kind=kind)))

    add_jsonl(layout.registry_path, "registry-paper", "paper_id")
    if (layout.knowledge_root / "parse" / "by_paper").exists():
        for path in sorted((layout.knowledge_root / "parse" / "by_paper").glob("*.pages.jsonl")):
            add_jsonl(path, "parsed-page")
    if (layout.knowledge_root / "paper_cards" / "by_paper").exists():
        for path in sorted((layout.knowledge_root / "paper_cards" / "by_paper").glob("*.card.json")):
            add_json(path, "paper-card")
    if (layout.knowledge_root / "evidence" / "by_paper").exists():
        for path in sorted((layout.knowledge_root / "evidence" / "by_paper").glob("*.evidence.jsonl")):
            add_jsonl(path, "evidence", "evidence_id")
    add_jsonl(layout.review_queue_path, "review-queue", "queue_id")
    add_jsonl(layout.process_events_path, "process-event", "event_id")
    add_jsonl(layout.guardian_reports_path, "guardian-report", "guardian_report_id")

    for path, override_entries in resolved_overrides.items():
        if path not in consumed:
            entries.extend(override_entries)
    entries.extend(extra_entries)
    return entries


def validate_workspace_entries(entries: list[BundleEntry], *, actor: str = "stored") -> None:
    diagnostics = validate_bundle(
        {"records": [{"kind": kind, "record": record} for kind, record in entries]},
        actor=actor,
    )
    if diagnostics:
        raise ResearchKBError(diagnostics[0])


def records_of_kind(entries: Iterable[BundleEntry], kind: str) -> list[dict[str, Any]]:
    return [record for entry_kind, record in entries if entry_kind == kind]
