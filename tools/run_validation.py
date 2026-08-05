from __future__ import annotations

import argparse
import json
import platform
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.validation import (
    L3_DIRECTORIES,
    REPOSITORY_ROOT,
    collect_nodeids,
    load_manifest,
    marker_for,
    nodeid_digest,
    selectors_for,
    verify_manifest,
    verify_nodeid_coverage,
)


def _write_receipt(path: Path, receipt: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def _portable_text(value: str) -> str:
    replacements = (
        (str(REPOSITORY_ROOT), "<repository-root>"),
        (str(Path(sys.executable).parent), "<python-runtime>"),
    )
    for source, target in replacements:
        value = value.replace(source, target).replace(source.replace("\\", "/"), target)
    value = re.sub(r"(?i)[a-z]:[\\/][^\s\"']+", "<redacted-windows-path>", value)
    posix_home_prefix = "/" + "home" + "/"
    value = re.sub(re.escape(posix_home_prefix) + r"[^\s\"']+", "<redacted-posix-home-path>", value)
    return value


def _portable_command(command: list[str]) -> list[str]:
    portable: list[str] = []
    for argument in command:
        if argument == sys.executable:
            portable.append("python")
            continue
        path = Path(argument)
        if path.is_absolute():
            try:
                portable.append(path.resolve().relative_to(REPOSITORY_ROOT).as_posix())
            except ValueError:
                portable.append(f"<absolute-path>/{path.name}")
            continue
        portable.append(_portable_text(argument))
    return portable


def _run(command: list[str], *, timeout_seconds: float | None = None) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        result = subprocess.run(
            command,
            cwd=REPOSITORY_ROOT,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return {
            "command": _portable_command(command),
            "return_code": 124,
            "duration_seconds": round(time.perf_counter() - started, 3),
            "timed_out": True,
            "timeout_seconds": timeout_seconds,
        }
    return {
        "command": _portable_command(command),
        "return_code": result.returncode,
        "duration_seconds": round(time.perf_counter() - started, 3),
        "timed_out": False,
        "timeout_seconds": timeout_seconds,
    }


def _changed_markdown(base: str) -> list[str]:
    result = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=ACMRT", f"{base}...HEAD", "--", "*.md"],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "could not list changed Markdown files")
    return [line for line in result.stdout.splitlines() if line]


def _check_local_links(paths: list[str]) -> None:
    import re

    missing: list[str] = []
    for raw_path in paths:
        path = REPOSITORY_ROOT / raw_path
        for target in re.findall(r"]\(([^)#]+)(?:#[^)]+)?\)", path.read_text(encoding="utf-8")):
            if "://" in target or target.startswith("mailto:"):
                continue
            if not (path.parent / target).resolve().exists():
                missing.append(f"{raw_path}: {target}")
    if missing:
        raise RuntimeError(f"missing relative Markdown links: {missing}")


def _commands_for(level: str, shard: str | None, selectors: list[str], build_dir: Path) -> list[list[str]]:
    python = sys.executable
    if level == "L0":
        return [
            ["git", "diff", "--check"],
            [python, "-m", "research_kb", "privacy", "scan", "--root", "."],
            [python, "-m", "build", "--outdir", str(build_dir)],
        ]
    if level == "L1":
        if not selectors:
            raise ValueError("L1 requires at least one --selector")
        return [[python, "-m", "pytest", "-p", "no:cacheprovider", "-q", *selectors]]
    if level == "L2":
        return [
            [
                python,
                "-m",
                "pytest",
                "-p",
                "no:cacheprovider",
                "-q",
                "tests/unit",
                "tests/contract",
                "-m",
                "not slow and not scale and not serial",
            ],
        ]
    if level == "L3":
        if not shard:
            raise ValueError("L3 requires --shard")
        report = verify_manifest()
        if shard == "all":
            return [
                [
                    python,
                    "-m",
                    "pytest",
                    "-p",
                    "no:cacheprovider",
                    "-q",
                    *([] if marker_for(current) is None else ["-m", marker_for(current)]),
                    *selectors_for(current),
                ]
                for current in report.l3_shards
            ]
        if shard not in report.l3_shards:
            raise ValueError(f"L3 shard is not declared: {shard}")
        marker = marker_for(shard)
        return [
            [
                python,
                "-m",
                "pytest",
                "-p",
                "no:cacheprovider",
                "-q",
                *([] if marker is None else ["-m", marker]),
                *selectors_for(shard),
            ]
        ]
    if level == "L4":
        report = verify_manifest()
        selected_shard = shard or report.scale_shard
        if selected_shard != report.scale_shard:
            raise ValueError(f"L4 requires the {report.scale_shard} shard")
        return [
            [python, "-m", "pytest", "-p", "no:cacheprovider", "-q", *selectors_for(selected_shard)]
        ]
    raise ValueError(f"unsupported validation level: {level}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run repository validation levels and stable test shards.")
    parser.add_argument("--level", choices=("L0", "L1", "L2", "L3", "L4"))
    parser.add_argument("--shard")
    parser.add_argument("--selector", action="append", default=[])
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--base", default="origin/main")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--collect-nodeids", action="store_true")
    parser.add_argument("--timeout-seconds", type=float)
    args = parser.parse_args()
    if args.timeout_seconds is not None and args.timeout_seconds <= 0:
        parser.error("--timeout-seconds must be greater than zero")

    started_at = datetime.now(timezone.utc)
    receipt: dict[str, Any] = {
        "schema_version": "test-validation-receipt@1.0",
        "started_at": started_at.isoformat(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "level": args.level,
        "shard": args.shard,
        "timeout_seconds": args.timeout_seconds,
        "receipt_path": _portable_text(args.receipt.as_posix()),
    }
    overall_started = time.perf_counter()
    return_code = 1
    timed_out = False
    try:
        if args.verify:
            verification = verify_nodeid_coverage() if args.collect_nodeids else verify_manifest().as_dict()
            receipt["verification"] = verification
            receipt["commands"] = []
            return_code = 0
        else:
            if not args.level:
                raise ValueError("--level is required unless --verify is used")
            with tempfile.TemporaryDirectory(prefix="research-kb-validation-build-") as temp_dir:
                if args.level == "L0":
                    markdown = _changed_markdown(args.base)
                    _check_local_links(markdown)
                    receipt["changed_markdown"] = markdown
                commands = _commands_for(args.level, args.shard, args.selector, Path(temp_dir))
                receipt["commands"] = []
                if args.level in {"L1", "L2", "L3", "L4"}:
                    selected = args.selector
                    selected_marker = None
                    if args.level == "L2":
                        selected = commands[0][6:]
                    elif args.level == "L3" and args.shard == "all":
                        selected = list(L3_DIRECTORIES)
                    elif args.level in {"L3", "L4"}:
                        selected = selectors_for(args.shard or verify_manifest().scale_shard)
                        if args.level == "L3":
                            selected_marker = marker_for(args.shard or "")
                    nodeids = collect_nodeids(selected, marker=selected_marker)
                    receipt["selected_node_count"] = len(nodeids)
                    receipt["selected_nodeid_sha256"] = nodeid_digest(nodeids)
                print(
                    json.dumps(
                        {
                            "level": args.level,
                            "shard": args.shard,
                            "commands": [_portable_command(command) for command in commands],
                            "receipt": _portable_text(args.receipt.as_posix()),
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )
                return_code = 0
                for command in commands:
                    result = _run(command, timeout_seconds=args.timeout_seconds)
                    receipt["commands"].append(result)
                    if result["timed_out"]:
                        timed_out = True
                        return_code = int(result["return_code"])
                        break
                    if result["return_code"] != 0:
                        return_code = int(result["return_code"])
                        break
        receipt["status"] = "timed_out" if timed_out else "passed" if return_code == 0 else "failed"
        return return_code
    except Exception as error:
        return_code = 2
        receipt["status"] = "error"
        receipt["error"] = _portable_text(f"{type(error).__name__}: {error}")
        print(receipt["error"], file=sys.stderr)
        return 2
    finally:
        receipt["return_code"] = return_code
        receipt["duration_seconds"] = round(time.perf_counter() - overall_started, 3)
        receipt["completed_at"] = datetime.now(timezone.utc).isoformat()
        _write_receipt(args.receipt, receipt)


if __name__ == "__main__":
    raise SystemExit(main())
