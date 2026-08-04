from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence

from benchmarks.p11_operational_density.generator import generate_workspace, inspect_generated_workspace
from benchmarks.p11_operational_density.measurement import (
    measure_backup_restore,
    measure_operational_reads,
    measure_startup,
)
from benchmarks.p11_operational_density.profiles import profile_by_id
from research_kb.storage.json_io import serialize_json


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "profile":
            profile = profile_by_id(args.profile)
            result: dict[str, Any] = {"profile_id": profile.profile_id, "parameters": profile.parameters()}
        elif args.command == "generate":
            generated = generate_workspace(
                Path(args.target),
                profile_id=args.profile,
                validate_full_bundle=not args.skip_full_validation,
            )
            result = {
                "status": "success",
                "profile_id": generated.profile.profile_id,
                "parameters": generated.profile.parameters(),
                "fixture_origin": "synthetic_from_scratch",
            }
        elif args.command == "inspect":
            generated = inspect_generated_workspace(
                Path(args.target),
                validate_full_bundle=args.full_validation,
            )
            result = {
                "status": "success",
                "profile_id": generated.profile.profile_id,
                "parameters": generated.profile.parameters(),
                "fixture_origin": "synthetic_from_scratch",
            }
        elif args.command == "measure-startup":
            result = measure_startup(Path(args.target), repetitions=args.repetitions)
        elif args.command == "measure-reads":
            generated = inspect_generated_workspace(Path(args.target))
            result = measure_operational_reads(generated, repetitions=args.repetitions)
        elif args.command == "measure-backup-restore":
            generated = inspect_generated_workspace(Path(args.target), validate_full_bundle=True)
            result = measure_backup_restore(
                generated,
                archive_path=Path(args.archive),
                restore_target=Path(args.restore_target),
            )
        elif args.command == "measure-maintenance":
            target = Path(args.target)
            inspect_generated_workspace(target, validate_full_bundle=True)
            result = _measure_maintenance_isolated(target)
        else:
            parser.error("unknown command")
            return 2
        if getattr(args, "output", None):
            _write_absent(Path(args.output), result)
        sys.stdout.buffer.write(serialize_json(_summary(result, bool(getattr(args, "output", None)))))
        return 0 if result.get("passed", True) else 1
    except (OSError, RuntimeError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="P11 operational density and recovery benchmark")
    commands = parser.add_subparsers(dest="command", required=True)
    profile = commands.add_parser("profile")
    profile.add_argument("--profile", required=True)
    generate = commands.add_parser("generate")
    generate.add_argument("--profile", required=True)
    generate.add_argument("--target", required=True)
    generate.add_argument("--skip-full-validation", action="store_true")
    inspect = commands.add_parser("inspect")
    inspect.add_argument("--target", required=True)
    inspect.add_argument("--full-validation", action="store_true")
    startup = commands.add_parser("measure-startup")
    startup.add_argument("--target", required=True)
    startup.add_argument("--repetitions", type=int, default=3)
    startup.add_argument("--output")
    reads = commands.add_parser("measure-reads")
    reads.add_argument("--target", required=True)
    reads.add_argument("--repetitions", type=int, default=5)
    reads.add_argument("--output")
    backup = commands.add_parser("measure-backup-restore")
    backup.add_argument("--target", required=True)
    backup.add_argument("--archive", required=True)
    backup.add_argument("--restore-target", required=True)
    backup.add_argument("--output")
    maintenance = commands.add_parser("measure-maintenance")
    maintenance.add_argument("--target", required=True)
    maintenance.add_argument("--output")
    return parser


def _write_absent(path: Path, value: dict[str, Any]) -> None:
    if not path.is_absolute() or not path.parent.is_dir() or path.exists():
        raise ValueError("receipt output must be one absent absolute path with an existing parent")
    path.write_bytes(serialize_json(value))


def _measure_maintenance_isolated(target: Path) -> dict[str, Any]:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "benchmarks.p11_operational_density.maintenance_worker",
            str(target),
        ],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=600,
    )
    try:
        result = json.loads(completed.stdout)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("isolated maintenance measurement did not return valid JSON") from error
    if completed.returncode not in {0, 1} or not isinstance(result, dict):
        raise RuntimeError("isolated maintenance measurement failed")
    return result


def _summary(result: dict[str, Any], receipt_written: bool) -> dict[str, Any]:
    return {
        "status": "success" if result.get("passed", True) else "failure",
        "profile_id": result.get("profile_id"),
        "passed": result.get("passed"),
        "receipt_written": receipt_written,
        "contains_private_paths": False,
        "result": None if receipt_written else result,
    }


if __name__ == "__main__":
    raise SystemExit(main())
