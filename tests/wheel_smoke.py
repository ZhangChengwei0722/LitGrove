from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import venv
from pathlib import Path


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
                "from research_kb.services import CompatibilityAdapterRegistry, CompatibilityInspectionService, ParseService, QuestionMappingService, RecordService, RegistryService; "
                "registry = SchemaRegistry(); "
                "assert registry.schema('mutation-request')['$id'].endswith('mutation-request'); "
                "assert registry.schema('compatibility-difference')['$id'].endswith('compatibility-difference'); "
                "assert registry.schema('compatibility-report')['$id'].endswith('compatibility-report'); "
                "assert registry.schema('question-mapping')['$id'].endswith('question-mapping'); "
                "assert LegacyReaderAdapter.__name__ == 'LegacyReaderAdapter'; "
                "assert CompatibilitySourceRef.__name__ == 'CompatibilitySourceRef'; "
                "assert CompatibilityAdapterRegistry.__name__ == 'CompatibilityAdapterRegistry'; "
                "assert CompatibilityInspectionService.__name__ == 'CompatibilityInspectionService'; "
                "assert QuestionMappingService.__name__ == 'QuestionMappingService'",
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
