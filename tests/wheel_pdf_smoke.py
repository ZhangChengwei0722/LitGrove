from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
import tempfile
import tomllib
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.pdf_helpers import write_synthetic_pdf


def _isolated_environment() -> dict[str, str]:
    environment = os.environ.copy()
    for variable in (
        "PIP_INDEX_URL",
        "PIP_EXTRA_INDEX_URL",
        "PIP_TRUSTED_HOST",
        "PIP_FIND_LINKS",
        "PIP_CERT",
        "PIP_CLIENT_CERT",
    ):
        environment.pop(variable, None)
    environment["PYTHONNOUSERSITE"] = "1"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PIP_NO_CACHE_DIR"] = "1"
    environment["PIP_CONFIG_FILE"] = "NUL" if os.name == "nt" else "/dev/null"
    environment.pop("PYTHONPATH", None)
    return environment


def _run_isolated(*args, **kwargs):
    kwargs["env"] = _isolated_environment()
    return subprocess.run(*args, **kwargs)


def _native_lock(root: Path, profile: str) -> Path:
    if profile not in {"runtime", "pdf"}:
        raise ValueError(f"unsupported PDF wheel smoke profile: {profile}")
    if sys.implementation.name != "cpython" or sys.version_info[:2] not in ((3, 11), (3, 12)):
        raise SystemExit("PDF wheel smoke requires CPython 3.11 or 3.12")
    machine = platform.machine().lower()
    if sys.platform == "win32" and machine in {"amd64", "x86_64"}:
        platform_tag = "win_amd64"
    elif sys.platform.startswith("linux") and machine in {"x86_64", "amd64"}:
        platform_tag = "linux_x86_64"
    else:
        raise SystemExit(f"unsupported PDF wheel smoke platform tuple: {sys.platform}/{machine}")
    python_tag = f"py{sys.version_info.major}{sys.version_info.minor}"
    lock = root / "requirements" / "locks" / platform_tag / python_tag / f"{profile}.txt"
    if not lock.is_file():
        raise SystemExit(f"native PDF wheel smoke lock is missing: {lock}")
    return lock


def _project_version(root: Path) -> str:
    with (root / "pyproject.toml").open("rb") as stream:
        version = tomllib.load(stream)["project"]["version"]
    if not isinstance(version, str) or not version:
        raise SystemExit("pyproject.toml does not declare a project version")
    return version


def _distribution_directory(root: Path) -> Path:
    override = os.environ.get("RESEARCH_KB_DIST_DIR")
    if override is None:
        return root / "dist"
    directory = Path(override)
    if not directory.is_absolute():
        raise SystemExit("RESEARCH_KB_DIST_DIR must be an absolute path")
    if not directory.is_dir():
        raise SystemExit("RESEARCH_KB_DIST_DIR must be an existing directory")
    if directory.is_symlink() or directory.resolve(strict=True) != directory:
        raise SystemExit("RESEARCH_KB_DIST_DIR must not be a symlink")
    return directory


def _wheel_for_version(root: Path, version: str) -> Path:
    normalized_version = version.replace("-", "_")
    distribution_directory = _distribution_directory(root)
    wheels = sorted(
        distribution_directory.glob(f"research_kb_core-{normalized_version}-*.whl")
    )
    if not wheels:
        raise SystemExit(f"build the expected {version} PDF wheel before running the smoke test")
    if len(wheels) != 1:
        raise SystemExit(f"expected one {version} PDF wheel, found {len(wheels)}")
    return wheels[0]


def _install_locked(python: Path, root: Path, wheel: Path, profile: str) -> None:
    bootstrap = root / "tools" / "release-lock-bootstrap.txt"
    if not bootstrap.is_file():
        raise SystemExit(f"bootstrap lock is missing: {bootstrap}")
    lock = _native_lock(root, profile)
    commands = (
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--isolated",
            "--index-url",
            "https://pypi.org/simple",
            "--no-cache-dir",
            "--require-hashes",
            "--no-deps",
            "--only-binary=:all:",
            "-r",
            str(bootstrap),
        ],
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--isolated",
            "--index-url",
            "https://pypi.org/simple",
            "--no-cache-dir",
            "--require-hashes",
            "--no-deps",
            "--only-binary=:all:",
            "-r",
            str(lock),
        ],
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--no-index",
            "--no-deps",
            "--no-compile",
            str(wheel),
        ],
    )
    for command in commands:
        _run_isolated(command, cwd=root, check=True)


_INSTALLED_PAYLOAD_SCRIPT = r"""
import base64
import csv
import hashlib
import importlib.metadata
import json
import sys
import sysconfig
from pathlib import Path, PurePosixPath, PureWindowsPath

root = Path(sys.argv[1]).resolve()
expected_version = sys.argv[2]
distribution = importlib.metadata.distribution("research-kb-core")
if not distribution.version or distribution.version != expected_version:
    raise SystemExit(
        f"installed Core distribution version mismatch: {distribution.version!r}"
    )
import research_kb

module_file = Path(research_kb.__file__).resolve()
purelib = Path(sysconfig.get_paths()["purelib"]).resolve()
scripts = Path(sysconfig.get_paths()["scripts"]).resolve()
if not module_file.is_relative_to(purelib):
    raise SystemExit(f"research_kb imported outside venv site-packages: {module_file}")
for checkout_root in (root, root / "src"):
    if module_file.is_relative_to(checkout_root.resolve()):
        raise SystemExit(f"research_kb imported from the checkout: {module_file}")

files = distribution.files or ()
record_entries = [
    PurePosixPath(item.as_posix())
    for item in files
    if item.as_posix().endswith(".dist-info/RECORD")
]
if len(record_entries) != 1:
    raise SystemExit(f"expected one Core RECORD entry, found {len(record_entries)}")
record_entry = record_entries[0]
record_path = Path(distribution.locate_file(record_entry)).resolve()
if not record_path.is_file() or not record_path.is_relative_to(purelib):
    raise SystemExit(f"Core RECORD is outside installed site-packages: {record_path}")

def is_under(path, root_path):
    return path == root_path or root_path in path.parents


checked = 0
record_rows = 0
with record_path.open("r", encoding="utf-8", newline="") as stream:
    for row in csv.reader(stream):
        if len(row) != 3 or not row[0]:
            raise SystemExit(f"malformed Core RECORD row: {row!r}")
        recorded_name = row[0]
        posix_name = PurePosixPath(recorded_name)
        windows_name = PureWindowsPath(recorded_name)
        if (
            "\\" in recorded_name
            or posix_name.is_absolute()
            or windows_name.is_absolute()
            or windows_name.root
            or windows_name.drive
        ):
            raise SystemExit(f"unsafe Core RECORD path: {recorded_name}")
        candidate = Path(distribution.locate_file(posix_name)).resolve()
        if not any(is_under(candidate, allowed) for allowed in (purelib, scripts)):
            raise SystemExit(f"Core RECORD path escapes the installed distribution: {recorded_name}")
        if not candidate.is_file():
            raise SystemExit(f"Core RECORD file is missing: {recorded_name}")
        if posix_name == record_entry:
            record_rows += 1
            continue
        if not row[1] or not row[2]:
            raise SystemExit(f"Core RECORD lacks hash or size: {recorded_name}")
        try:
            algorithm, expected_digest = row[1].split("=", 1)
            actual_digest = base64.urlsafe_b64encode(
                hashlib.new(algorithm, candidate.read_bytes()).digest()
            ).rstrip(b"=").decode("ascii")
            expected_size = int(row[2])
        except (ValueError, TypeError) as error:
            raise SystemExit(f"invalid Core RECORD digest or size: {recorded_name}") from error
        if actual_digest != expected_digest:
            raise SystemExit(f"Core RECORD hash mismatch: {recorded_name}")
        if candidate.stat().st_size != expected_size:
            raise SystemExit(f"Core RECORD size mismatch: {recorded_name}")
        checked += 1
if record_rows != 1:
    raise SystemExit(f"expected one Core RECORD row, found {record_rows}")

print(json.dumps({"version": distribution.version, "module_file": str(module_file), "checked_files": checked}))
"""


def _assert_installed_payload(
    python: Path, cwd: Path, root: Path, expected_version: str
) -> dict:
    try:
        completed = _run_isolated(
            [str(python), "-c", _INSTALLED_PAYLOAD_SCRIPT, str(root), expected_version],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except subprocess.CalledProcessError as error:
        raise SystemExit(
            "installed payload verification failed:\n"
            f"stdout:\n{error.stdout or ''}\n"
            f"stderr:\n{error.stderr or ''}"
        ) from error
    return json.loads(completed.stdout)


def _run_json(python: Path, cwd: Path, *args: str) -> dict:
    completed = _run_isolated(
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
    completed = _run_isolated(
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
    expected_version = _project_version(ROOT)
    wheel = _wheel_for_version(ROOT, expected_version)
    _native_lock(ROOT, "pdf")

    with tempfile.TemporaryDirectory(prefix="research-kb-pdf-wheel-smoke-") as temporary:
        temporary_root = Path(temporary)
        environment = temporary_root / "venv"
        venv.EnvBuilder(with_pip=True).create(environment)
        python = environment / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        _install_locked(python, ROOT, wheel, "pdf")
        _assert_installed_payload(python, temporary_root, ROOT, expected_version)
        installed_version = _run_isolated(
            [str(python), "-c", "from importlib.metadata import version; print(version('pdfplumber'))"],
            cwd=temporary_root,
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
        if not {"adequacy gate", "adequacy show", "discovery search", "identity list", "intake inspect", "intake inspect-acquired", "job list", "job show", "manuscript inspect", "obsidian render --dry-run", "obsidian status", "paper context", "review context", "source list", "source scan", "step7 context", "step7 render"}.issubset(capability["read_commands"]):
            raise SystemExit("PDF wheel capability report lacks deterministic intake/context reads")
        if not {"adequacy assess", "obsidian render", "trunk advance"}.issubset(capability["write_commands"]):
            raise SystemExit("PDF wheel capability report lacks Source Adequacy writes")
        if capability["discovery_connectors"] != [{"connector": "europe-pmc", "availability": "available", "network_required": True}]:
            raise SystemExit("PDF wheel capability report lacks the Europe PMC connector")
        if capability["features"]["review_runtime"] is not True or capability["features"]["step7_runtime"] is not True or capability["features"]["on_demand_discovery"] is not True or capability["features"]["manuscript_projection"] is not True or capability["features"]["pipeline_jobs"] is not True or capability["features"]["source_asset_runtime"] is not True or capability["features"]["registry_identity_correction"] is not True or capability["features"]["source_adequacy"] is not True or capability["features"]["deterministic_trunk"] is not True or capability["features"]["deterministic_intake_application"] is not True:
            raise SystemExit("PDF wheel capability report lacks Review Memory, Step 7 or discovery runtime")
        if not all(
            capability["features"][name]
            for name in (
                "workspace_materialization",
                "trusted_parse_authority",
                "supervised_pdf_parse",
                "trusted_parse_intake_application",
            )
        ):
            raise SystemExit("PDF wheel capability report lacks B1 Core services")
        if "trusted-parse-authority" not in capability["operational_record_kinds"]:
            raise SystemExit("PDF wheel capability report lacks trusted Parse authority records")
        for command in ("context", "render"):
            _run_isolated(
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
                "local_inbox": "./sources/inbox",
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

        facade_source = write_synthetic_pdf(
            sources / "wheel-facade-primary.pdf",
            ["Synthetic installed-wheel facade intake."],
        )
        (sources / "inbox").mkdir()
        facade_request = {
            "idempotency_key": "pdf-wheel-facade-1",
            "requested_operation": "basic_paper_card",
            "document_route": "primary",
            "route_reason": None,
            "bibliography": {
                "title": "Synthetic installed-wheel facade intake",
                "authors": ["Fixture Author"],
                "year": 2026,
                "doi": None,
            },
            "expected_sha256": _sha256(facade_source),
            "expected_size_bytes": facade_source.stat().st_size,
        }
        _run_isolated(
            [
                str(python),
                "-c",
                (
                    "from pathlib import Path; "
                    "from research_kb.application import APPLICATION_SERVICE_INTERFACE_VERSION; "
                    "from research_kb.services import CapabilityService, DeterministicIntakeApplicationService, IntakeSourceAdequacyResolutionApplicationService, WorkspaceSessionService; "
                    "assert APPLICATION_SERVICE_INTERFACE_VERSION == '1.23'; "
                    "assert IntakeSourceAdequacyResolutionApplicationService.__name__ == 'IntakeSourceAdequacyResolutionApplicationService'; "
                    "assert CapabilityService().show()['features']['intake_source_adequacy_resolution'] is True; "
                    f"session = WorkspaceSessionService({{'wheel': Path({str(config_path)!r})}}).open('wheel'); "
                    f"source = Path({str(facade_source)!r}); request = {facade_request!r}; "
                    "stream = source.open('rb'); "
                    "result = DeterministicIntakeApplicationService().start_upload(session, stream, request); "
                    "stream.close(); "
                    "assert result['pipeline']['status'] == 'completed'; "
                    "assert result['pipeline']['current_node'] == 'primary_semantic_gate'; "
                    "assert result['source_adequacy']['gate_status'] == 'allowed'; "
                    "assert result['persistent_writes'] > 0"
                ),
            ],
            cwd=temporary_root,
            check=True,
        )

    print("wheel_pdf_smoke=success")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
