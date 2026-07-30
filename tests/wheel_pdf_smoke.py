from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.pdf_helpers import write_synthetic_pdf


def _run_json(python: Path, cwd: Path, *args: str) -> dict:
    completed = subprocess.run(
        [str(python), "-m", "research_kb", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if completed.stderr:
        raise SystemExit("PDF wheel command wrote unexpected stderr")
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
        raise SystemExit("PDF wheel stdin command wrote unexpected stderr")
    return json.loads(completed.stdout)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    wheels = sorted((ROOT / "dist").glob("research_kb_core-*.whl"))
    if not wheels:
        raise SystemExit("build a wheel before running the PDF smoke test")

    with tempfile.TemporaryDirectory(prefix="research-kb-pdf-wheel-smoke-") as temporary:
        temporary_root = Path(temporary)
        environment = temporary_root / "venv"
        venv.EnvBuilder(with_pip=True).create(environment)
        python = environment / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        subprocess.run(
            [str(python), "-m", "pip", "install", f"{wheels[-1]}[pdf]"],
            check=True,
        )
        installed_version = subprocess.run(
            [str(python), "-c", "from importlib.metadata import version; print(version('pdfplumber'))"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        ).stdout.strip()
        capability = _run_json(python, temporary_root, "capability", "show")
        pdf_capability = next(item for item in capability["parse_adapters"] if item["adapter"] == "pdfplumber")
        if pdf_capability != {
            "adapter": "pdfplumber",
            "availability": "available",
            "version": installed_version,
            "diagnostic_code": None,
        }:
            raise SystemExit("PDF wheel capability report does not match the installed adapter")
        text_flow_capability = next(
            item for item in capability["parse_adapters"] if item["adapter"] == "pdfplumber-text-flow"
        )
        if text_flow_capability != {
            "adapter": "pdfplumber-text-flow",
            "availability": "available",
            "version": installed_version,
            "diagnostic_code": None,
        }:
            raise SystemExit("PDF wheel text-flow capability does not match the installed adapter")
        if not {"discovery search", "identity list", "intake inspect", "intake inspect-acquired", "job list", "job show", "manuscript inspect", "paper context", "review context", "source list", "source scan", "step7 context", "step7 render"}.issubset(capability["read_commands"]):
            raise SystemExit("PDF wheel capability report lacks deterministic intake/context reads")
        if capability["discovery_connectors"] != [{"connector": "europe-pmc", "availability": "available", "network_required": True}]:
            raise SystemExit("PDF wheel capability report lacks the Europe PMC connector")
        if capability["features"]["review_runtime"] is not True or capability["features"]["step7_runtime"] is not True or capability["features"]["on_demand_discovery"] is not True or capability["features"]["manuscript_projection"] is not True or capability["features"]["pipeline_jobs"] is not True or capability["features"]["source_asset_runtime"] is not True or capability["features"]["registry_identity_correction"] is not True:
            raise SystemExit("PDF wheel capability report lacks Review Memory, Step 7 or discovery runtime")
        for command in ("context", "render"):
            subprocess.run(
                [str(python), "-m", "research_kb", "step7", command, "--help"],
                cwd=temporary_root,
                check=True,
                capture_output=True,
            )

        workspace_root = temporary_root / "synthetic-pdf-workspace"
        sources = workspace_root / "sources"
        sources.mkdir(parents=True)
        source = write_synthetic_pdf(
            sources / "wheel-primary.pdf",
            ["Invented PDF wheel response."],
        )
        source_before = _sha256(source)
        profile = {
            "contract_version": "1.0",
            "domain_profile": {
                "id": "domain-pdf-wheel",
                "name": "Synthetic PDF Wheel Domain",
                "version": "1.0",
            },
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
                "id": "workspace_d4444444-4444-4444-8444-444444444444",
                "knowledge_root": "./knowledge",
                "source_roots": [
                    {"root_id": "pdf-wheel-sources", "path": "./sources", "read_only_assets": True}
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
        profile_path.write_text(json.dumps(profile) + "\n", encoding="utf-8", newline="\n")
        config_path.write_text(json.dumps(workspace) + "\n", encoding="utf-8", newline="\n")

        init_output = _run_json(
            python,
            temporary_root,
            "workspace",
            "init",
            "--workspace",
            str(config_path),
        )
        if init_output["result"] != "initialized":
            raise SystemExit("PDF wheel workspace did not initialize")
        manuscript_knowledge_before = {
            path.relative_to(workspace_root / "knowledge").as_posix(): path.read_bytes()
            for path in (workspace_root / "knowledge").rglob("*")
            if path.is_file()
        }
        manuscript = _run_json(
            python,
            temporary_root,
            "manuscript",
            "inspect",
            "--workspace",
            str(config_path),
            "--source",
            str(source),
        )
        if manuscript["document"]["parser"] != {
            "adapter": "pdfplumber",
            "version": installed_version,
        }:
            raise SystemExit("PDF wheel manuscript parser identity does not match installed metadata")
        if manuscript["document"]["unit_count"] != 1 or manuscript["persistent_writes"] != 0:
            raise SystemExit("PDF wheel manuscript projection is incomplete")
        if manuscript["units"][0]["locator"] != "pdf:page:1":
            raise SystemExit("PDF wheel manuscript page locator is unstable")
        if _sha256(source) != source_before:
            raise SystemExit("PDF wheel manuscript projection changed the source")
        if {
            path.relative_to(workspace_root / "knowledge").as_posix(): path.read_bytes()
            for path in (workspace_root / "knowledge").rglob("*")
            if path.is_file()
        } != manuscript_knowledge_before:
            raise SystemExit("PDF wheel manuscript projection changed managed workspace files")
        intake = _run_json(
            python,
            temporary_root,
            "intake",
            "inspect",
            "--workspace",
            str(config_path),
            "--source",
            str(source),
        )
        if intake["registration"] != {"paper_ids": [], "state": "unregistered"}:
            raise SystemExit("PDF wheel intake did not report an unregistered source")
        paper_id = _run_json_stdin(
            python,
            temporary_root,
            {"fixture_origin": "synthetic_from_scratch"},
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
            temporary_root,
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
            raise SystemExit("PDF wheel intake did not recover the registered paper")
        parse_output = _run_json(
            python,
            temporary_root,
            "parse",
            "run",
            "--workspace",
            str(config_path),
            "--paper-id",
            paper_id,
            "--adapter",
            "pdfplumber-text-flow",
        )
        if parse_output["parser"] != {"adapter": "pdfplumber-text-flow", "version": installed_version}:
            raise SystemExit("PDF wheel parser identity does not match installed package metadata")
        if parse_output["pages"] != 1:
            raise SystemExit("PDF wheel did not persist one row for the generated page")
        knowledge_before_read = {
            path.relative_to(workspace_root / "knowledge").as_posix(): path.read_bytes()
            for path in (workspace_root / "knowledge").rglob("*")
            if path.is_file()
        }
        parse_read = _run_json(
            python,
            temporary_root,
            "parse",
            "show",
            "--workspace",
            str(config_path),
            "--paper-id",
            paper_id,
            "--page",
            "1",
        )
        status = _run_json(
            python,
            temporary_root,
            "paper",
            "status",
            "--workspace",
            str(config_path),
            "--paper-id",
            paper_id,
        )
        if parse_read["returned_page_count"] != 1:
            raise SystemExit("PDF wheel parse show did not return page one")
        if status["parse"]["adapter"] != "pdfplumber-text-flow" or status["source"]["state"] != "current":
            raise SystemExit("PDF wheel paper status did not expose current PDF parse state")
        if not status["integrity"]["mutation_safe"]:
            raise SystemExit("PDF wheel paper status unexpectedly blocked mutation")
        if {
            path.relative_to(workspace_root / "knowledge").as_posix(): path.read_bytes()
            for path in (workspace_root / "knowledge").rglob("*")
            if path.is_file()
        } != knowledge_before_read:
            raise SystemExit("PDF wheel deterministic reads changed managed workspace files")

        def promote(request: dict) -> dict:
            return _run_json_stdin(
                python,
                temporary_root,
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

        quote = "Invented PDF wheel response."
        start = parse_read["pages"][0]["text"].index(quote)
        end = start + len(quote)
        locator = f"page:1:char:{start}-{end}"
        common_context = {"paper_id": paper_id}
        evidence_id = promote(
            {
                "contract_version": "1.0",
                "operation": "append",
                "record_kind": "evidence",
                "target_record_id": None,
                "context": common_context,
                "payload": {
                    "claim": "The invented PDF wheel response was reported.",
                    "evidence_type": "reported_result",
                    "quote": quote,
                    "source_page": {
                        "pdf_page": 1,
                        "printed_page": None,
                        "section": "Synthetic results",
                        "figure_or_table": None,
                    },
                    "locator": locator,
                    "support_scope": "The generated PDF wheel fixture only.",
                    "what_it_does_not_support": ["Other synthetic settings"],
                    "review_status": "ai_checked",
                    "fixture_origin": "synthetic_from_scratch",
                },
                "fixture_origin": "synthetic_from_scratch",
            }
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
                    "claim_candidate": "The PDF wheel response is universal.",
                    "reason": "The generated PDF contains one invented setting only.",
                    "source_page": {
                        "pdf_page": 1,
                        "printed_page": None,
                        "section": "Synthetic results",
                        "figure_or_table": None,
                    },
                    "locator": locator,
                    "resolution_status": "needs_resolution",
                    "review_status": "ai_checked",
                    "fixture_origin": "synthetic_from_scratch",
                },
                "fixture_origin": "synthetic_from_scratch",
            }
        )["record_id"]
        sections = [
            {"section_id": item["section_id"], "units": []}
            for item in intake["domain_profile"]["paper_card_sections"]
        ]
        sections[1]["units"].append(
            {
                "section_id": sections[1]["section_id"],
                "statement": "The generated PDF asks whether the invented response occurs.",
                "statement_type": "reported_result",
                "grounding_status": "grounded",
                "evidence_ids": [evidence_id],
                "boundary_refs": [queue_id],
                "source_page": {
                    "pdf_page": 1,
                    "printed_page": None,
                    "section": "Synthetic results",
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
                "fixture_origin": "synthetic_from_scratch",
            }
        )
        knowledge_before_context = {
            path.relative_to(workspace_root / "knowledge").as_posix(): path.read_bytes()
            for path in (workspace_root / "knowledge").rglob("*")
            if path.is_file()
        }
        context = _run_json(
            python,
            temporary_root,
            "paper",
            "context",
            "--workspace",
            str(config_path),
            "--paper-id",
            paper_id,
        )
        review_context = _run_json(
            python,
            temporary_root,
            "review",
            "context",
            "--workspace",
            str(config_path),
            "--paper-id",
            paper_id,
        )
        unit_ids = [
            unit["unit_id"]
            for section in context["paper_card"]["sections"]
            for unit in section["units"]
        ]
        if len(unit_ids) != 1:
            raise SystemExit("PDF wheel paper context did not return the Card Unit ID")
        if [item["evidence_id"] for item in context["evidence"]] != [evidence_id]:
            raise SystemExit("PDF wheel paper context did not return Evidence")
        if [item["queue_id"] for item in context["review_queue"]] != [queue_id]:
            raise SystemExit("PDF wheel paper context did not return review queue")
        if review_context["review_memory"] is not None or review_context["freshness"]["state"] != "absent":
            raise SystemExit("PDF wheel review context did not preserve the absent Review Memory state")
        if {
            path.relative_to(workspace_root / "knowledge").as_posix(): path.read_bytes()
            for path in (workspace_root / "knowledge").rglob("*")
            if path.is_file()
        } != knowledge_before_context:
            raise SystemExit("PDF wheel paper context changed managed workspace files")
        if _sha256(source) != source_before:
            raise SystemExit("PDF wheel parse changed the generated source PDF")

    print("wheel_pdf_smoke=success")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
