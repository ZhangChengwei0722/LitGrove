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
from research_kb.errors import (
    UNSAFE_DIRECTORY_MODE,
    WORKSPACE_IDENTITY_CONFLICT,
    WORKSPACE_LAYOUT_CONFLICT,
    WORKSPACE_NOT_INITIALIZED,
    ResearchKBError,
    redact_absolute_paths,
)
from research_kb.guardian import GuardianService
from research_kb.mutation import load_mutation_request
from research_kb.parse.synthetic_text import SyntheticTextAdapter
from research_kb.privacy import scan_repository
from research_kb.services.records import RecordService
from research_kb.services.registry import RegistryService
from research_kb.services.parse import ParseService
from research_kb.services.bootstrap import WorkspaceBootstrapService
from research_kb.storage.json_io import read_jsonl
from research_kb.storage.transactions import MANUAL_RESOLUTION_ACTIONS, TransactionManager
from research_kb.workspace import WorkspaceLayout


ID_FIELDS = {
    "registry-paper": "paper_id",
    "evidence": "evidence_id",
    "review-queue": "queue_id",
    "process-event": "event_id",
    "guardian-report": "guardian_report_id",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="research-kb")
    parser.add_argument("--version", action="version", version=f"research-kb {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    workspace = commands.add_parser("workspace", help="initialize deterministic workspace layout")
    workspace_commands = workspace.add_subparsers(dest="workspace_command", required=True)
    workspace_init = workspace_commands.add_parser("init", help="validate and initialize one workspace")
    workspace_init.add_argument("--workspace", required=True, type=Path)
    workspace_init.add_argument("--dry-run", action="store_true")

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

    data = commands.add_parser("data", help="inspect deterministic structured stores")
    data_commands = data.add_subparsers(dest="data_command", required=True)
    check_jsonl = data_commands.add_parser("check-jsonl", help="validate every record in a JSONL store")
    check_jsonl.add_argument("--kind", required=True)
    check_jsonl.add_argument("--input", required=True, type=Path)
    check_jsonl.add_argument("--actor", choices=("agent", "cli", "user"), default="cli")

    record = commands.add_parser("record", help="promote validated mutation requests")
    record_commands = record.add_subparsers(dest="record_command", required=True)
    promote = record_commands.add_parser("promote", help="promote one candidate mutation request")
    promote.add_argument("--workspace", required=True, type=Path)
    promote.add_argument("--request", required=True, type=Path)
    promote.add_argument("--actor", choices=("agent", "cli", "user"), required=True)

    registry = commands.add_parser("registry", help="manage registered source references")
    registry_commands = registry.add_subparsers(dest="registry_command", required=True)
    registry_add = registry_commands.add_parser("add", help="register one read-only source asset")
    registry_add.add_argument("--workspace", required=True, type=Path)
    registry_add.add_argument("--root-id", required=True)
    registry_add.add_argument("--relative-path", required=True)
    registry_add.add_argument("--metadata", required=True, type=Path)

    parse = commands.add_parser("parse", help="run deterministic parse adapters")
    parse_commands = parse.add_subparsers(dest="parse_command", required=True)
    parse_run = parse_commands.add_parser("run", help="parse one registered source")
    parse_run.add_argument("--workspace", required=True, type=Path)
    parse_run.add_argument("--paper-id", required=True)
    parse_run.add_argument("--adapter", choices=("synthetic-text",), required=True)

    guardian = commands.add_parser("guardian", help="check workspace integrity")
    guardian_commands = guardian.add_subparsers(dest="guardian_command", required=True)
    guardian_check = guardian_commands.add_parser("check", help="run deterministic Guardian checks")
    guardian_check.add_argument("--workspace", required=True, type=Path)
    guardian_check.add_argument("--write-report", action="store_true")

    transaction = commands.add_parser("transaction", help="inspect or recover interrupted writes")
    transaction_commands = transaction.add_subparsers(dest="transaction_command", required=True)
    recover = transaction_commands.add_parser("recover", help="recover transaction journals by digest")
    recover.add_argument("--workspace", required=True, type=Path)
    recover.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    _configure_standard_streams()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "workspace" and args.workspace_command == "init":
            return _workspace_init(args)
        if args.command == "contract" and args.contract_command == "validate":
            return _contract_validate(args)
        if args.command == "privacy" and args.privacy_command == "scan":
            return _privacy_scan(args)
        if args.command == "data" and args.data_command == "check-jsonl":
            return _data_check_jsonl(args)
        if args.command == "record" and args.record_command == "promote":
            return _record_promote(args)
        if args.command == "registry" and args.registry_command == "add":
            return _registry_add(args)
        if args.command == "parse" and args.parse_command == "run":
            return _parse_run(args)
        if args.command == "guardian" and args.guardian_command == "check":
            return _guardian_check(args)
        if args.command == "transaction" and args.transaction_command == "recover":
            return _transaction_recover(args)
    except ResearchKBError as error:
        _write_json({"status": "error", "diagnostic": error.diagnostic.to_dict()}, stream=sys.stderr)
        if error.diagnostic.code in {"RKBC-001", "RKBC-003"}:
            return 3
        if error.diagnostic.code in {
            "RKBC-016",
            "RKBC-017",
            "RKBC-018",
            WORKSPACE_NOT_INITIALIZED,
            WORKSPACE_IDENTITY_CONFLICT,
            WORKSPACE_LAYOUT_CONFLICT,
            UNSAFE_DIRECTORY_MODE,
        }:
            return 4
        return 2
    except (OSError, ValueError, json.JSONDecodeError, yaml.YAMLError) as error:
        _write_json({"status": "error", "error": redact_absolute_paths(str(error))}, stream=sys.stderr)
        return 2
    parser.error("unsupported command")
    return 2


def _workspace_init(args: argparse.Namespace) -> int:
    result = WorkspaceBootstrapService(args.workspace).run(dry_run=args.dry_run)
    _write_json(result.to_dict())
    return result.exit_code


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


def _data_check_jsonl(args: argparse.Namespace) -> int:
    try:
        records = read_jsonl(
            args.input,
            record_kind=args.kind,
            missing_ok=False,
            id_field=ID_FIELDS.get(args.kind),
        )
    except ResearchKBError as error:
        _write_json({"status": "failure", "records": 0, "diagnostics": [error.diagnostic.to_dict()]})
        return 1
    diagnostics = []
    for record in records:
        diagnostics.extend(validate_record(args.kind, record, actor=args.actor))
    _write_json({
        "status": "success" if not diagnostics else "failure",
        "records": len(records),
        "diagnostics": [item.to_dict() for item in diagnostics],
    })
    if any(item.code in {"RKBC-001", "RKBC-003"} for item in diagnostics):
        return 3
    return 0 if not diagnostics else 1


def _transaction_recover(args: argparse.Namespace) -> int:
    layout = WorkspaceLayout.load(args.workspace)
    actions = TransactionManager(layout).recover(dry_run=args.dry_run)
    needs_resolution = any(item["action"] in MANUAL_RESOLUTION_ACTIONS for item in actions)
    _write_json({
        "status": "needs_resolution" if needs_resolution else "success",
        "dry_run": args.dry_run,
        "actions": actions,
    })
    return 4 if needs_resolution else 0


def _record_promote(args: argparse.Namespace) -> int:
    layout = WorkspaceLayout.load(args.workspace)
    request = load_mutation_request(args.request)
    record, transaction = RecordService(layout).promote(request, actor=args.actor)
    _write_json({
        "status": "success",
        "record_kind": request.record_kind,
        "record_id": _record_id(request.record_kind, record),
        "event_id": transaction.event_id,
        "target": layout.target_relative_path(transaction.target),
    })
    return 0


def _registry_add(args: argparse.Namespace) -> int:
    layout = WorkspaceLayout.load(args.workspace)
    metadata = _load_mapping(args.metadata)
    paper, transaction = RegistryService(layout).add(
        root_id=args.root_id,
        relative_path=args.relative_path,
        metadata=metadata,
        actor="cli",
    )
    _write_json({
        "status": "success",
        "paper_id": paper["paper_id"],
        "duplicate_candidate_ids": paper["duplicate_candidate_ids"],
        "event_id": transaction.event_id,
        "target": layout.target_relative_path(transaction.target),
    })
    return 0


def _parse_run(args: argparse.Namespace) -> int:
    layout = WorkspaceLayout.load(args.workspace)
    adapter = SyntheticTextAdapter()
    pages, transaction = ParseService(layout).run(
        paper_id=args.paper_id,
        adapter=adapter,
        actor="cli",
    )
    _write_json({
        "status": "success",
        "paper_id": args.paper_id,
        "parse_run_id": transaction.event_id,
        "pages": len(pages),
        "target": layout.target_relative_path(transaction.target),
    })
    return 0


def _guardian_check(args: argparse.Namespace) -> int:
    layout = WorkspaceLayout.load(args.workspace)
    result = GuardianService(layout).check(write_report=args.write_report)
    _write_json({
        "status": result.report["status"],
        "guardian_report_id": result.report["guardian_report_id"],
        "findings": result.report["findings"],
        "report_written": result.transaction is not None,
        "event_id": result.transaction.event_id if result.transaction is not None else None,
    })
    return 0 if result.report["status"] == "success" else 1


def _record_id(kind: str, record: dict[str, Any]) -> str:
    id_field = {
        "registry-paper": "paper_id",
        "paper-card": "paper_id",
        "evidence": "evidence_id",
        "review-queue": "queue_id",
    }[kind]
    return record[id_field]


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
