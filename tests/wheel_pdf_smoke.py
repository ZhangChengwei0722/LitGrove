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
        metadata_path = workspace_root / "metadata.json"
        profile_path.write_text(json.dumps(profile) + "\n", encoding="utf-8", newline="\n")
        config_path.write_text(json.dumps(workspace) + "\n", encoding="utf-8", newline="\n")
        metadata_path.write_text(
            json.dumps({"fixture_origin": "synthetic_from_scratch"}) + "\n",
            encoding="utf-8",
            newline="\n",
        )

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
        paper_id = _run_json(
            python,
            temporary_root,
            "registry",
            "add",
            "--workspace",
            str(config_path),
            "--root-id",
            "pdf-wheel-sources",
            "--relative-path",
            source.name,
            "--metadata",
            str(metadata_path),
        )["paper_id"]
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
            "pdfplumber",
        )
        installed_version = subprocess.run(
            [str(python), "-c", "from importlib.metadata import version; print(version('pdfplumber'))"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        ).stdout.strip()
        if parse_output["parser"] != {"adapter": "pdfplumber", "version": installed_version}:
            raise SystemExit("PDF wheel parser identity does not match installed package metadata")
        if parse_output["pages"] != 1:
            raise SystemExit("PDF wheel did not persist one row for the generated page")
        if _sha256(source) != source_before:
            raise SystemExit("PDF wheel parse changed the generated source PDF")

    print("wheel_pdf_smoke=success")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
