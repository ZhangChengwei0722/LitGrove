from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

import yaml

from research_kb import __version__
from research_kb.contracts.registry import SchemaRegistry
from research_kb.contracts.validator import validate_bundle, validate_record
from research_kb.errors import ResearchKBError
from research_kb.privacy import scan_repository


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="research-kb")
    parser.add_argument("--version", action="version", version=f"research-kb {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    contract = commands.add_parser("contract", help="validate public contract records")
    contract_commands = contract.add_subparsers(dest="contract_command", required=True)
    validate = contract_commands.add_parser("validate", help="validate one record and optional bundle")
    validate.add_argument("--kind", required=True)
    validate.add_argument("--input", required=True, type=Path)
    validate.add_argument("--bundle", type=Path)
    validate.add_argument("--actor", choices=("agent", "cli", "user"), default="agent")

    privacy = commands.add_parser("privacy", help="run privacy checks")
    privacy_commands = privacy.add_subparsers(dest="privacy_command", required=True)
    scan = privacy_commands.add_parser("scan", help="scan repository files and build artifacts")
    scan.add_argument("--root", required=True, type=Path)
    scan.add_argument("--allowlist", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    _configure_standard_streams()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "contract" and args.contract_command == "validate":
            return _contract_validate(args)
        if args.command == "privacy" and args.privacy_command == "scan":
            return _privacy_scan(args)
    except (OSError, ValueError, json.JSONDecodeError, yaml.YAMLError, ResearchKBError) as error:
        _write_json({"status": "error", "error": str(error)}, stream=sys.stderr)
        return 2
    parser.error("unsupported command")
    return 2


def _contract_validate(args: argparse.Namespace) -> int:
    registry = SchemaRegistry()
    record = _load_mapping(args.input)
    diagnostics = validate_record(args.kind, record, registry=registry, actor=args.actor)
    if args.bundle is not None:
        bundle = _load_mapping(args.bundle)
        diagnostics.extend(validate_bundle(bundle, registry=registry, actor=args.actor))
    unique = []
    seen = set()
    for diagnostic in diagnostics:
        key = (diagnostic.code, diagnostic.record_kind, diagnostic.record_id, diagnostic.json_path, diagnostic.message)
        if key not in seen:
            seen.add(key)
            unique.append(diagnostic)
    _write_json({"status": "success" if not unique else "failure", "diagnostics": [item.to_dict() for item in unique]})
    if any(item.code == "RKBC-001" or item.code == "RKBC-003" for item in unique):
        return 3
    return 0 if not unique else 1


def _privacy_scan(args: argparse.Namespace) -> int:
    result = scan_repository(args.root, args.allowlist)
    _write_json(
        {
            "status": "success" if result.ok else "failure",
            "expected_findings": len(result.expected),
            "unexpected_findings": [
                {"path": item.path, "finding_type": item.finding_type, "detail": item.detail}
                for item in result.unexpected
            ],
        }
    )
    return 0 if result.ok else 1


def _load_mapping(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    loaded = json.loads(text) if path.suffix.lower() == ".json" else yaml.safe_load(text)
    if not isinstance(loaded, dict):
        raise ValueError("input root must be a mapping")
    return loaded


def _write_json(value: dict[str, Any], stream: Any | None = None) -> None:
    output = sys.stdout if stream is None else stream
    output.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def _configure_standard_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="strict")
