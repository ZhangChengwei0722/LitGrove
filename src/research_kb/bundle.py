from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from research_kb.config.loader import load_config
from research_kb.contracts.validator import validate_bundle
from research_kb.errors import WORKSPACE_LAYOUT_CONFLICT, Diagnostic, ResearchKBError
from research_kb.primary_bundles import active_primary_entries
from research_kb.review_bundles import active_review_entries
from research_kb.organization_bundles import expand_active_organization_entries
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

    def add_json(path: Path, kind: str, *, expected_paper_id: str | None = None) -> None:
        resolved = path.resolve()
        if resolved in resolved_overrides:
            override_entries = resolved_overrides[resolved]
            if expected_paper_id is not None:
                _require_paper_filename_binding(override_entries, kind, expected_paper_id)
            entries.extend(override_entries)
            consumed.add(resolved)
            return
        if path.is_file():
            record = read_json_document(path, record_kind=kind)
            if expected_paper_id is not None:
                _require_paper_filename_binding([(kind, record)], kind, expected_paper_id)
            entries.append((kind, record))

    add_jsonl(layout.registry_path, "registry-paper", "paper_id")
    add_jsonl(
        layout.source_assets_path,
        "source-asset-state",
        "source_asset_state_id",
    )
    add_jsonl(
        layout.identity_corrections_path,
        "registry-identity-correction",
        "correction_id",
    )
    if (layout.knowledge_root / "parse" / "by_paper").exists():
        for path in sorted((layout.knowledge_root / "parse" / "by_paper").glob("*.pages.jsonl")):
            add_jsonl(path, "parsed-page")
    if (layout.knowledge_root / "paper_cards" / "by_paper").exists():
        for path in sorted((layout.knowledge_root / "paper_cards" / "by_paper").glob("*.card.json")):
            add_json(path, "paper-card")
    if (layout.knowledge_root / "evidence" / "by_paper").exists():
        for path in sorted((layout.knowledge_root / "evidence" / "by_paper").glob("*.evidence.jsonl")):
            add_jsonl(path, "evidence", "evidence_id")
    if (layout.knowledge_root / "review_memories" / "by_paper").exists():
        for path in sorted((layout.knowledge_root / "review_memories" / "by_paper").glob("*.review.json")):
            add_json(
                path,
                "review-memory",
                expected_paper_id=path.name[: -len(".review.json")],
            )
    if (layout.knowledge_root / "primary_bundles" / "by_paper").exists():
        for path in sorted((layout.knowledge_root / "primary_bundles" / "by_paper").glob("*.primary.json")):
            resolved = path.resolve()
            if resolved in resolved_overrides:
                override_entries = resolved_overrides[resolved]
                bundles = [record for kind, record in override_entries if kind == "primary-semantic-bundle"]
                if len(bundles) != 1:
                    raise ResearchKBError(
                        Diagnostic(WORKSPACE_LAYOUT_CONFLICT, "primary-semantic-bundle", None, "", "Primary bundle override must contain one bundle")
                    )
                bundle = bundles[0]
                consumed.add(resolved)
            else:
                bundle = read_json_document(path, record_kind="primary-semantic-bundle")
            _require_paper_filename_binding(
                [("primary-semantic-bundle", bundle)],
                "primary-semantic-bundle",
                path.name[: -len(".primary.json")],
            )
            entries.append(("primary-semantic-bundle", bundle))
    if (layout.knowledge_root / "review_bundles" / "by_paper").exists():
        for path in sorted((layout.knowledge_root / "review_bundles" / "by_paper").glob("*.review-bundle.json")):
            resolved = path.resolve()
            if resolved in resolved_overrides:
                override_entries = resolved_overrides[resolved]
                bundles = [record for kind, record in override_entries if kind == "review-semantic-bundle"]
                if len(bundles) != 1:
                    raise ResearchKBError(
                        Diagnostic(WORKSPACE_LAYOUT_CONFLICT, "review-semantic-bundle", None, "", "Review bundle override must contain one bundle")
                    )
                bundle = bundles[0]
                consumed.add(resolved)
            else:
                bundle = read_json_document(path, record_kind="review-semantic-bundle")
            _require_paper_filename_binding(
                [("review-semantic-bundle", bundle)],
                "review-semantic-bundle",
                path.name[: -len(".review-bundle.json")],
            )
            entries.append(("review-semantic-bundle", bundle))
    for root, pattern, kind, id_field in (
        (layout.knowledge_root / "organization" / "directions" / "by_id", "*.direction-bundle.json", "direction-bundle", "direction_id"),
        (layout.knowledge_root / "organization" / "field_map" / "by_id", "*.field-map-bundle.json", "field-map-bundle", "field_map_entry_id"),
        (layout.knowledge_root / "organization" / "questions" / "by_id", "*.question-revision-bundle.json", "question-revision-bundle", "question_id"),
        (layout.knowledge_root / "organization" / "tags" / "by_id", "*.tag-bundle.json", "tag-bundle", "tag_id"),
        (layout.knowledge_root / "organization" / "tag_links" / "by_id", "*.tag-link-bundle.json", "tag-link-bundle", "tag_link_id"),
        (layout.knowledge_root / "organization" / "screening_criteria" / "by_id", "*.screening-criteria-bundle.json", "screening-criteria-bundle", "criteria_id"),
        (layout.knowledge_root / "organization" / "screening_decisions" / "by_id", "*.screening-decision-bundle.json", "screening-decision-bundle", "decision_id"),
    ):
        if not root.exists():
            continue
        for path in sorted(root.glob(pattern)):
            resolved = path.resolve()
            if resolved in resolved_overrides:
                override_entries = resolved_overrides[resolved]
                bundles = [record for entry_kind, record in override_entries if entry_kind == kind]
                if len(bundles) != 1:
                    raise ResearchKBError(
                        Diagnostic(WORKSPACE_LAYOUT_CONFLICT, kind, None, "", "organization bundle override must contain one bundle")
                    )
                record = bundles[0]
                consumed.add(resolved)
            else:
                record = read_json_document(path, record_kind=kind)
            expected_id = path.name[: -len(pattern[1:])]
            if record.get(id_field) != expected_id:
                raise ResearchKBError(
                    Diagnostic(WORKSPACE_LAYOUT_CONFLICT, kind, expected_id, f"/{id_field}", "store filename does not match contained target ID")
                )
            entries.append((kind, record))
    add_jsonl(layout.review_queue_path, "review-queue", "queue_id")
    add_jsonl(layout.question_mappings_path, "question-mapping", "question_id")
    add_jsonl(layout.discovery_candidates_path, "discovery-candidate", "candidate_id")
    for kind in (
        "step7-synthesis",
        "step7-review-angle",
        "step7-insight",
        "step7-cross-view",
    ):
        add_jsonl(layout.step7_store_path(kind), kind, "candidate_id")
    if layout.operational_archives_root.exists():
        for path in sorted(layout.operational_archives_root.glob("*/manifest.json")):
            entries.append(
                (
                    "operational-archive-manifest",
                    read_json_document(path, record_kind="operational-archive-manifest"),
                )
            )
    add_jsonl(layout.process_events_path, "process-event", "event_id")
    add_jsonl(layout.pipeline_jobs_path, "pipeline-job-state", "state_id")
    add_jsonl(layout.maintenance_work_path, "maintenance-work", "maintenance_id")
    if layout.backup_receipts_root.exists():
        for path in sorted(layout.backup_receipts_root.glob("*.json")):
            entries.append(("backup-local-receipt", read_json_document(path, record_kind="backup-local-receipt")))
    if layout.restore_receipts_root.exists():
        for path in sorted(layout.restore_receipts_root.glob("*.json")):
            entries.append(("restore-receipt", read_json_document(path, record_kind="restore-receipt")))
    add_jsonl(
        layout.source_adequacy_path,
        "source-adequacy-profile",
        "profile_id",
    )
    add_jsonl(
        layout.agent_tasks_path,
        "agent-task-state",
        "state_id",
    )
    add_jsonl(layout.guardian_reports_path, "guardian-report", "guardian_report_id")
    add_jsonl(
        layout.guardian_finding_dispositions_path,
        "guardian-finding-disposition",
        "disposition_id",
    )

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
    materialized = expand_active_organization_entries(list(entries))
    records = [record for entry_kind, record in materialized if entry_kind == kind]
    if kind in {"paper-card", "evidence", "review-queue"}:
        records.extend(
            child
            for entry_kind, bundle in materialized
            if entry_kind == "primary-semantic-bundle"
            for child_kind, child in active_primary_entries(bundle)
            if child_kind == kind
        )
    if kind == "review-memory":
        records.extend(
            child
            for entry_kind, bundle in materialized
            if entry_kind == "review-semantic-bundle"
            for child_kind, child in active_review_entries(bundle)
            if child_kind == kind
        )
    return records


def _require_paper_filename_binding(
    entries: Iterable[BundleEntry],
    kind: str,
    expected_paper_id: str,
) -> None:
    for entry_kind, record in entries:
        if entry_kind != kind or record.get("paper_id") != expected_paper_id:
            raise ResearchKBError(
                Diagnostic(
                    WORKSPACE_LAYOUT_CONFLICT,
                    kind,
                    expected_paper_id,
                    "/paper_id",
                    "store filename does not match contained paper_id",
                )
            )
