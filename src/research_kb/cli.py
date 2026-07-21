from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Sequence

import yaml

from research_kb import __version__
from research_kb.cli_input import read_bounded_json_object
from research_kb.discovery import DiscoveryConnector
from research_kb.discovery.europe_pmc import EuropePmcConnector
from research_kb.contracts.registry import SchemaRegistry
from research_kb.bundle import load_workspace_entries, records_of_kind, validate_workspace_entries
from research_kb.contracts.validator import validate_bundle, validate_record
from research_kb.errors import (
    UNSAFE_DIRECTORY_MODE,
    UNRESOLVED_REFERENCE,
    PROTECTED_INPUT_CHANGED,
    WORKSPACE_IDENTITY_CONFLICT,
    WORKSPACE_LAYOUT_CONFLICT,
    WORKSPACE_LAYOUT_UPGRADE_REQUIRED,
    WORKSPACE_NOT_INITIALIZED,
    Diagnostic,
    ResearchKBError,
    redact_absolute_paths,
)
from research_kb.guardian import GuardianService
from research_kb.identifiers import Namespace, validate_id
from research_kb.mutation import load_mutation_request, mutation_request_from_mapping
from research_kb.parse.pdfplumber_adapter import PdfPlumberAdapter
from research_kb.parse.synthetic_text import SyntheticTextAdapter
from research_kb.privacy import scan_repository
from research_kb.services.records import RecordService
from research_kb.services.capability import CapabilityService
from research_kb.services.parse_read import ParseReadService
from research_kb.services.paper_status import PaperStatusService
from research_kb.services.paper_context import PaperContextService
from research_kb.services.review_context import ReviewContextService
from research_kb.services.question_view import QuestionReadingViewService
from research_kb.services.step7_context import Step7ContextService
from research_kb.services.step7_view import Step7ReadingViewService
from research_kb.services.registry import RegistryService
from research_kb.services.parse import ParseService
from research_kb.services.bootstrap import WorkspaceBootstrapService
from research_kb.services.compatibility import CompatibilityAdapterRegistry, CompatibilityInspectionService
from research_kb.services.intake_inspect import IntakeInspectService
from research_kb.services.discovery import DiscoveryConnectorRegistry, DiscoveryService
from research_kb.services.discovery_candidate import DiscoveryCandidateService
from research_kb.compatibility import LegacyReaderAdapter
from research_kb.storage.json_io import read_jsonl, serialize_json
from research_kb.storage.transactions import MANUAL_RESOLUTION_ACTIONS, TransactionManager
from research_kb.workspace import WorkspaceLayout


ID_FIELDS = {
    "registry-paper": "paper_id",
    "evidence": "evidence_id",
    "review-queue": "queue_id",
    "process-event": "event_id",
    "guardian-report": "guardian_report_id",
    "question-mapping": "question_id",
    "step7-synthesis": "candidate_id",
    "step7-review-angle": "candidate_id",
    "step7-insight": "candidate_id",
    "step7-cross-view": "candidate_id",
    "discovery-candidate": "candidate_id",
}
REGISTRY_METADATA_STDIN_LIMIT = 64 * 1024
MUTATION_REQUEST_STDIN_LIMIT = 4 * 1024 * 1024
DISCOVERY_REQUEST_INPUT_LIMIT = 64 * 1024


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="research-kb")
    parser.add_argument("--version", action="version", version=f"research-kb {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    capability = commands.add_parser("capability", help="inspect installed deterministic capabilities")
    capability_commands = capability.add_subparsers(dest="capability_command", required=True)
    capability_commands.add_parser("show", help="emit the public capability report")

    discovery = commands.add_parser("discovery", help="search public metadata through bounded connectors")
    discovery_commands = discovery.add_subparsers(dest="discovery_command", required=True)
    discovery_search = discovery_commands.add_parser("search", help="emit one transient discovery report")
    discovery_search.add_argument("--provider", required=True)
    discovery_search.add_argument("--request", required=True, type=Path)
    discovery_select = discovery_commands.add_parser("select", help="persist explicitly user-selected discovery results")
    discovery_select.add_argument("--workspace", required=True, type=Path)
    discovery_select.add_argument("--request", required=True, type=Path)
    discovery_select.add_argument("--actor", choices=("agent", "cli", "user"), required=True)
    discovery_list = discovery_commands.add_parser("list", help="list persisted discovery candidates")
    discovery_list.add_argument("--workspace", required=True, type=Path)
    discovery_show = discovery_commands.add_parser("show", help="show one persisted discovery candidate")
    discovery_show.add_argument("--workspace", required=True, type=Path)
    discovery_show.add_argument("--candidate-id", required=True)

    workspace = commands.add_parser("workspace", help="initialize deterministic workspace layout")
    workspace_commands = workspace.add_subparsers(dest="workspace_command", required=True)
    workspace_init = workspace_commands.add_parser("init", help="validate and initialize one workspace")
    workspace_init.add_argument("--workspace", required=True, type=Path)
    workspace_init.add_argument("--dry-run", action="store_true")

    intake = commands.add_parser("intake", help="inspect source intake state")
    intake_commands = intake.add_subparsers(dest="intake_command", required=True)
    intake_inspect = intake_commands.add_parser("inspect", help="project one source path for deterministic intake")
    intake_inspect.add_argument("--workspace", required=True, type=Path)
    intake_inspect.add_argument("--source", required=True, type=Path)

    compatibility = commands.add_parser("compatibility", help="inspect legacy data through explicit read-only adapters")
    compatibility_commands = compatibility.add_subparsers(dest="compatibility_command", required=True)
    compatibility_inspect = compatibility_commands.add_parser("inspect", help="emit one deterministic compatibility report")
    compatibility_inspect.add_argument("--workspace", required=True, type=Path)
    compatibility_inspect.add_argument("--adapter", required=True)

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
    parse_run.add_argument("--adapter", choices=("synthetic-text", "pdfplumber"), required=True)
    parse_show = parse_commands.add_parser("show", help="emit validated parsed-page records")
    parse_show.add_argument("--workspace", required=True, type=Path)
    parse_show.add_argument("--paper-id", required=True)
    parse_show.add_argument("--page")

    paper = commands.add_parser("paper", help="inspect one paper's deterministic state and context")
    paper_commands = paper.add_subparsers(dest="paper_command", required=True)
    paper_status = paper_commands.add_parser("status", help="emit one bounded paper status projection")
    paper_status.add_argument("--workspace", required=True, type=Path)
    paper_status.add_argument("--paper-id", required=True)
    paper_context = paper_commands.add_parser("context", help="emit one paper's canonical scientific context")
    paper_context.add_argument("--workspace", required=True, type=Path)
    paper_context.add_argument("--paper-id", required=True)

    review = commands.add_parser("review", help="inspect deterministic Review Memory state")
    review_commands = review.add_subparsers(dest="review_command", required=True)
    review_context = review_commands.add_parser("context", help="emit one review paper's Review Memory context")
    review_context.add_argument("--workspace", required=True, type=Path)
    review_context.add_argument("--paper-id", required=True)

    guardian = commands.add_parser("guardian", help="check workspace integrity")
    guardian_commands = guardian.add_subparsers(dest="guardian_command", required=True)
    guardian_check = guardian_commands.add_parser("check", help="run deterministic Guardian checks")
    guardian_check.add_argument("--workspace", required=True, type=Path)
    guardian_check.add_argument("--write-report", action="store_true")

    question = commands.add_parser("question", help="inspect persisted question mappings")
    question_commands = question.add_subparsers(dest="question_command", required=True)
    question_list = question_commands.add_parser("list", help="list question mappings")
    question_list.add_argument("--workspace", required=True, type=Path)
    question_show = question_commands.add_parser("show", help="show one question mapping")
    question_show.add_argument("--workspace", required=True, type=Path)
    question_show.add_argument("--question-id", required=True)
    question_render = question_commands.add_parser("render", help="render one question reading view")
    question_render.add_argument("--workspace", required=True, type=Path)
    question_render.add_argument("--question-id", required=True)

    step7 = commands.add_parser("step7", help="inspect persisted Step 7 candidates")
    step7_commands = step7.add_subparsers(dest="step7_command", required=True)
    step7_context = step7_commands.add_parser("context", help="emit one question's Step 7 candidate context")
    step7_context.add_argument("--workspace", required=True, type=Path)
    step7_context.add_argument("--question-id", required=True)
    step7_render = step7_commands.add_parser("render", help="render one Step 7 reading view")
    step7_render.add_argument("--workspace", required=True, type=Path)
    step7_render.add_argument("--question-id", required=True)

    transaction = commands.add_parser("transaction", help="inspect or recover interrupted writes")
    transaction_commands = transaction.add_subparsers(dest="transaction_command", required=True)
    recover = transaction_commands.add_parser("recover", help="recover transaction journals by digest")
    recover.add_argument("--workspace", required=True, type=Path)
    recover.add_argument("--dry-run", action="store_true")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    compatibility_adapters: Iterable[LegacyReaderAdapter] = (),
    discovery_connectors: Iterable[DiscoveryConnector] | None = None,
) -> int:
    _configure_standard_streams()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "capability" and args.capability_command == "show":
            return _capability_show(args)
        if args.command == "discovery" and args.discovery_command == "search":
            connectors = (EuropePmcConnector(),) if discovery_connectors is None else discovery_connectors
            return _discovery_search(args, connectors)
        if args.command == "discovery" and args.discovery_command == "select":
            return _discovery_select(args)
        if args.command == "discovery" and args.discovery_command == "list":
            return _discovery_list(args)
        if args.command == "discovery" and args.discovery_command == "show":
            return _discovery_show(args)
        if args.command == "workspace" and args.workspace_command == "init":
            return _workspace_init(args)
        if args.command == "intake" and args.intake_command == "inspect":
            return _intake_inspect(args)
        if args.command == "compatibility" and args.compatibility_command == "inspect":
            return _compatibility_inspect(args, compatibility_adapters)
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
        if args.command == "parse" and args.parse_command == "show":
            return _parse_show(args)
        if args.command == "paper" and args.paper_command == "status":
            return _paper_status(args)
        if args.command == "paper" and args.paper_command == "context":
            return _paper_context(args)
        if args.command == "review" and args.review_command == "context":
            return _review_context(args)
        if args.command == "guardian" and args.guardian_command == "check":
            return _guardian_check(args)
        if args.command == "question" and args.question_command == "list":
            return _question_list(args)
        if args.command == "question" and args.question_command == "show":
            return _question_show(args)
        if args.command == "question" and args.question_command == "render":
            return _question_render(args)
        if args.command == "step7" and args.step7_command == "context":
            return _step7_context(args)
        if args.command == "step7" and args.step7_command == "render":
            return _step7_render(args)
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
            "RKBC-034",
            WORKSPACE_NOT_INITIALIZED,
            WORKSPACE_IDENTITY_CONFLICT,
            WORKSPACE_LAYOUT_CONFLICT,
            WORKSPACE_LAYOUT_UPGRADE_REQUIRED,
            UNSAFE_DIRECTORY_MODE,
            PROTECTED_INPUT_CHANGED,
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


def _intake_inspect(args: argparse.Namespace) -> int:
    layout = WorkspaceLayout.load(args.workspace)
    _write_json_once(IntakeInspectService(layout).inspect(source=args.source))
    return 0


def _capability_show(args: argparse.Namespace) -> int:
    del args
    _write_json_once(CapabilityService().show())
    return 0


def _discovery_search(
    args: argparse.Namespace,
    connectors: Iterable[DiscoveryConnector],
) -> int:
    stream = sys.stdin.buffer if args.request == Path("-") else args.request.open("rb")
    try:
        request = read_bounded_json_object(
            stream,
            limit=DISCOVERY_REQUEST_INPUT_LIMIT,
            record_kind="discovery-request",
        )
    finally:
        if args.request != Path("-"):
            stream.close()
    report = DiscoveryService(DiscoveryConnectorRegistry(connectors)).search(
        args.provider,
        request,
    )
    _write_json_once(report)
    return 0


def _discovery_select(args: argparse.Namespace) -> int:
    layout = WorkspaceLayout.load(args.workspace)
    stream = sys.stdin.buffer if args.request == Path("-") else args.request.open("rb")
    try:
        request = read_bounded_json_object(
            stream,
            limit=MUTATION_REQUEST_STDIN_LIMIT,
            record_kind="discovery-selection-request",
        )
    finally:
        if args.request != Path("-"):
            stream.close()
    result = DiscoveryCandidateService(layout).select(request, actor=args.actor)
    _write_json_once(result.to_dict(layout))
    return 0


def _discovery_list(args: argparse.Namespace) -> int:
    layout = WorkspaceLayout.load(args.workspace)
    _write_json_once(DiscoveryCandidateService(layout).list())
    return 0


def _discovery_show(args: argparse.Namespace) -> int:
    layout = WorkspaceLayout.load(args.workspace)
    _write_json_once(DiscoveryCandidateService(layout).show(args.candidate_id))
    return 0


def _compatibility_inspect(
    args: argparse.Namespace,
    adapters: Iterable[LegacyReaderAdapter],
) -> int:
    layout = WorkspaceLayout.load(args.workspace)
    registry = CompatibilityAdapterRegistry(adapters)
    result = CompatibilityInspectionService(layout, registry).inspect(args.adapter)
    _write_json(result.report)
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
    request = (
        mutation_request_from_mapping(
            read_bounded_json_object(
                sys.stdin.buffer,
                limit=MUTATION_REQUEST_STDIN_LIMIT,
                record_kind="mutation-request",
            )
        )
        if args.request == Path("-")
        else load_mutation_request(args.request)
    )
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
    metadata = (
        read_bounded_json_object(
            sys.stdin.buffer,
            limit=REGISTRY_METADATA_STDIN_LIMIT,
            record_kind="registry-metadata",
        )
        if args.metadata == Path("-")
        else _load_mapping(args.metadata)
    )
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
    adapter = {
        "synthetic-text": SyntheticTextAdapter,
        "pdfplumber": PdfPlumberAdapter,
    }[args.adapter]()
    pages, transaction = ParseService(layout).run(
        paper_id=args.paper_id,
        adapter=adapter,
        actor="cli",
    )
    _write_json({
        "status": "success",
        "paper_id": args.paper_id,
        "parse_run_id": transaction.event_id,
        "parser": pages[0]["parser"],
        "pages": len(pages),
        "target": layout.target_relative_path(transaction.target),
    })
    return 0


def _parse_show(args: argparse.Namespace) -> int:
    layout = WorkspaceLayout.load(args.workspace)
    result = ParseReadService(layout).show(paper_id=args.paper_id, page=args.page)
    _write_json_once(result)
    return 0


def _paper_status(args: argparse.Namespace) -> int:
    layout = WorkspaceLayout.load(args.workspace)
    result = PaperStatusService(layout).show(paper_id=args.paper_id)
    _write_json_once(result)
    return 0


def _paper_context(args: argparse.Namespace) -> int:
    layout = WorkspaceLayout.load(args.workspace)
    result = PaperContextService(layout).show(paper_id=args.paper_id)
    _write_json_once(result)
    return 0


def _review_context(args: argparse.Namespace) -> int:
    layout = WorkspaceLayout.load(args.workspace)
    result = ReviewContextService(layout).show(paper_id=args.paper_id)
    _write_json_once(result)
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


def _question_list(args: argparse.Namespace) -> int:
    layout = WorkspaceLayout.load(args.workspace)
    entries = load_workspace_entries(layout)
    validate_workspace_entries(entries)
    questions = sorted(records_of_kind(entries, "question-mapping"), key=lambda item: item["question_id"])
    _write_json({
        "status": "success",
        "questions": [
            {
                "question_id": item["question_id"],
                "question_text": item["question_text"],
                "scope": item["scope"],
                "mapping_status": item["mapping_status"],
                "linked_paper_count": len(item["paper_links"]),
                "updated_at": item["updated_at"],
            }
            for item in questions
        ],
    })
    return 0


def _question_show(args: argparse.Namespace) -> int:
    layout = WorkspaceLayout.load(args.workspace)
    question_id = validate_id(args.question_id, Namespace.QUESTION)
    entries = load_workspace_entries(layout)
    validate_workspace_entries(entries)
    question = next(
        (item for item in records_of_kind(entries, "question-mapping") if item["question_id"] == question_id),
        None,
    )
    if question is None:
        raise ResearchKBError(
            Diagnostic(
                UNRESOLVED_REFERENCE,
                "question-mapping",
                question_id,
                "/question_id",
                "question mapping does not exist",
            )
        )
    _write_json({"status": "success", "question": question})
    return 0


def _question_render(args: argparse.Namespace) -> int:
    layout = WorkspaceLayout.load(args.workspace)
    entries = load_workspace_entries(layout)
    content = QuestionReadingViewService(entries).render(args.question_id)
    _write_bytes_once(content)
    return 0


def _step7_context(args: argparse.Namespace) -> int:
    layout = WorkspaceLayout.load(args.workspace)
    _write_json_once(Step7ContextService(layout).show(question_id=args.question_id))
    return 0


def _step7_render(args: argparse.Namespace) -> int:
    layout = WorkspaceLayout.load(args.workspace)
    entries = load_workspace_entries(layout)
    content = Step7ReadingViewService(entries).render(args.question_id)
    _write_bytes_once(content)
    return 0


def _record_id(kind: str, record: dict[str, Any]) -> str:
    id_field = {
        "registry-paper": "paper_id",
        "paper-card": "paper_id",
        "evidence": "evidence_id",
        "review-queue": "queue_id",
        "review-memory": "review_memory_id",
        "question-mapping": "question_id",
        "step7-synthesis": "candidate_id",
        "step7-review-angle": "candidate_id",
        "step7-insight": "candidate_id",
        "step7-cross-view": "candidate_id",
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


def _write_json_once(value: dict[str, Any], stream: Any | None = None) -> None:
    _write_bytes_once(serialize_json(value), stream=stream)


def _write_bytes_once(content: bytes, stream: Any | None = None) -> None:
    output = sys.stdout if stream is None else stream
    binary = getattr(output, "buffer", None)
    if binary is not None:
        written = binary.write(content)
        if written != len(content):
            raise OSError("short stdout write")
        binary.flush()
        return

    text = content.decode("utf-8")
    written = output.write(text)
    if written != len(text):
        raise OSError("short stdout write")
    output.flush()


def _configure_standard_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="strict")
