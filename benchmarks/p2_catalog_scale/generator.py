from __future__ import annotations

import hashlib
import json
import os
import stat
import uuid
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from benchmarks.p2_catalog_scale.profiles import (
    GENERATOR_CONTRACT_VERSION,
    GenerationProfile,
    profile_by_id,
)
from research_kb.bundle import load_workspace_entries, validate_workspace_entries
from research_kb.catalog import CatalogAdapterRegistry, CatalogSnapshot
from research_kb.catalog.models import canonical_digest
from research_kb.errors import PATH_ESCAPE, Diagnostic, ResearchKBError
from research_kb.identifiers import Namespace
from research_kb.services.bootstrap import WorkspaceBootstrapService
from research_kb.storage.json_io import read_json_document, serialize_json
from research_kb.workspace import WorkspaceLayout


GENERATOR_MARKER = ".p2-catalog-generator.json"
GENERATOR_MANIFEST = "generator-manifest.json"
DEFAULT_SEED = "p2-catalog-seed-v1"
FIXTURE_ORIGIN = "synthetic_from_scratch"
DOMAIN_PROFILE_ID = "synthetic-p2-catalog"
SOURCE_ROOT_ID = "synthetic-sources"
SECTIONS = (
    "research_background_significance",
    "research_problem",
    "method_principle_advantages",
    "conclusions_applications",
    "innovation",
    "limitations",
    "future_outlook",
)
REVIEW_SECTIONS = (
    "review_objective_scope",
    "review_question_search_boundaries",
    "taxonomy_field_structure",
    "major_synthesis",
    "methods_metrics_guardrails",
    "gaps_frontiers",
    "primary_leads_reuse",
)
BENCHMARK_ERROR = "RKBC-036"
RUNTIME_BOUND_PATHS = (
    "workspace/knowledge/.research-kb/locks/workspace.lock",
    "workspace/knowledge/.research-kb/workspace.json",
)


@dataclass(frozen=True, slots=True)
class GeneratedWorkspace:
    target: Path
    profile: GenerationProfile
    seed: str
    layout: WorkspaceLayout
    manifest: dict[str, Any]
    catalog_snapshot: CatalogSnapshot


def export_portable_small_seed(generated: GeneratedWorkspace) -> Path:
    if generated.profile.profile_id != "p2-small":
        raise _benchmark_error("only p2-small may be exported as a committed portable seed")
    inspect_generated_workspace(generated.target)
    repository_root = Path(__file__).resolve().parents[2]
    destination = repository_root / "tests" / "fixtures" / "p2_small"
    if os.path.lexists(destination):
        raise _target_error("committed p2-small fixture target already exists")
    destination.mkdir()
    for relative, expected_digest in generated.manifest["file_digests"].items():
        source = generated.target / Path(*relative.split("/"))
        if _sha256_file(source) != expected_digest:
            raise _benchmark_error("portable seed source digest changed before export")
        target = destination / Path(*relative.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
    manifest_bytes = (generated.target / GENERATOR_MANIFEST).read_bytes()
    (destination / GENERATOR_MANIFEST).write_bytes(manifest_bytes)
    marker = {
        "contract_version": "1.0",
        "generator_contract_version": GENERATOR_CONTRACT_VERSION,
        "profile_id": generated.profile.profile_id,
        "seed": generated.seed,
        "state": "portable_seed",
        "content_tree_digest": generated.manifest["content_tree_digest"],
        "manifest_digest": hashlib.sha256(manifest_bytes).hexdigest(),
        "fixture_origin": FIXTURE_ORIGIN,
    }
    (destination / GENERATOR_MARKER).write_bytes(serialize_json(marker))
    return destination


def materialize_portable_seed(seed_root: Path, target: Path) -> GeneratedWorkspace:
    seed_root = Path(seed_root)
    marker = read_json_document(seed_root / GENERATOR_MARKER, record_kind="p2-portable-marker")
    manifest = read_json_document(seed_root / GENERATOR_MANIFEST, record_kind="p2-generator-manifest")
    if marker.get("state") != "portable_seed":
        raise _benchmark_error("portable fixture marker is missing or invalid")
    if marker.get("manifest_digest") != _sha256_file(seed_root / GENERATOR_MANIFEST):
        raise _benchmark_error("portable fixture manifest digest changed")
    profile = _profile_or_error(str(marker.get("profile_id", "")))
    seed = str(marker.get("seed", ""))
    target = _prepare_target(Path(target), profile, seed)
    for relative, expected_digest in manifest["file_digests"].items():
        source = seed_root / Path(*relative.split("/"))
        if not source.is_file() or _sha256_file(source) != expected_digest:
            raise _benchmark_error("portable fixture file is missing or changed")
        destination = target / Path(*relative.split("/"))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source.read_bytes())
    bootstrap = WorkspaceBootstrapService(target / "workspace" / "workspace.yaml").run()
    if bootstrap.exit_code != 0:
        raise _benchmark_error("portable fixture bootstrap failed")
    manifest_bytes = (seed_root / GENERATOR_MANIFEST).read_bytes()
    (target / GENERATOR_MANIFEST).write_bytes(manifest_bytes)
    complete_marker = {
        "contract_version": "1.0",
        "generator_contract_version": GENERATOR_CONTRACT_VERSION,
        "profile_id": profile.profile_id,
        "seed": seed,
        "state": "complete",
        "content_tree_digest": manifest["content_tree_digest"],
        "manifest_digest": hashlib.sha256(manifest_bytes).hexdigest(),
        "fixture_origin": FIXTURE_ORIGIN,
    }
    (target / GENERATOR_MARKER).write_bytes(serialize_json(complete_marker))
    return inspect_generated_workspace(target)


def generate_workspace(
    target: Path,
    *,
    profile_id: str,
    seed: str = DEFAULT_SEED,
) -> GeneratedWorkspace:
    profile = _profile_or_error(profile_id)
    target = _prepare_target(Path(target), profile, seed)
    workspace_root = target / "workspace"
    workspace_root.mkdir()
    try:
        _write_workspace_configuration(workspace_root, profile, seed)
        bootstrap = WorkspaceBootstrapService(workspace_root / "workspace.yaml").run()
        if bootstrap.exit_code != 0:
            raise _benchmark_error("generated workspace bootstrap failed")
        layout = WorkspaceLayout.load(workspace_root / "workspace.yaml")
        _write_payload(layout, profile, seed)
        entries = load_workspace_entries(layout)
        validate_workspace_entries(entries)
        snapshot = CatalogAdapterRegistry().project_entries(entries, workspace_id=layout.workspace_id)
        if len(snapshot.documents) != profile.catalog_item_count:
            raise _benchmark_error("generated catalog item count differs from the profile")
        manifest = _build_manifest(target, layout, profile, seed, entries, snapshot)
        (target / GENERATOR_MANIFEST).write_bytes(serialize_json(manifest))
        marker = {
            "contract_version": "1.0",
            "generator_contract_version": GENERATOR_CONTRACT_VERSION,
            "profile_id": profile.profile_id,
            "seed": seed,
            "state": "complete",
            "content_tree_digest": manifest["content_tree_digest"],
            "manifest_digest": _sha256_file(target / GENERATOR_MANIFEST),
            "fixture_origin": FIXTURE_ORIGIN,
        }
        (target / GENERATOR_MARKER).write_bytes(serialize_json(marker))
        return GeneratedWorkspace(target, profile, seed, layout, manifest, snapshot)
    except Exception:
        raise


def inspect_generated_workspace(target: Path) -> GeneratedWorkspace:
    target, profile, seed, manifest, layout = _verify_generated_target(Path(target))
    entries = load_workspace_entries(layout)
    validate_workspace_entries(entries)
    snapshot = CatalogAdapterRegistry().project_entries(entries, workspace_id=layout.workspace_id)
    if len(snapshot.documents) != profile.catalog_item_count:
        raise _benchmark_error("inspected catalog item count differs from the profile")
    return GeneratedWorkspace(target, profile, seed, layout, manifest, snapshot)


def verify_generated_payload(target: Path) -> dict[str, Any]:
    _, _, _, manifest, _ = _verify_generated_target(Path(target))
    return manifest


def _verify_generated_target(
    target: Path,
) -> tuple[Path, GenerationProfile, str, dict[str, Any], WorkspaceLayout]:
    target = Path(target)
    if not target.is_absolute() or not target.is_dir():
        raise _benchmark_error("generated target is not an absolute directory")
    marker = read_json_document(target / GENERATOR_MARKER, record_kind="p2-generator-marker")
    manifest = read_json_document(target / GENERATOR_MANIFEST, record_kind="p2-generator-manifest")
    if marker.get("state") != "complete":
        raise _benchmark_error("generated target is incomplete")
    if marker.get("generator_contract_version") != GENERATOR_CONTRACT_VERSION:
        raise _benchmark_error("generated target uses an unsupported generator contract")
    if marker.get("manifest_digest") != _sha256_file(target / GENERATOR_MANIFEST):
        raise _benchmark_error("generated manifest digest does not match the marker")
    profile = _profile_or_error(str(marker.get("profile_id", "")))
    seed = str(marker.get("seed", ""))
    if manifest.get("profile_id") != profile.profile_id or manifest.get("seed") != seed:
        raise _benchmark_error("generated marker and manifest identity differ")
    inventory = _workspace_inventory(target / "workspace")
    if inventory != manifest.get("file_digests"):
        raise _benchmark_error("generated workspace file inventory or digest changed")
    if _tree_digest(inventory) != marker.get("content_tree_digest"):
        raise _benchmark_error("generated workspace tree digest changed")
    allowed_root_entries = {
        GENERATOR_MARKER,
        GENERATOR_MANIFEST,
        "workspace",
        "runtime",
        "receipts",
    }
    if any(path.name not in allowed_root_entries for path in target.iterdir()):
        raise _benchmark_error("generated target contains a foreign root entry")
    layout = WorkspaceLayout.load(target / "workspace" / "workspace.yaml")
    return target, profile, seed, manifest, layout


def _prepare_target(target: Path, profile: GenerationProfile, seed: str) -> Path:
    if not target.is_absolute():
        raise _target_error("generator target must be absolute")
    repository_root = Path(__file__).resolve().parents[2]
    unresolved = Path(os.path.abspath(target))
    if unresolved == repository_root or unresolved.is_relative_to(repository_root):
        raise _target_error("large generator targets are forbidden inside the repository")
    if os.path.lexists(unresolved):
        raise _target_error("generator target must not exist")
    parent = unresolved.parent
    if not parent.is_dir() or _has_unsafe_component(parent):
        raise _target_error("generator target parent is missing or unsafe")
    parent_resolved = parent.resolve()
    resolved = parent_resolved / unresolved.name
    resolved.mkdir()
    initial_marker = {
        "contract_version": "1.0",
        "generator_contract_version": GENERATOR_CONTRACT_VERSION,
        "profile_id": profile.profile_id,
        "seed": seed,
        "state": "generating",
        "fixture_origin": FIXTURE_ORIGIN,
    }
    (resolved / GENERATOR_MARKER).write_bytes(serialize_json(initial_marker))
    return resolved


def _write_workspace_configuration(root: Path, profile: GenerationProfile, seed: str) -> None:
    workspace_id = _id(Namespace.WORKSPACE, seed, 1)
    workspace = {
        "contract_version": "1.0",
        "workspace": {
            "id": workspace_id,
            "knowledge_root": "./knowledge",
            "source_roots": [
                {
                    "root_id": SOURCE_ROOT_ID,
                    "path": "./sources",
                    "read_only_assets": True,
                }
            ],
            "local_inbox": "./inbox",
            "domain_profile": "./domain-profile.yaml",
        },
        "runtime": {
            "path_serialization": "workspace_relative_posix",
            "default_encoding": "utf-8",
            "line_ending": "lf",
        },
    }
    profile_document = {
        "contract_version": "1.0",
        "domain_profile": {
            "id": DOMAIN_PROFILE_ID,
            "name": "Synthetic P2 Catalog Studies",
            "version": "1.0",
        },
        "paper_card_sections": [
            {"section_id": section, "label": section.replace("_", " ").title()}
            for section in SECTIONS
        ],
        "evidence_axes": ["input", "process", "outcome"],
        "question_types": ["mechanism", "comparison"],
        "terminology": {"sample": "synthetic catalog case"},
        "step7_extensions": {},
    }
    root.joinpath("sources").mkdir()
    root.joinpath("workspace.yaml").write_text(
        yaml.safe_dump(workspace, sort_keys=False, allow_unicode=False),
        encoding="utf-8",
        newline="\n",
    )
    root.joinpath("domain-profile.yaml").write_text(
        yaml.safe_dump(profile_document, sort_keys=False, allow_unicode=False),
        encoding="utf-8",
        newline="\n",
    )


def _write_payload(layout: WorkspaceLayout, profile: GenerationProfile, seed: str) -> None:
    _write_jsonl(layout.registry_path, _registry_records(profile, seed))
    for paper_index in range(profile.paper_count):
        is_review = paper_index >= profile.primary_paper_count
        context = _paper_context(paper_index, is_review=is_review, seed=seed)
        source_path = layout.source_roots[SOURCE_ROOT_ID] / context["source_relative_path"]
        source_path.write_bytes(context["source_bytes"])
        _write_jsonl(layout.parse_path(context["paper_id"]), [_parsed_page(context)])
        if is_review:
            layout.review_memory_path(context["paper_id"]).write_bytes(
                serialize_json(_review_memory(context, profile.review_units_per_review, seed))
            )
        else:
            evidence = [
                _evidence(context, evidence_index, seed)
                for evidence_index in range(profile.evidence_per_primary)
            ]
            _write_jsonl(layout.evidence_path(context["paper_id"]), evidence)
            layout.paper_card_path(context["paper_id"]).write_bytes(
                serialize_json(_paper_card(context, evidence, profile.card_units_per_primary, seed))
            )
    _write_jsonl(layout.question_mappings_path, _question_records(profile, seed))
    step7 = _step7_records(profile, seed)
    for kind, records in step7.items():
        _write_jsonl(layout.step7_store_path(kind), records)
    _write_jsonl(layout.process_events_path, _process_events(profile, seed))
    _write_jsonl(layout.guardian_reports_path, _guardian_reports(layout.workspace_id, profile, seed))


def _registry_records(profile: GenerationProfile, seed: str) -> Iterator[dict[str, Any]]:
    for paper_index in range(profile.paper_count):
        is_review = paper_index >= profile.primary_paper_count
        context = _paper_context(paper_index, is_review=is_review, seed=seed)
        yield {
            "schema_version": "1.0",
            "paper_id": context["paper_id"],
            "source_ref": {
                "root_id": SOURCE_ROOT_ID,
                "relative_path": context["source_relative_path"],
            },
            "source_fingerprint": context["source_fingerprint"],
            "bibliography": {
                "title": context["title"],
                "authors": ["Synthetic Author"],
                "year": 2026,
                "doi": None,
            },
            "screening_status": "candidate",
            "duplicate_candidate_ids": [],
            "review_status": "ai_checked",
            "automation_status": "passed_auto_checks",
            "created_at": _timestamp(paper_index),
            "updated_at": _timestamp(paper_index),
            "fixture_origin": FIXTURE_ORIGIN,
        }


def _paper_context(paper_index: int, *, is_review: bool, seed: str) -> dict[str, Any]:
    ordinal = paper_index + 1
    paper_id = _id(Namespace.PAPER, seed, ordinal)
    paper_type = "review" if is_review else "primary"
    token = f"{ordinal:08d}"
    quote = (
        f"The fabricated review groups response timing for synthetic item {token}."
        if is_review
        else f"The fabricated intervention produced response token {token}."
    )
    source_bytes = (
        f"Synthetic {paper_type} catalog record {token}.\n{quote}\n"
    ).encode("utf-8")
    return {
        "paper_index": paper_index,
        "ordinal": ordinal,
        "paper_id": paper_id,
        "parse_event_id": _id(Namespace.PROCESS_EVENT, seed, ordinal),
        "source_relative_path": f"source-{token}.txt",
        "source_bytes": source_bytes,
        "source_fingerprint": {
            "algorithm": "sha256",
            "value": hashlib.sha256(source_bytes).hexdigest(),
        },
        "title": f"Synthetic {paper_type.title()} Catalog Record {token}",
        "quote": quote,
        "is_review": is_review,
    }


def _parsed_page(context: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "paper_id": context["paper_id"],
        "parse_run_id": context["parse_event_id"],
        "parser": {"adapter": "synthetic-text", "version": "1.0"},
        "pdf_page": 1,
        "printed_page": None,
        "text": context["source_bytes"].decode("utf-8"),
        "locator": "page:1:block:1",
        "created_at": _timestamp(context["paper_index"]),
        "fixture_origin": FIXTURE_ORIGIN,
    }


def _evidence(context: dict[str, Any], evidence_index: int, seed: str) -> dict[str, Any]:
    ordinal = context["paper_index"] + 1 + evidence_index * 1_000_000
    return {
        "schema_version": "1.0",
        "evidence_id": _id(Namespace.EVIDENCE, seed, ordinal),
        "paper_id": context["paper_id"],
        "claim": f"Synthetic item {context['ordinal']:08d} records a fabricated response.",
        "evidence_type": "reported_result",
        "quote": context["quote"],
        "source_page": {
            "pdf_page": 1,
            "printed_page": None,
            "section": "Synthetic results",
            "figure_or_table": None,
        },
        "locator": "page:1:block:1",
        "support_scope": "The fabricated response in this synthetic record only.",
        "what_it_does_not_support": ["External scientific conclusions"],
        "source_type": "primary",
        "canonical": True,
        "source_fingerprint": context["source_fingerprint"],
        "review_status": "ai_checked",
        "automation_status": "passed_auto_checks",
        "created_at": _timestamp(context["paper_index"]),
        "updated_at": _timestamp(context["paper_index"]),
        "fixture_origin": FIXTURE_ORIGIN,
    }


def _paper_card(
    context: dict[str, Any],
    evidence: list[dict[str, Any]],
    unit_count: int,
    seed: str,
) -> dict[str, Any]:
    units_by_section = {section: [] for section in SECTIONS}
    statement_types = ("reported_result", "method_description", "limitation")
    sections = ("research_problem", "method_principle_advantages", "limitations")
    for unit_index in range(unit_count):
        ordinal = context["paper_index"] * unit_count + unit_index + 1
        section = sections[unit_index % len(sections)]
        units_by_section[section].append(
            {
                "unit_id": _id(Namespace.UNIT, seed, ordinal),
                "section_id": section,
                "statement": (
                    f"Synthetic Card Unit {unit_index + 1} for item {context['ordinal']:08d}."
                ),
                "statement_type": statement_types[unit_index % len(statement_types)],
                "grounding_status": "grounded",
                "evidence_ids": [evidence[unit_index % len(evidence)]["evidence_id"]],
                "boundary_refs": [],
                "source_page": {
                    "pdf_page": 1,
                    "printed_page": None,
                    "section": "Synthetic results",
                    "figure_or_table": None,
                },
                "confidence": "medium",
            }
        )
    return {
        "schema_version": "1.0",
        "paper_id": context["paper_id"],
        "domain_profile_id": DOMAIN_PROFILE_ID,
        "card_status": "calibrated",
        "review_status": "ai_checked",
        "automation_status": "passed_auto_checks",
        "sections": [
            {"section_id": section, "units": units_by_section[section]}
            for section in SECTIONS
        ],
        "created_at": _timestamp(context["paper_index"]),
        "updated_at": _timestamp(context["paper_index"]),
        "fixture_origin": FIXTURE_ORIGIN,
    }


def _review_memory(
    context: dict[str, Any],
    unit_count: int,
    seed: str,
) -> dict[str, Any]:
    units = []
    unit_types = ("field_axis", "method_guardrail", "frontier_topic")
    for unit_index in range(unit_count):
        ordinal = context["paper_index"] * unit_count + unit_index + 1
        units.append(
            {
                "review_unit_id": _id(Namespace.REVIEW_UNIT, seed, ordinal),
                "section_id": "taxonomy_field_structure",
                "unit_type": unit_types[unit_index % len(unit_types)],
                "content": (
                    f"Synthetic Review Unit {unit_index + 1} for item {context['ordinal']:08d}."
                ),
                "source_notes": [
                    {
                        "pdf_page": 1,
                        "printed_page": None,
                        "section": "Synthetic taxonomy",
                        "figure_or_table": None,
                        "note_type": "paraphrase",
                        "text": context["quote"],
                        "locator": None,
                        "reopen_priority": "high",
                    }
                ],
                "workflow_impacts": [
                    {
                        "target": "primary_paper_reading",
                        "action": "Use this fabricated category only for synthetic routing tests.",
                    }
                ],
                "evidence_use": {
                    "can_support_canonical_evidence": False,
                    "can_guide_primary_grounding": True,
                    "primary_grounding_required_before": ["comparative_claim"],
                },
                "reuse_quality": {
                    "reuse_confidence": "medium",
                    "staleness_risk": "low",
                    "reason": "The fabricated unit is explicit in the synthetic source.",
                },
                "primary_paper_lead": None,
                "background_only": True,
                "can_enter_canonical_evidence": False,
                "not_fact": True,
            }
        )
    sections = [
        {
            "section_id": section,
            "units": units if section == "taxonomy_field_structure" else [],
        }
        for section in REVIEW_SECTIONS
    ]
    return {
        "schema_version": "1.0",
        "review_memory_id": _id(
            Namespace.REVIEW_MEMORY,
            seed,
            context["paper_index"] + 1,
        ),
        "paper_id": context["paper_id"],
        "source_type": "review",
        "review_subtype": "narrative_review",
        "review_subtype_source": "agent_high_confidence",
        "review_subtype_reason": "The synthetic source is declared as a review fixture.",
        "read_status": "targeted_read",
        "scope_tags": ["synthetic_catalog"],
        "one_sentence_reuse_value": "Provides fabricated background units for catalog testing.",
        "memory_value": {"status": "reusable", "reason": "Synthetic units are retained."},
        "coverage_limits": {
            "unread_sections": [],
            "weakly_read_sections": [],
            "reason": "The bounded synthetic source contains one section.",
        },
        "sections": sections,
        "non_reusable_notes": [],
        "source_fingerprint": context["source_fingerprint"],
        "parse_snapshot": {
            "parse_run_id": context["parse_event_id"],
            "adapter": "synthetic-text",
            "version": "1.0",
        },
        "background_only": True,
        "can_enter_canonical_evidence": False,
        "not_fact": True,
        "review_status": "ai_checked",
        "automation_status": "passed_auto_checks",
        "created_at": _timestamp(context["paper_index"]),
        "updated_at": _timestamp(context["paper_index"]),
        "fixture_origin": FIXTURE_ORIGIN,
    }


def _question_records(profile: GenerationProfile, seed: str) -> list[dict[str, Any]]:
    records = []
    for question_index in range(profile.question_count):
        paper_indices = [question_index % profile.primary_paper_count]
        if question_index == 0 and profile.primary_paper_count > 1:
            paper_indices.append(1)
        links = []
        for link_index, paper_index in enumerate(paper_indices):
            context = _paper_context(paper_index, is_review=False, seed=seed)
            unit_ordinal = paper_index * profile.card_units_per_primary + 1
            evidence_ordinal = paper_index + 1
            links.append(
                {
                    "question_link_id": _id(
                        Namespace.QUESTION_LINK,
                        seed,
                        question_index * 10 + link_index + 1,
                    ),
                    "paper_id": context["paper_id"],
                    "selected_card_unit_ids": [_id(Namespace.UNIT, seed, unit_ordinal)],
                    "role_in_question": "comparison",
                    "relevance_rationale": "The fabricated Unit is selected for a synthetic Question.",
                    "evidence_ids": [_id(Namespace.EVIDENCE, seed, evidence_ordinal)],
                    "boundary_refs": [],
                }
            )
        records.append(
            {
                "schema_version": "1.0",
                "question_id": _id(Namespace.QUESTION, seed, question_index + 1),
                "question_text": f"Synthetic catalog Question {question_index + 1}?",
                "scope": "The generated P2 fixture only.",
                "domain_profile_id": DOMAIN_PROFILE_ID,
                "paper_links": links,
                "mapping_status": "ai_checked",
                "created_at": _timestamp(question_index),
                "updated_at": _timestamp(question_index),
                "fixture_origin": FIXTURE_ORIGIN,
            }
        )
    return records


def _step7_records(profile: GenerationProfile, seed: str) -> dict[str, list[dict[str, Any]]]:
    records = {
        "step7-synthesis": [],
        "step7-review-angle": [],
        "step7-insight": [],
        "step7-cross-view": [],
    }
    if profile.step7_candidate_count == 0:
        return records
    if profile.question_count < 1 or profile.primary_paper_count < 2:
        raise _benchmark_error("Step 7 fixture requires one Question and two primary papers")
    question_id = _id(Namespace.QUESTION, seed, 1)
    bases = []
    evidence_ids = []
    unit_ids = []
    for paper_index in (0, 1):
        context = _paper_context(paper_index, is_review=False, seed=seed)
        unit_id = _id(
            Namespace.UNIT,
            seed,
            paper_index * profile.card_units_per_primary + 1,
        )
        evidence_id = _id(Namespace.EVIDENCE, seed, paper_index + 1)
        bases.append({"paper_id": context["paper_id"], "card_unit_ids": [unit_id]})
        unit_ids.append(unit_id)
        evidence_ids.append(evidence_id)
    common = {
        "schema_version": "1.0",
        "question_id": question_id,
        "candidate_status": "keep",
        "analysis_operator": "compare",
        "paper_card_base": bases,
        "evidence_base": evidence_ids,
        "review_queue_refs": [],
        "missing_evidence": ["Independent fabricated replication"],
        "assumptions": ["The synthetic items are comparable"],
        "risk": ["The fixture has no external scientific meaning"],
        "testability": "Inspect the deterministic generated records.",
        "next_action": "Retain for P2 catalog validation.",
        "trace_status": "traceable",
        "input_snapshot": {
            "domain_profile_version": "1.0",
            "card_unit_ids": unit_ids,
            "evidence_ids": evidence_ids,
            "review_queue_ids": [],
        },
        "not_fact": True,
        "review_status": "ai_draft",
        "automation_status": "pending",
        "rejection_rationale": None,
        "created_at": _timestamp(0),
        "updated_at": _timestamp(0),
        "fixture_origin": FIXTURE_ORIGIN,
    }
    synthesis_id = _id(Namespace.SYNTHESIS, seed, 1)
    angle_id = _id(Namespace.REVIEW_ANGLE, seed, 1)
    for ordinal in range(profile.step7_synthesis_count):
        records["step7-synthesis"].append(
            {
                **common,
                "candidate_id": _id(Namespace.SYNTHESIS, seed, ordinal + 1),
                "type": "synthesis",
                "title": f"Synthetic synthesis {ordinal + 1}",
                "analysis_operator": "aggregate",
                "claim": "The fabricated records share one catalog-visible pattern.",
                "scope": "Generated fixture records only.",
                "agreement_pattern": "Both synthetic records use bounded text.",
                "conflict_pattern": "No scientific conflict is represented.",
                "boundary_statement": "This candidate is not a scientific fact.",
            }
        )
    for ordinal in range(profile.step7_review_angle_count):
        records["step7-review-angle"].append(
            {
                **common,
                "candidate_id": _id(Namespace.REVIEW_ANGLE, seed, ordinal + 1),
                "type": "review_angle",
                "title": f"Synthetic review angle {ordinal + 1}",
                "thesis": "Organize the generated items by record route.",
                "organizing_axes": ["synthetic route"],
                "included_clusters": ["primary and review fixtures"],
                "excluded_scope": ["real scientific conclusions"],
                "why_this_angle_adds_value": "It exercises the catalog adapter.",
            }
        )
    for ordinal in range(profile.step7_insight_count):
        records["step7-insight"].append(
            {
                **common,
                "candidate_id": _id(Namespace.INSIGHT, seed, ordinal + 1),
                "type": "insight",
                "title": f"Synthetic insight {ordinal + 1}",
                "analysis_operator": "experiment_design",
                "insight_type": "experimental_idea",
                "hypothesis_or_idea": "A bounded filter should retrieve related fixture items.",
                "rationale": "The projection stores exact paper and Question identifiers.",
                "falsification_condition": "The exact filter returns an unrelated item.",
                "minimum_test": "Run one exact filter query.",
            }
        )
    for ordinal in range(profile.step7_cross_view_count):
        records["step7-cross-view"].append(
            {
                **common,
                "candidate_id": _id(Namespace.CROSS_VIEW, seed, ordinal + 1),
                "type": "cross_view",
                "title": f"Synthetic cross view {ordinal + 1}",
                "source_views": [synthesis_id, angle_id],
                "relation_type": "complements",
                "why_interesting": "The fixture joins two non-factual candidate views.",
                "shared_dimension": "catalog traceability",
                "non_equivalence_warning": "The candidate types remain distinct.",
            }
        )
    return records


def _process_events(profile: GenerationProfile, seed: str) -> Iterator[dict[str, Any]]:
    for event_index in range(profile.process_event_count):
        if event_index < profile.paper_count:
            context = _paper_context(
                event_index,
                is_review=event_index >= profile.primary_paper_count,
                seed=seed,
            )
            operation = "synthetic_parse"
            input_refs = [context["paper_id"]]
            output_refs = [context["paper_id"]]
        else:
            operation = "synthetic_catalog_activity"
            input_refs = []
            output_refs = []
        yield {
            "schema_version": "1.0",
            "event_id": _id(Namespace.PROCESS_EVENT, seed, event_index + 1),
            "operation": operation,
            "actor": "cli",
            "result": "success",
            "input_refs": input_refs,
            "output_refs": output_refs,
            "created_at": _timestamp(event_index),
            "fixture_origin": FIXTURE_ORIGIN,
        }


def _guardian_reports(
    workspace_id: str,
    profile: GenerationProfile,
    seed: str,
) -> Iterator[dict[str, Any]]:
    for report_index in range(profile.guardian_report_count):
        yield {
            "schema_version": "1.0",
            "guardian_report_id": _id(Namespace.GUARDIAN_REPORT, seed, report_index + 1),
            "workspace_id": workspace_id,
            "status": "success",
            "findings": [],
            "created_at": _timestamp(report_index),
            "fixture_origin": FIXTURE_ORIGIN,
        }


def _build_manifest(
    target: Path,
    layout: WorkspaceLayout,
    profile: GenerationProfile,
    seed: str,
    entries: list[tuple[str, dict[str, Any]]],
    snapshot: CatalogSnapshot,
) -> dict[str, Any]:
    inventory = _workspace_inventory(target / "workspace")
    durable_counts: dict[str, int] = {}
    for kind, _ in entries:
        durable_counts[kind] = durable_counts.get(kind, 0) + 1
    scientific_kinds = {
        "registry-paper",
        "paper-card",
        "evidence",
        "review-memory",
        "question-mapping",
        "step7-synthesis",
        "step7-review-angle",
        "step7-insight",
        "step7-cross-view",
    }
    operational_kinds = {"process-event", "guardian-report"}
    scientific_items = sum(
        1 for document in snapshot.documents if document.authority_layer == "canonical"
    )
    operational_items = sum(
        1 for document in snapshot.documents if document.authority_layer == "operational"
    )
    first_paper = _id(Namespace.PAPER, seed, 1)
    first_question = _id(Namespace.QUESTION, seed, 1) if profile.question_count else None
    paper_items = sum(1 for item in snapshot.documents if item.paper_id == first_paper)
    question_items = (
        sum(1 for item in snapshot.documents if item.question_id == first_question)
        if first_question
        else 0
    )
    manifest = {
        "contract_version": "1.0",
        "generator_contract_version": GENERATOR_CONTRACT_VERSION,
        "profile_id": profile.profile_id,
        "seed": seed,
        "parameters": profile.parameters(),
        "expected_durable_record_counts": dict(sorted(durable_counts.items())),
        "expected_durable_record_totals": {
            "scientific": sum(durable_counts.get(kind, 0) for kind in scientific_kinds),
            "operational": sum(durable_counts.get(kind, 0) for kind in operational_kinds),
            "supporting_parsed_pages": durable_counts.get("parsed-page", 0),
        },
        "expected_catalog_item_counts": {
            "scientific": scientific_items,
            "operational": operational_items,
            "total": len(snapshot.documents),
        },
        "expected_queries": {
            "paper_id": first_paper,
            "paper_item_count": paper_items,
            "question_id": first_question,
            "question_item_count": question_items,
        },
        "file_count": len(inventory),
        "portable_seed_file_count": len(inventory) + 2,
        "runtime_bound_file_count": len(RUNTIME_BOUND_PATHS),
        "runtime_bound_paths": list(RUNTIME_BOUND_PATHS),
        "total_file_count": len(inventory) + len(RUNTIME_BOUND_PATHS),
        "byte_count": sum(_file_size(target / path) for path in inventory),
        "file_digests": inventory,
        "content_tree_digest": _tree_digest(inventory),
        "canonical_tree_digest": _tree_digest(
            {key: value for key, value in inventory.items() if key.startswith("workspace/knowledge/")}
        ),
        "source_tree_digest": _tree_digest(
            {key: value for key, value in inventory.items() if key.startswith("workspace/sources/")}
        ),
        "fixture_origin": FIXTURE_ORIGIN,
    }
    manifest["manifest_payload_digest"] = canonical_digest(manifest)
    return manifest


def _workspace_inventory(workspace_root: Path) -> dict[str, str]:
    if not workspace_root.is_dir() or _has_unsafe_component(workspace_root):
        raise _benchmark_error("generated workspace root is missing or unsafe")
    target = workspace_root.parent
    inventory: dict[str, str] = {}
    pending = [(workspace_root, (workspace_root.name,))]
    while pending:
        directory, relative_parts = pending.pop()
        try:
            children = sorted(os.scandir(directory), key=lambda item: item.name)
        except OSError as error:
            raise _benchmark_error("generated workspace inventory cannot be read") from error
        for child in children:
            try:
                metadata = child.stat(follow_symlinks=False)
            except OSError as error:
                raise _benchmark_error("generated workspace path cannot be inspected") from error
            if child.is_symlink() or bool(
                getattr(metadata, "st_file_attributes", 0)
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
            ):
                raise _benchmark_error("generated workspace contains an unsafe path type")
            child_parts = (*relative_parts, child.name)
            if stat.S_ISDIR(metadata.st_mode):
                pending.append((Path(child.path), child_parts))
            elif stat.S_ISREG(metadata.st_mode):
                relative = "/".join(child_parts)
                if relative not in RUNTIME_BOUND_PATHS:
                    inventory[relative] = _sha256_file(Path(child.path))
    for relative in RUNTIME_BOUND_PATHS:
        bound_path = target / Path(*relative.split("/"))
        if not bound_path.is_file() or _is_unsafe_link(bound_path):
            raise _benchmark_error("generated workspace runtime binding is missing or unsafe")
    return dict(sorted(inventory.items()))


def _write_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        for record in records:
            handle.write(serialize_json(record))


def _id(namespace: Namespace, seed: str, ordinal: int) -> str:
    identity = f"{GENERATOR_CONTRACT_VERSION}|{seed}|{namespace.value}|{ordinal}"
    digest = hashlib.sha256(identity.encode("utf-8")).digest()
    value = uuid.UUID(bytes=digest[:16], version=4)
    return f"{namespace.value}_{value}"


def _timestamp(ordinal: int) -> str:
    day = ordinal // 86_400
    second = ordinal % 86_400
    hour, remainder = divmod(second, 3_600)
    minute, value = divmod(remainder, 60)
    return f"2026-01-{day + 1:02d}T{hour:02d}:{minute:02d}:{value:02d}Z"


def _tree_digest(inventory: dict[str, str]) -> str:
    return canonical_digest([[path, digest] for path, digest in sorted(inventory.items())])


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _file_size(path: Path) -> int:
    return path.stat().st_size


def _profile_or_error(profile_id: str) -> GenerationProfile:
    try:
        return profile_by_id(profile_id)
    except ValueError as error:
        raise _benchmark_error(str(error)) from error


def _has_unsafe_component(path: Path) -> bool:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        if os.path.lexists(current) and _is_unsafe_link(current):
            return True
    return False


def _is_unsafe_link(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    if callable(is_junction) and is_junction():
        return True
    try:
        attributes = getattr(os.lstat(path), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _target_error(message: str) -> ResearchKBError:
    return ResearchKBError(Diagnostic(PATH_ESCAPE, "p2-generator", None, "/target", message))


def _benchmark_error(message: str) -> ResearchKBError:
    return ResearchKBError(Diagnostic(BENCHMARK_ERROR, "p2-generator", None, "", message))


__all__ = [
    "GENERATOR_CONTRACT_VERSION",
    "GeneratedWorkspace",
    "GenerationProfile",
    "export_portable_small_seed",
    "generate_workspace",
    "inspect_generated_workspace",
    "materialize_portable_seed",
    "profile_by_id",
    "verify_generated_payload",
]
