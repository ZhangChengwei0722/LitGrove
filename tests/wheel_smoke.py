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


def _write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


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
        subprocess.run(
            [
                str(python), "-c",
                "from research_kb.compatibility import CompatibilitySourceRef, LegacyReaderAdapter; "
                "from research_kb.contracts.registry import SchemaRegistry; "
                "from research_kb.guardian import GuardianService; "
                "from research_kb.services import CompatibilityAdapterRegistry, CompatibilityInspectionService, ParseService, QuestionMappingService, QuestionReadingViewService, RecordService, RegistryService; "
                "registry = SchemaRegistry(); "
                "assert registry.schema('mutation-request')['$id'].endswith('mutation-request'); "
                "assert registry.schema('compatibility-difference')['$id'].endswith('compatibility-difference'); "
                "assert registry.schema('compatibility-report')['$id'].endswith('compatibility-report'); "
                "assert registry.schema('question-mapping')['$id'].endswith('question-mapping'); "
                "assert LegacyReaderAdapter.__name__ == 'LegacyReaderAdapter'; "
                "assert CompatibilitySourceRef.__name__ == 'CompatibilitySourceRef'; "
                "assert CompatibilityAdapterRegistry.__name__ == 'CompatibilityAdapterRegistry'; "
                "assert CompatibilityInspectionService.__name__ == 'CompatibilityInspectionService'; "
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
        if marker["layout_contract_version"] != "m2b-1":
            raise SystemExit("wheel workspace did not initialize at m2b-1")
        if not (workspace_root / "knowledge" / "questions").is_dir():
            raise SystemExit("wheel workspace lacks questions directory")
        if (workspace_root / "knowledge" / "questions" / "mappings.jsonl").exists():
            raise SystemExit("workspace init created an empty question store")

        source = sources / "wheel-study.txt"
        source.write_text("Invented wheel source.\n", encoding="utf-8", newline="\n")
        metadata_path = workspace_root / "metadata.json"
        _write_json(
            metadata_path,
            {
                "bibliography": {
                    "title": "Synthetic Wheel Study",
                    "authors": ["Fixture Author"],
                    "year": 2026,
                    "doi": None,
                },
                "fixture_origin": "synthetic_from_scratch",
            },
        )
        paper_id = _run_json(
            python,
            Path(temporary),
            "registry",
            "add",
            "--workspace",
            str(config_path),
            "--root-id",
            "wheel-sources",
            "--relative-path",
            source.name,
            "--metadata",
            str(metadata_path),
        )["paper_id"]

        def promote(name: str, request: dict) -> dict:
            request_path = workspace_root / f"{name}.json"
            _write_json(request_path, request)
            return _run_json(
                python,
                Path(temporary),
                "record",
                "promote",
                "--workspace",
                str(config_path),
                "--request",
                str(request_path),
                "--actor",
                "agent",
            )

        common_context = {"paper_id": paper_id}
        evidence_id = promote(
            "evidence-request",
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
            "queue-request",
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
        sections = [
            {"section_id": item["section_id"], "units": []}
            for item in profile["paper_card_sections"]
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
            "card-request",
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
        card_path = next((workspace_root / "knowledge" / "paper_cards" / "by_paper").glob("*.card.json"))
        card = json.loads(card_path.read_text(encoding="utf-8"))
        unit_id = next(unit["unit_id"] for section in card["sections"] for unit in section["units"])
        question_id = promote(
            "question-request",
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
