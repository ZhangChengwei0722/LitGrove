from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import venv
from pathlib import Path


def _run_json(python: Path, cwd: Path, *args: str) -> dict:
    completed = subprocess.run(
        [str(python), "-m", "research_kb", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return json.loads(completed.stdout)


def _run_json_stdin(python: Path, cwd: Path, value: dict, *args: str) -> dict:
    completed = subprocess.run(
        [str(python), "-m", "research_kb", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        input=json.dumps(value),
        text=True,
        encoding="utf-8",
    )
    if completed.stderr:
        raise SystemExit("base wheel stdin command wrote unexpected stderr")
    return json.loads(completed.stdout)


def _tree_snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    wheels = sorted((root / "dist").glob("research_kb_core-*.whl"))
    if not wheels:
        raise SystemExit("build a wheel before running the smoke test")
    with tempfile.TemporaryDirectory(prefix="research-kb-wheel-smoke-") as temporary:
        environment = Path(temporary) / "venv"
        venv.EnvBuilder(with_pip=True).create(environment)
        python = environment / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        subprocess.run([str(python), "-m", "pip", "install", str(wheels[-1])], check=True)
        subprocess.run([str(python), "-m", "research_kb", "--version"], cwd=temporary, check=True)
        capability = _run_json(python, Path(temporary), "capability", "show")
        pdf_capability = next(item for item in capability["parse_adapters"] if item["adapter"] == "pdfplumber")
        if pdf_capability != {
            "adapter": "pdfplumber",
            "availability": "dependency_missing",
            "version": None,
            "diagnostic_code": "RKBC-028",
        }:
            raise SystemExit("base wheel capability report did not expose the missing PDF dependency")
        if not {"intake inspect", "paper context", "review context"}.issubset(capability["read_commands"]):
            raise SystemExit("base wheel capability report lacks deterministic intake/context reads")
        if capability["features"]["review_runtime"] is not True:
            raise SystemExit("base wheel capability report lacks Review Memory runtime")
        subprocess.run(
            [
                str(python), "-c",
                "from research_kb.compatibility import CompatibilitySourceRef, LegacyReaderAdapter; "
                "from research_kb.contracts.registry import SchemaRegistry; "
                "from research_kb.guardian import GuardianService; "
                "from research_kb.services import CompatibilityAdapterRegistry, CompatibilityInspectionService, IntakeInspectService, PaperContextService, ParseService, QuestionMappingService, QuestionReadingViewService, RecordService, RegistryService, ReviewContextService, ReviewMemoryService; "
                "registry = SchemaRegistry(); "
                "assert registry.schema('mutation-request')['$id'].endswith('mutation-request'); "
                "assert registry.schema('compatibility-difference')['$id'].endswith('compatibility-difference'); "
                "assert registry.schema('compatibility-report')['$id'].endswith('compatibility-report'); "
                "assert registry.schema('question-mapping')['$id'].endswith('question-mapping'); "
                "assert registry.schema('review-memory')['$id'].endswith('review-memory'); "
                "assert LegacyReaderAdapter.__name__ == 'LegacyReaderAdapter'; "
                "assert CompatibilitySourceRef.__name__ == 'CompatibilitySourceRef'; "
                "assert CompatibilityAdapterRegistry.__name__ == 'CompatibilityAdapterRegistry'; "
                "assert CompatibilityInspectionService.__name__ == 'CompatibilityInspectionService'; "
                "assert IntakeInspectService.__name__ == 'IntakeInspectService'; "
                "assert PaperContextService.__name__ == 'PaperContextService'; "
                "assert ReviewContextService.__name__ == 'ReviewContextService'; "
                "assert ReviewMemoryService.__name__ == 'ReviewMemoryService'; "
                "assert QuestionMappingService.__name__ == 'QuestionMappingService'; "
                "assert QuestionReadingViewService.__name__ == 'QuestionReadingViewService'",
            ],
            cwd=temporary,
            check=True,
        )
        workspace_root = Path(temporary) / "synthetic-workspace"
        sources = workspace_root / "sources"
        sources.mkdir(parents=True)
        profile = {
            "contract_version": "1.0",
            "domain_profile": {"id": "wheel-domain", "name": "Synthetic Wheel Domain", "version": "1.0"},
            "paper_card_sections": [
                {"section_id": value, "label": value.replace("_", " ").title()}
                for value in (
                    "research_background_significance",
                    "research_problem",
                    "method_principle_advantages",
                    "conclusions_applications",
                    "innovation",
                    "limitations",
                    "future_outlook",
                )
            ],
            "evidence_axes": ["input", "outcome"],
            "question_types": ["comparison"],
            "terminology": {},
            "step7_extensions": {},
        }
        workspace = {
            "contract_version": "1.0",
            "workspace": {
                "id": "workspace_c3333333-3333-4333-8333-333333333333",
                "knowledge_root": "./knowledge",
                "source_roots": [
                    {"root_id": "wheel-sources", "path": "./sources", "read_only_assets": True}
                ],
                "local_inbox": "./inbox",
                "domain_profile": "./domain-profile.json",
            },
            "runtime": {
                "path_serialization": "workspace_relative_posix",
                "default_encoding": "utf-8",
                "line_ending": "lf",
            },
        }
        profile_path = workspace_root / "domain-profile.json"
        config_path = workspace_root / "workspace.json"
        profile_path.write_text(json.dumps(profile, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        config_path.write_text(json.dumps(workspace, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        outputs = []
        for extra in (["--dry-run"], [], []):
            completed = subprocess.run(
                [
                    str(python), "-m", "research_kb", "workspace", "init",
                    "--workspace", str(config_path), *extra,
                ],
                cwd=temporary,
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            outputs.append(json.loads(completed.stdout)["result"])
        if outputs != ["planned", "initialized", "no_change"]:
            raise SystemExit(f"unexpected workspace init results: {outputs}")
        marker = json.loads((workspace_root / "knowledge" / ".research-kb" / "workspace.json").read_text(encoding="utf-8"))
        if marker["layout_contract_version"] != "m3a-2a":
            raise SystemExit("wheel workspace did not initialize at m3a-2a")
        if not (workspace_root / "knowledge" / "questions").is_dir():
            raise SystemExit("wheel workspace lacks questions directory")
        if (workspace_root / "knowledge" / "questions" / "mappings.jsonl").exists():
            raise SystemExit("workspace init created an empty question store")
        if not (workspace_root / "knowledge" / "review_memories" / "by_paper").is_dir():
            raise SystemExit("wheel workspace lacks review memory directories")
        if any((workspace_root / "knowledge" / "review_memories" / "by_paper").iterdir()):
            raise SystemExit("workspace init created a review memory record")

        source = sources / "wheel-study.txt"
        source.write_text("The invented wheel response increased.\n", encoding="utf-8", newline="\n")
        intake = _run_json(
            python,
            Path(temporary),
            "intake",
            "inspect",
            "--workspace",
            str(config_path),
            "--source",
            str(source),
        )
        if intake["registration"] != {"paper_ids": [], "state": "unregistered"}:
            raise SystemExit("base wheel intake did not report an unregistered source")
        paper_id = _run_json_stdin(
            python,
            Path(temporary),
            {
                "bibliography": {
                    "title": "Synthetic Wheel Study",
                    "authors": ["Fixture Author"],
                    "year": 2026,
                    "doi": None,
                },
                "fixture_origin": "synthetic_from_scratch",
            },
            "registry",
            "add",
            "--workspace",
            str(config_path),
            "--root-id",
            intake["source"]["root_id"],
            "--relative-path",
            intake["source"]["relative_path"],
            "--metadata",
            "-",
        )["paper_id"]
        registered_intake = _run_json(
            python,
            Path(temporary),
            "intake",
            "inspect",
            "--workspace",
            str(config_path),
            "--source",
            str(source),
        )
        if registered_intake["registration"] != {
            "paper_ids": [paper_id],
            "state": "registered_current",
        }:
            raise SystemExit("base wheel intake did not recover the registered paper")
        source_before_parse = source.read_bytes()
        unavailable = subprocess.run(
            [
                str(python), "-m", "research_kb", "parse", "run",
                "--workspace", str(config_path),
                "--paper-id", paper_id,
                "--adapter", "pdfplumber",
            ],
            cwd=temporary,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if unavailable.returncode != 2 or unavailable.stdout:
            raise SystemExit("base wheel did not fail closed for unavailable PDF adapter")
        unavailable_payload = json.loads(unavailable.stderr)
        if unavailable_payload["diagnostic"]["code"] != "RKBC-028":
            raise SystemExit("base wheel returned the wrong unavailable-adapter diagnostic")
        parse_output = _run_json(
            python,
            Path(temporary),
            "parse",
            "run",
            "--workspace",
            str(config_path),
            "--paper-id",
            paper_id,
            "--adapter",
            "synthetic-text",
        )
        if parse_output["parser"] != {"adapter": "synthetic-text", "version": "1.0"}:
            raise SystemExit("base wheel synthetic parser identity is incorrect")
        parse_read = _run_json(
            python,
            Path(temporary),
            "parse",
            "show",
            "--workspace",
            str(config_path),
            "--paper-id",
            paper_id,
            "--page",
            "1",
        )
        if parse_read["returned_page_count"] != 1:
            raise SystemExit("base wheel parse show did not return one selected page")
        if source.read_bytes() != source_before_parse:
            raise SystemExit("base wheel parse changed the source asset")

        def promote(request: dict) -> dict:
            return _run_json_stdin(
                python,
                Path(temporary),
                request,
                "record",
                "promote",
                "--workspace",
                str(config_path),
                "--request",
                "-",
                "--actor",
                "agent",
            )

        common_context = {"paper_id": paper_id}
        evidence_id = promote(
            {
                "contract_version": "1.0",
                "operation": "append",
                "record_kind": "evidence",
                "target_record_id": None,
                "context": common_context,
                "payload": {
                    "claim": "The synthetic wheel response increased.",
                    "evidence_type": "reported_result",
                    "quote": "The invented wheel response increased.",
                    "source_page": {
                        "pdf_page": 1,
                        "printed_page": None,
                        "section": "Results",
                        "figure_or_table": None,
                    },
                    "locator": "page:1:block:1",
                    "support_scope": "The invented wheel case only.",
                    "what_it_does_not_support": ["Other synthetic cases"],
                    "review_status": "ai_checked",
                    "fixture_origin": "synthetic_from_scratch",
                },
            },
        )["record_id"]
        queue_id = promote(
            {
                "contract_version": "1.0",
                "operation": "append",
                "record_kind": "review-queue",
                "target_record_id": None,
                "context": common_context,
                "payload": {
                    "issue_type": "overclaim",
                    "claim_candidate": "The synthetic wheel response is universal.",
                    "reason": "The invented source contains one case.",
                    "source_page": {
                        "pdf_page": 1,
                        "printed_page": None,
                        "section": "Discussion",
                        "figure_or_table": None,
                    },
                    "locator": "page:1:block:2",
                    "resolution_status": "needs_resolution",
                    "review_status": "ai_checked",
                    "fixture_origin": "synthetic_from_scratch",
                },
            },
        )["record_id"]
        partial_context = _run_json(
            python,
            Path(temporary),
            "paper",
            "context",
            "--workspace",
            str(config_path),
            "--paper-id",
            paper_id,
        )
        if partial_context["paper_card"] is not None:
            raise SystemExit("base wheel partial context unexpectedly contains a Paper Card")
        if [item["evidence_id"] for item in partial_context["evidence"]] != [evidence_id]:
            raise SystemExit("base wheel partial context did not recover Evidence")
        if [item["queue_id"] for item in partial_context["review_queue"]] != [queue_id]:
            raise SystemExit("base wheel partial context did not recover review queue")
        sections = [
            {"section_id": item["section_id"], "units": []}
            for item in intake["domain_profile"]["paper_card_sections"]
        ]
        sections[1]["units"].append(
            {
                "section_id": sections[1]["section_id"],
                "statement": "The synthetic wheel study asks whether the response increases.",
                "statement_type": "reported_result",
                "grounding_status": "grounded",
                "evidence_ids": [evidence_id],
                "boundary_refs": [queue_id],
                "source_page": {
                    "pdf_page": 1,
                    "printed_page": None,
                    "section": "Results",
                    "figure_or_table": None,
                },
                "confidence": "medium",
            }
        )
        promote(
            {
                "contract_version": "1.0",
                "operation": "append",
                "record_kind": "paper-card",
                "target_record_id": None,
                "context": common_context,
                "payload": {
                    "card_status": "calibrated",
                    "review_status": "ai_checked",
                    "sections": sections,
                    "fixture_origin": "synthetic_from_scratch",
                },
            },
        )
        promoted_context = _run_json(
            python,
            Path(temporary),
            "paper",
            "context",
            "--workspace",
            str(config_path),
            "--paper-id",
            paper_id,
        )
        unit_id = next(
            unit["unit_id"]
            for section in promoted_context["paper_card"]["sections"]
            for unit in section["units"]
        )
        question_id = promote(
            {
                "contract_version": "1.0",
                "operation": "append",
                "record_kind": "question-mapping",
                "target_record_id": None,
                "context": {"paper_id": None, "question_origin": "user_supplied"},
                "payload": {
                    "question_text": "How does the synthetic wheel response change?",
                    "scope": "The invented wheel fixture only.",
                    "mapping_status": "ai_checked",
                    "paper_links": [
                        {
                            "paper_id": paper_id,
                            "selected_card_unit_ids": [unit_id],
                            "role_in_question": "comparison",
                            "relevance_rationale": "The fabricated unit addresses the wheel question.",
                            "boundary_refs": [],
                        }
                    ],
                },
                "fixture_origin": "synthetic_from_scratch",
            },
        )["record_id"]

        before_knowledge = _tree_snapshot(workspace_root / "knowledge")
        before_sources = _tree_snapshot(sources)
        status = _run_json(
            python,
            Path(temporary),
            "paper",
            "status",
            "--workspace",
            str(config_path),
            "--paper-id",
            paper_id,
        )
        context = _run_json(
            python,
            Path(temporary),
            "paper",
            "context",
            "--workspace",
            str(config_path),
            "--paper-id",
            paper_id,
        )
        if status["paper_card"]["unit_count"] != 1 or status["question_mappings"]["linked_count"] != 1:
            raise SystemExit("base wheel paper status did not project the completed synthetic chain")
        if not status["integrity"]["mutation_safe"]:
            raise SystemExit("base wheel paper status unexpectedly blocked mutation")
        if next(
            unit["unit_id"]
            for section in context["paper_card"]["sections"]
            for unit in section["units"]
        ) != unit_id:
            raise SystemExit("base wheel paper context did not preserve the Card Unit ID")
        if [item["evidence_id"] for item in context["evidence"]] != [evidence_id]:
            raise SystemExit("base wheel paper context did not preserve Evidence")
        if [item["queue_id"] for item in context["review_queue"]] != [queue_id]:
            raise SystemExit("base wheel paper context did not preserve review queue")
        if _tree_snapshot(workspace_root / "knowledge") != before_knowledge:
            raise SystemExit("base wheel deterministic reads changed managed workspace files")
        if _tree_snapshot(sources) != before_sources:
            raise SystemExit("base wheel deterministic reads changed source files")
        rendered = subprocess.run(
            [
                str(python),
                "-m",
                "research_kb",
                "question",
                "render",
                "--workspace",
                str(config_path),
                "--question-id",
                question_id,
            ],
            cwd=temporary,
            check=True,
            capture_output=True,
        )
        if rendered.stderr:
            raise SystemExit("wheel render wrote stderr")
        if b"\r" in rendered.stdout or not rendered.stdout.endswith(b"\n"):
            raise SystemExit("wheel render is not UTF-8/LF output")
        rendered_text = rendered.stdout.decode("utf-8")
        for required in (
            'view_type: "question_reading_view"',
            "## Linked Papers And Selected Card Units",
            "## Canonical Evidence Trace",
            "## Review Queue Boundaries",
            evidence_id,
            queue_id,
        ):
            if required not in rendered_text:
                raise SystemExit(f"wheel render lacks required trace: {required}")
        if _tree_snapshot(workspace_root / "knowledge") != before_knowledge:
            raise SystemExit("wheel render changed managed workspace files")
        if _tree_snapshot(sources) != before_sources:
            raise SystemExit("wheel render changed source files")
        if (workspace_root / "knowledge" / "views").exists():
            raise SystemExit("wheel render created a views directory")

        review_source = sources / "wheel-review.txt"
        review_source.write_text(
            "The fabricated wheel review repeats an existing orientation.\n",
            encoding="utf-8",
            newline="\n",
        )
        review_intake = _run_json(
            python,
            Path(temporary),
            "intake",
            "inspect",
            "--workspace",
            str(config_path),
            "--source",
            str(review_source),
        )
        review_paper_id = _run_json_stdin(
            python,
            Path(temporary),
            {
                "bibliography": {"title": "Synthetic Wheel Review"},
                "fixture_origin": "synthetic_from_scratch",
            },
            "registry",
            "add",
            "--workspace",
            str(config_path),
            "--root-id",
            review_intake["source"]["root_id"],
            "--relative-path",
            review_intake["source"]["relative_path"],
            "--metadata",
            "-",
        )["paper_id"]
        _run_json(
            python,
            Path(temporary),
            "parse",
            "run",
            "--workspace",
            str(config_path),
            "--paper-id",
            review_paper_id,
            "--adapter",
            "synthetic-text",
        )
        review_sections = [
            {"section_id": section_id, "units": []}
            for section_id in (
                "review_objective_scope",
                "review_question_search_boundaries",
                "taxonomy_field_structure",
                "major_synthesis",
                "methods_metrics_guardrails",
                "gaps_frontiers",
                "primary_leads_reuse",
            )
        ]
        review_memory_id = promote(
            {
                "contract_version": "1.0",
                "operation": "append",
                "record_kind": "review-memory",
                "target_record_id": None,
                "context": {"paper_id": review_paper_id},
                "payload": {
                    "review_subtype": "narrative_review",
                    "review_subtype_source": "agent_high_confidence",
                    "review_subtype_reason": "The synthetic source is explicitly a secondary orientation.",
                    "read_status": "deep_read",
                    "scope_tags": ["synthetic_review"],
                    "one_sentence_reuse_value": "Records that the fabricated review is redundant.",
                    "memory_value": {
                        "status": "low_value",
                        "reason": "The fabricated orientation duplicates existing synthetic context.",
                    },
                    "coverage_limits": {
                        "unread_sections": [],
                        "weakly_read_sections": [],
                        "reason": "The one-line synthetic source was read completely.",
                    },
                    "sections": review_sections,
                    "non_reusable_notes": [
                        {"content": "The orientation is duplicated.", "reason": "duplicate"}
                    ],
                    "review_status": "ai_checked",
                    "fixture_origin": "synthetic_from_scratch",
                },
                "fixture_origin": "synthetic_from_scratch",
            }
        )["record_id"]
        review_before = _tree_snapshot(workspace_root / "knowledge")
        review_context = _run_json(
            python,
            Path(temporary),
            "review",
            "context",
            "--workspace",
            str(config_path),
            "--paper-id",
            review_paper_id,
        )
        if review_context["review_memory"]["review_memory_id"] != review_memory_id:
            raise SystemExit("base wheel review context did not recover Review Memory")
        if review_context["freshness"]["state"] != "current":
            raise SystemExit("base wheel Review Memory is unexpectedly stale")
        if review_context["review_memory"]["memory_value"]["status"] != "low_value":
            raise SystemExit("base wheel low-value Review Memory was not preserved")
        if _tree_snapshot(workspace_root / "knowledge") != review_before:
            raise SystemExit("base wheel review context changed managed workspace files")
        guardian = _run_json(
            python,
            Path(temporary),
            "guardian",
            "check",
            "--workspace",
            str(config_path),
        )
        if guardian["status"] != "success":
            raise SystemExit("base wheel Guardian rejected valid Review Memory")
        subprocess.run(
            [
                str(python), "-m", "research_kb", "contract", "validate",
                "--kind", "workspace", "--input", str(root / "templates" / "workspace.example.yaml"),
            ],
            cwd=temporary,
            check=True,
        )
    print("wheel_smoke=success")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
