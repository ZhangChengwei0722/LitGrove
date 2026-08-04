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
from research_kb.discovery import (
    DiscoveryAcquisitionTransport,
    DiscoveryConnector,
    DiscoveryResolver,
)
from research_kb.discovery.europe_pmc import EuropePmcConnector, EuropePmcResolver
from research_kb.discovery.europe_pmc_pdf import EuropePmcPdfTransport
from research_kb.errors import (
    SCHEMA_VALIDATION_FAILED,
    UNSAFE_DIRECTORY_MODE,
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
from research_kb.mutation import load_mutation_request, mutation_request_from_mapping
from research_kb.obsidian_views import OPTIONAL_TABLES
from research_kb.services.acquired_candidate_intake import AcquiredCandidateIntakeService
from research_kb.services.application_validation import ContractValidationService, JsonlValidationService
from research_kb.services.capability import CapabilityService
from research_kb.services.guardian_disposition import GuardianFindingDispositionService
from research_kb.services.pipeline_job import PipelineJobService
from research_kb.services.local_source_intake import LocalSourceIntakeService
from research_kb.services.registry_identity import RegistryIdentityCorrectionService
from research_kb.services.source_asset import SourceAssetService
from research_kb.services.records import RecordService
from research_kb.services.parse_read import ParseReadService
from research_kb.services.paper_status import PaperStatusService
from research_kb.services.paper_context import PaperContextService
from research_kb.services.review_context import ReviewContextService
from research_kb.services.parse_application import ParseApplicationService
from research_kb.services.privacy_scan import PrivacyScanService
from research_kb.services.question_read import QuestionQueryService, WorkspaceQuestionReadingViewService
from research_kb.services.recovery import TransactionRecoveryService
from research_kb.services.step7_context import Step7ContextService
from research_kb.services.step7_render import WorkspaceStep7ReadingViewService
from research_kb.services.registry import RegistryService
from research_kb.services.bootstrap import WorkspaceBootstrapService
from research_kb.services.compatibility import CompatibilityAdapterRegistry, CompatibilityInspectionService
from research_kb.services.intake_inspect import IntakeInspectService
from research_kb.services.manuscript_projection import ManuscriptProjectionService
from research_kb.services.obsidian_generated_views_application import (
    ObsidianGeneratedViewsApplicationService,
)
from research_kb.services.workspace_session import WorkspaceSessionService
from research_kb.services.discovery import DiscoveryConnectorRegistry, DiscoveryService
from research_kb.services.discovery_candidate import DiscoveryCandidateService
from research_kb.services.discovery_acquisition import (
    DiscoveryAcquisitionService,
    DiscoveryAcquisitionTransportRegistry,
)
from research_kb.services.discovery_resolution import DiscoveryResolutionService, DiscoveryResolverRegistry
from research_kb.services.deterministic_trunk import DeterministicTrunkService
from research_kb.services.source_adequacy import SourceAdequacyService
from research_kb.compatibility import LegacyReaderAdapter
from research_kb.storage.json_io import serialize_json
from research_kb.workspace import WorkspaceLayout


REGISTRY_METADATA_STDIN_LIMIT = 64 * 1024
MUTATION_REQUEST_STDIN_LIMIT = 4 * 1024 * 1024
DISCOVERY_REQUEST_INPUT_LIMIT = 64 * 1024
PIPELINE_JOB_REQUEST_INPUT_LIMIT = 64 * 1024
GUARDIAN_DISPOSITION_REQUEST_INPUT_LIMIT = 64 * 1024
SOURCE_REQUEST_INPUT_LIMIT = 64 * 1024
IDENTITY_REQUEST_INPUT_LIMIT = 64 * 1024
SOURCE_ADEQUACY_REQUEST_INPUT_LIMIT = 64 * 1024


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
    discovery_resolve = discovery_commands.add_parser("resolve", help="resolve one candidate's supported OA route")
    discovery_resolve.add_argument("--workspace", required=True, type=Path)
    discovery_resolve.add_argument("--candidate-id", required=True)
    discovery_resolve.add_argument("--provider", required=True)
    discovery_acquire = discovery_commands.add_parser(
        "acquire",
        help="create one explicitly authorized OA PDF in local_inbox",
    )
    discovery_acquire.add_argument("--workspace", required=True, type=Path)
    discovery_acquire.add_argument("--candidate-id", required=True)
    discovery_acquire.add_argument("--provider", required=True)
    discovery_acquire.add_argument(
        "--actor",
        choices=("agent", "cli", "user"),
        required=True,
    )

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
    intake_inspect_acquired = intake_commands.add_parser(
        "inspect-acquired",
        help="project one acquired discovery candidate for deterministic intake",
    )
    intake_inspect_acquired.add_argument("--workspace", required=True, type=Path)
    intake_inspect_acquired.add_argument("--candidate-id", required=True)

    manuscript = commands.add_parser("manuscript", help="project one local manuscript read-only")
    manuscript_commands = manuscript.add_subparsers(dest="manuscript_command", required=True)
    manuscript_inspect = manuscript_commands.add_parser(
        "inspect",
        help="emit bounded DOCX or PDF manuscript units",
    )
    manuscript_inspect.add_argument("--workspace", required=True, type=Path)
    manuscript_inspect.add_argument("--source", required=True, type=Path)

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

    source = commands.add_parser("source", help="manage source manifestations and local inbox handoff")
    source_commands = source.add_subparsers(dest="source_command", required=True)
    source_list = source_commands.add_parser("list", help="list current redacted Source Asset projections")
    source_list.add_argument("--workspace", required=True, type=Path)
    source_scan = source_commands.add_parser("scan", help="scan a bounded stable local_inbox snapshot")
    source_scan.add_argument("--workspace", required=True, type=Path)
    source_scan.add_argument("--max-entries", type=int, default=100)
    source_scan.add_argument("--min-stable-age-seconds", type=int, default=5)
    for command_name, command_help in (
        ("reference", "register one existing source reference"),
        ("copy", "copy one exact user-authorized PDF create-only into local_inbox"),
        ("select", "revalidate and select one transient inbox candidate"),
        ("associate", "append one Registry paper association"),
        ("observe", "append one current source observation"),
        ("relink", "append one same-digest source relink"),
    ):
        source_mutation = source_commands.add_parser(command_name, help=command_help)
        source_mutation.add_argument("--workspace", required=True, type=Path)
        source_mutation.add_argument("--request", required=True, type=Path)
        source_mutation.add_argument("--actor", choices=("agent", "cli", "user"), required=True)

    identity = commands.add_parser("identity", help="inspect or append Registry identity corrections")
    identity_commands = identity.add_subparsers(dest="identity_command", required=True)
    identity_list = identity_commands.add_parser("list", help="list current Registry identity projection")
    identity_list.add_argument("--workspace", required=True, type=Path)
    identity_correct = identity_commands.add_parser("correct", help="append one user-authorized identity correction")
    identity_correct.add_argument("--workspace", required=True, type=Path)
    identity_correct.add_argument("--request", required=True, type=Path)
    identity_correct.add_argument("--actor", choices=("agent", "cli", "user"), required=True)

    parse = commands.add_parser("parse", help="run deterministic parse adapters")
    parse_commands = parse.add_subparsers(dest="parse_command", required=True)
    parse_run = parse_commands.add_parser("run", help="parse one registered source")
    parse_run.add_argument("--workspace", required=True, type=Path)
    parse_run.add_argument("--paper-id", required=True)
    parse_run.add_argument(
        "--adapter",
        choices=("synthetic-text", "pdfplumber", "pdfplumber-text-flow"),
        required=True,
    )
    parse_show = parse_commands.add_parser("show", help="emit validated parsed-page records")
    parse_show.add_argument("--workspace", required=True, type=Path)
    parse_show.add_argument("--paper-id", required=True)
    parse_show.add_argument("--page")

    adequacy = commands.add_parser("adequacy", help="assess and inspect source fitness by requested use")
    adequacy_commands = adequacy.add_subparsers(dest="adequacy_command", required=True)
    adequacy_assess = adequacy_commands.add_parser("assess", help="persist one exact Source Adequacy profile")
    adequacy_assess.add_argument("--workspace", required=True, type=Path)
    adequacy_assess.add_argument("--request", required=True, type=Path)
    adequacy_assess.add_argument("--actor", choices=("cli", "user"), required=True)
    adequacy_show = adequacy_commands.add_parser("show", help="show redacted Source Adequacy profiles")
    adequacy_show.add_argument("--workspace", required=True, type=Path)
    adequacy_show.add_argument("--paper-id", required=True)
    adequacy_show.add_argument("--operation")
    adequacy_gate = adequacy_commands.add_parser("gate", help="evaluate one zero-write requested-use gate")
    adequacy_gate.add_argument("--workspace", required=True, type=Path)
    adequacy_gate.add_argument("--paper-id", required=True)
    adequacy_gate.add_argument("--operation", required=True)

    trunk = commands.add_parser("trunk", help="advance the deterministic source-to-semantic boundary")
    trunk_commands = trunk.add_subparsers(dest="trunk_command", required=True)
    trunk_advance = trunk_commands.add_parser("advance", help="advance or resume one deterministic Pipeline Job")
    trunk_advance.add_argument("--workspace", required=True, type=Path)
    trunk_advance.add_argument("--request", required=True, type=Path)
    trunk_advance.add_argument("--actor", choices=("cli", "user"), required=True)

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
    guardian_disposition = guardian_commands.add_parser(
        "disposition",
        help="append one auditable Guardian finding disposition",
    )
    guardian_disposition.add_argument("--workspace", required=True, type=Path)
    guardian_disposition.add_argument("--request", required=True, type=Path)
    guardian_disposition.add_argument("--actor", choices=("agent", "cli", "user"), required=True)

    job = commands.add_parser("job", help="manage deterministic Pipeline Jobs")
    job_commands = job.add_subparsers(dest="job_command", required=True)
    job_create = job_commands.add_parser("create", help="create one Pipeline Job")
    job_create.add_argument("--workspace", required=True, type=Path)
    job_create.add_argument("--request", required=True, type=Path)
    job_create.add_argument("--actor", choices=("agent", "cli", "user"), required=True)
    job_list = job_commands.add_parser("list", help="list current Pipeline Job states")
    job_list.add_argument("--workspace", required=True, type=Path)
    job_list.add_argument("--page-size", type=int, default=20)
    job_list.add_argument("--cursor")
    job_show = job_commands.add_parser("show", help="show one Pipeline Job history")
    job_show.add_argument("--workspace", required=True, type=Path)
    job_show.add_argument("--job-id", required=True)
    for command_name, command_help in (
        ("transition", "append one Pipeline Job transition"),
        ("cancel", "cooperatively cancel one Pipeline Job"),
        ("recover", "append one Pipeline Job recovery transition"),
    ):
        command = job_commands.add_parser(command_name, help=command_help)
        command.add_argument("--workspace", required=True, type=Path)
        command.add_argument("--job-id", required=True)
        command.add_argument("--request", required=True, type=Path)
        command.add_argument("--actor", choices=("agent", "cli", "user"), required=True)

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

    step7 = commands.add_parser("step7", help="inspect persisted Research Synthesis candidates")
    step7_commands = step7.add_subparsers(dest="step7_command", required=True)
    step7_context = step7_commands.add_parser("context", help="emit one question's Research Synthesis candidate context")
    step7_context.add_argument("--workspace", required=True, type=Path)
    step7_context.add_argument("--question-id", required=True)
    step7_render = step7_commands.add_parser("render", help="render one Research Synthesis reading view")
    step7_render.add_argument("--workspace", required=True, type=Path)
    step7_render.add_argument("--question-id", required=True)

    obsidian = commands.add_parser("obsidian", help="manage generated Obsidian reading views")
    obsidian_commands = obsidian.add_subparsers(dest="obsidian_command", required=True)
    obsidian_status = obsidian_commands.add_parser("status", help="inspect generated-view freshness")
    obsidian_status.add_argument("--workspace", required=True, type=Path)
    obsidian_status.add_argument("--page-size", type=int, default=20)
    obsidian_status.add_argument("--cursor")
    obsidian_render = obsidian_commands.add_parser("render", help="preview or render generated views")
    obsidian_render.add_argument("--workspace", required=True, type=Path)
    obsidian_render.add_argument(
        "--table",
        dest="optional_tables",
        action="append",
        choices=OPTIONAL_TABLES,
        default=[],
    )
    obsidian_render.add_argument("--dry-run", action="store_true")
    obsidian_render.add_argument("--discard-managed-edits", action="store_true")
    obsidian_render.add_argument("--actor", choices=("cli", "user"), default="cli")

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
    discovery_resolvers: Iterable[DiscoveryResolver] | None = None,
    discovery_acquisition_transports: Iterable[DiscoveryAcquisitionTransport] | None = None,
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
        if args.command == "discovery" and args.discovery_command == "resolve":
            resolvers = (EuropePmcResolver(),) if discovery_resolvers is None else discovery_resolvers
            return _discovery_resolve(args, resolvers)
        if args.command == "discovery" and args.discovery_command == "acquire":
            resolvers = (EuropePmcResolver(),) if discovery_resolvers is None else discovery_resolvers
            transports = (
                (EuropePmcPdfTransport(),)
                if discovery_acquisition_transports is None
                else discovery_acquisition_transports
            )
            return _discovery_acquire(args, resolvers, transports)
        if args.command == "workspace" and args.workspace_command == "init":
            return _workspace_init(args)
        if args.command == "intake" and args.intake_command == "inspect":
            return _intake_inspect(args)
        if args.command == "intake" and args.intake_command == "inspect-acquired":
            return _intake_inspect_acquired(args)
        if args.command == "manuscript" and args.manuscript_command == "inspect":
            return _manuscript_inspect(args)
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
        if args.command == "source" and args.source_command == "list":
            return _source_list(args)
        if args.command == "source" and args.source_command == "scan":
            return _source_scan(args)
        if args.command == "source" and args.source_command in {"reference", "copy", "select", "associate", "observe", "relink"}:
            return _source_mutation(args)
        if args.command == "identity" and args.identity_command == "list":
            return _identity_list(args)
        if args.command == "identity" and args.identity_command == "correct":
            return _identity_correct(args)
        if args.command == "parse" and args.parse_command == "run":
            return _parse_run(args)
        if args.command == "parse" and args.parse_command == "show":
            return _parse_show(args)
        if args.command == "adequacy" and args.adequacy_command == "assess":
            return _adequacy_assess(args)
        if args.command == "adequacy" and args.adequacy_command == "show":
            return _adequacy_show(args)
        if args.command == "adequacy" and args.adequacy_command == "gate":
            return _adequacy_gate(args)
        if args.command == "trunk" and args.trunk_command == "advance":
            return _trunk_advance(args)
        if args.command == "paper" and args.paper_command == "status":
            return _paper_status(args)
        if args.command == "paper" and args.paper_command == "context":
            return _paper_context(args)
        if args.command == "review" and args.review_command == "context":
            return _review_context(args)
        if args.command == "guardian" and args.guardian_command == "check":
            return _guardian_check(args)
        if args.command == "guardian" and args.guardian_command == "disposition":
            return _guardian_disposition(args)
        if args.command == "job" and args.job_command == "create":
            return _job_create(args)
        if args.command == "job" and args.job_command == "list":
            return _job_list(args)
        if args.command == "job" and args.job_command == "show":
            return _job_show(args)
        if args.command == "job" and args.job_command == "transition":
            return _job_transition(args)
        if args.command == "job" and args.job_command == "cancel":
            return _job_cancel(args)
        if args.command == "job" and args.job_command == "recover":
            return _job_recover(args)
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
        if args.command == "obsidian" and args.obsidian_command == "status":
            return _obsidian_status(args)
        if args.command == "obsidian" and args.obsidian_command == "render":
            return _obsidian_render(args)
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


def _manuscript_inspect(args: argparse.Namespace) -> int:
    layout = WorkspaceLayout.load(args.workspace)
    _write_json_once(ManuscriptProjectionService(layout).inspect(source=args.source))
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


def _intake_inspect_acquired(args: argparse.Namespace) -> int:
    layout = WorkspaceLayout.load(args.workspace)
    report = AcquiredCandidateIntakeService(layout).inspect(args.candidate_id)
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


def _discovery_resolve(
    args: argparse.Namespace,
    resolvers: Iterable[DiscoveryResolver],
) -> int:
    layout = WorkspaceLayout.load(args.workspace)
    report = DiscoveryResolutionService(
        layout,
        DiscoveryResolverRegistry(resolvers),
    ).resolve(args.candidate_id, provider=args.provider)
    _write_json_once(report)
    return 0


def _discovery_acquire(
    args: argparse.Namespace,
    resolvers: Iterable[DiscoveryResolver],
    transports: Iterable[DiscoveryAcquisitionTransport],
) -> int:
    layout = WorkspaceLayout.load(args.workspace)
    report = DiscoveryAcquisitionService(
        layout,
        resolver_registry=DiscoveryResolverRegistry(resolvers),
        transport_registry=DiscoveryAcquisitionTransportRegistry(transports),
    ).acquire(
        args.candidate_id,
        provider=args.provider,
        actor=args.actor,
    )
    _write_json_once(report)
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
    record = _load_mapping(args.input)
    bundle = _load_mapping(args.bundle) if args.bundle is not None else None
    result = ContractValidationService().validate(
        kind=args.kind,
        record=record,
        bundle=bundle,
        actor=args.actor,
    )
    _write_json(result.to_dict())
    return result.exit_code


def _privacy_scan(args: argparse.Namespace) -> int:
    result = PrivacyScanService().scan(root=args.root, allowlist=args.allowlist)
    _write_json(result.to_dict())
    return result.exit_code


def _data_check_jsonl(args: argparse.Namespace) -> int:
    result = JsonlValidationService().check(path=args.input, kind=args.kind, actor=args.actor)
    _write_json(result.to_dict())
    return result.exit_code


def _transaction_recover(args: argparse.Namespace) -> int:
    layout = WorkspaceLayout.load(args.workspace)
    result = TransactionRecoveryService(layout).recover(dry_run=args.dry_run)
    _write_json(result.to_dict())
    return result.exit_code


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


def _source_list(args: argparse.Namespace) -> int:
    _write_json_once(SourceAssetService(WorkspaceLayout.load(args.workspace)).list())
    return 0


def _source_scan(args: argparse.Namespace) -> int:
    result = LocalSourceIntakeService(WorkspaceLayout.load(args.workspace)).scan(
        max_entries=args.max_entries,
        min_stable_age_seconds=args.min_stable_age_seconds,
    )
    _write_json_once(result)
    return 0


def _source_mutation(args: argparse.Namespace) -> int:
    request = _read_bounded_request(
        args.request,
        limit=SOURCE_REQUEST_INPUT_LIMIT,
        record_kind=f"source-{args.source_command}-request",
    )
    layout = WorkspaceLayout.load(args.workspace)
    if args.source_command == "copy":
        _require_request_fields(
            request,
            required={"source", "job_id", "paper_id", "asset_role"},
            optional={"fixture_origin"},
            record_kind="source-copy-request",
        )
        if not isinstance(request["source"], str):
            raise ResearchKBError(
                Diagnostic(
                    SCHEMA_VALIDATION_FAILED,
                    "source-copy-request",
                    None,
                    "/source",
                    "copy source must be an absolute path string",
                )
            )
        result = LocalSourceIntakeService(layout).copy(
            source=Path(request["source"]),
            job_id=request["job_id"],
            paper_id=request["paper_id"],
            asset_role=request["asset_role"],
            actor=args.actor,
            fixture_origin=request.get("fixture_origin"),
        )
        _write_json_once(result)
        return 0
    if args.source_command == "select":
        _require_request_fields(
            request,
            required={"candidate_token", "job_id", "paper_id", "asset_role"},
            optional={"min_stable_age_seconds"},
            record_kind="source-select-request",
        )
        result = LocalSourceIntakeService(layout).select(
            candidate_handle=request["candidate_token"],
            job_id=request["job_id"],
            paper_id=request["paper_id"],
            asset_role=request["asset_role"],
            actor=args.actor,
            min_stable_age_seconds=request.get("min_stable_age_seconds", 5),
        )
        _write_json_once(result)
        return 0

    service = SourceAssetService(layout)
    if args.source_command == "reference":
        _require_request_fields(
            request,
            required={"job_id", "paper_id", "asset_role", "root_id", "relative_path"},
            optional={"fixture_origin"},
            record_kind="source-reference-request",
        )
        mutation = service.register_reference(
            job_id=request["job_id"],
            paper_id=request["paper_id"],
            asset_role=request["asset_role"],
            root_id=request["root_id"],
            relative_path=request["relative_path"],
            actor=args.actor,
            fixture_origin=request.get("fixture_origin"),
        )
    elif args.source_command == "associate":
        _require_request_fields(
            request,
            required={"source_asset_id", "job_id", "paper_id", "expected_state_id", "expected_state_digest"},
            optional=set(),
            record_kind="source-associate-request",
        )
        mutation = service.associate(
            source_asset_id=request["source_asset_id"],
            job_id=request["job_id"],
            paper_id=request["paper_id"],
            expected_state_id=request["expected_state_id"],
            expected_state_digest=request["expected_state_digest"],
            actor=args.actor,
        )
    elif args.source_command == "observe":
        _require_request_fields(
            request,
            required={"source_asset_id", "job_id", "expected_state_id", "expected_state_digest"},
            optional=set(),
            record_kind="source-observe-request",
        )
        mutation = service.observe(
            source_asset_id=request["source_asset_id"],
            job_id=request["job_id"],
            expected_state_id=request["expected_state_id"],
            expected_state_digest=request["expected_state_digest"],
            actor=args.actor,
        )
    else:
        _require_request_fields(
            request,
            required={"source_asset_id", "job_id", "root_id", "relative_path", "expected_state_id", "expected_state_digest"},
            optional=set(),
            record_kind="source-relink-request",
        )
        mutation = service.relink(
            source_asset_id=request["source_asset_id"],
            job_id=request["job_id"],
            root_id=request["root_id"],
            relative_path=request["relative_path"],
            expected_state_id=request["expected_state_id"],
            expected_state_digest=request["expected_state_digest"],
            actor=args.actor,
        )
    _write_json_once(
        {
            "status": "success",
            "result": "updated" if mutation.transaction is not None else "no_change",
            "source_asset_id": mutation.state["source_asset_id"],
            "source_asset_state_id": mutation.state["source_asset_state_id"],
            "paper_id": mutation.state["paper_id"],
            "source_ref": mutation.state["source_ref"],
            "persistent_writes": 1 if mutation.transaction is not None else 0,
            "event_id": None if mutation.transaction is None else mutation.transaction.event_id,
        }
    )
    return 0


def _identity_list(args: argparse.Namespace) -> int:
    _write_json_once(RegistryIdentityCorrectionService(WorkspaceLayout.load(args.workspace)).list())
    return 0


def _identity_correct(args: argparse.Namespace) -> int:
    request = _read_bounded_request(
        args.request,
        limit=IDENTITY_REQUEST_INPUT_LIMIT,
        record_kind="identity-correction-request",
    )
    _require_request_fields(
        request,
        required={
            "job_id",
            "operation",
            "subject_paper_ids",
            "retained_paper_id",
            "supersedes_correction_id",
            "rationale",
            "expected_previous_correction_id",
            "expected_previous_correction_digest",
        },
        optional={"fixture_origin"},
        record_kind="identity-correction-request",
    )
    result = RegistryIdentityCorrectionService(WorkspaceLayout.load(args.workspace)).record(
        job_id=request["job_id"],
        operation=request["operation"],
        subject_paper_ids=request["subject_paper_ids"],
        retained_paper_id=request["retained_paper_id"],
        supersedes_correction_id=request["supersedes_correction_id"],
        rationale=request["rationale"],
        expected_previous_correction_id=request["expected_previous_correction_id"],
        expected_previous_correction_digest=request["expected_previous_correction_digest"],
        actor=args.actor,
        fixture_origin=request.get("fixture_origin"),
    )
    _write_json_once(
        {
            "status": "success",
            "result": "updated" if result.transaction is not None else "no_change",
            "correction_id": result.correction["correction_id"],
            "persistent_writes": 1 if result.transaction is not None else 0,
            "event_id": None if result.transaction is None else result.transaction.event_id,
        }
    )
    return 0


def _parse_run(args: argparse.Namespace) -> int:
    layout = WorkspaceLayout.load(args.workspace)
    result = ParseApplicationService(layout).run(
        paper_id=args.paper_id,
        adapter_name=args.adapter,
        actor="cli",
    )
    _write_json(result.to_dict())
    return 0


def _parse_show(args: argparse.Namespace) -> int:
    layout = WorkspaceLayout.load(args.workspace)
    result = ParseReadService(layout).show(paper_id=args.paper_id, page=args.page)
    _write_json_once(result)
    return 0


def _adequacy_assess(args: argparse.Namespace) -> int:
    request = _read_bounded_request(
        args.request,
        limit=SOURCE_ADEQUACY_REQUEST_INPUT_LIMIT,
        record_kind="source-adequacy-assess-request",
    )
    _require_request_fields(
        request,
        required={"paper_id", "job_id", "requested_operation"},
        optional={"basis_profile_id", "user_decision"},
        record_kind="source-adequacy-assess-request",
    )
    service = SourceAdequacyService(WorkspaceLayout.load(args.workspace))
    mutation = service.assess(
        paper_id=request["paper_id"],
        job_id=request["job_id"],
        requested_operation=request["requested_operation"],
        actor=args.actor,
        basis_profile_id=request.get("basis_profile_id"),
        user_decision=request.get("user_decision"),
    )
    projection = service.show(
        paper_id=request["paper_id"],
        requested_operation=request["requested_operation"],
    )
    item = next(
        value for value in projection["items"] if value["profile_id"] == mutation.profile["profile_id"]
    )
    _write_json_once(
        {
            "status": "success",
            "result": "updated" if mutation.transaction is not None else "no_change",
            "profile": item,
            "persistent_writes": int(mutation.transaction is not None),
            "event_id": None if mutation.transaction is None else mutation.transaction.event_id,
        }
    )
    return 0


def _adequacy_show(args: argparse.Namespace) -> int:
    _write_json_once(
        SourceAdequacyService(WorkspaceLayout.load(args.workspace)).show(
            paper_id=args.paper_id,
            requested_operation=args.operation,
        )
    )
    return 0


def _adequacy_gate(args: argparse.Namespace) -> int:
    _write_json_once(
        SourceAdequacyService(WorkspaceLayout.load(args.workspace)).gate(
            paper_id=args.paper_id,
            requested_operation=args.operation,
        )
    )
    return 0


def _trunk_advance(args: argparse.Namespace) -> int:
    request = _read_bounded_request(
        args.request,
        limit=SOURCE_ADEQUACY_REQUEST_INPUT_LIMIT,
        record_kind="deterministic-trunk-request",
    )
    _require_request_fields(
        request,
        required={"job_id", "paper_id", "requested_operation", "adapter_name"},
        optional={"document_route", "route_reason"},
        record_kind="deterministic-trunk-request",
    )
    result = DeterministicTrunkService(WorkspaceLayout.load(args.workspace)).advance(
        job_id=request["job_id"],
        paper_id=request["paper_id"],
        requested_operation=request["requested_operation"],
        adapter_name=request["adapter_name"],
        actor=args.actor,
        document_route=request.get("document_route"),
        route_reason=request.get("route_reason"),
    )
    _write_json_once(result.to_dict())
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


def _guardian_disposition(args: argparse.Namespace) -> int:
    request = _read_bounded_request(
        args.request,
        limit=GUARDIAN_DISPOSITION_REQUEST_INPUT_LIMIT,
        record_kind="guardian-disposition-request",
    )
    _require_request_fields(
        request,
        required={"guardian_report_id", "finding_index", "status", "rationale"},
        optional={"expected_previous_disposition_id", "fixture_origin"},
        record_kind="guardian-disposition-request",
    )
    result = GuardianFindingDispositionService(WorkspaceLayout.load(args.workspace)).record(
        guardian_report_id=request["guardian_report_id"],
        finding_index=request["finding_index"],
        status=request["status"],
        rationale=request["rationale"],
        expected_previous_disposition_id=request.get("expected_previous_disposition_id"),
        actor=args.actor,
        fixture_origin=request.get("fixture_origin"),
    )
    _write_json_once(
        {
            "status": "success",
            "result": "updated" if result.transaction is not None else "no_change",
            "disposition": result.disposition,
            "persistent_writes": 1 if result.transaction is not None else 0,
            "event_id": result.transaction.event_id if result.transaction is not None else None,
        }
    )
    return 0


def _job_create(args: argparse.Namespace) -> int:
    request = _read_bounded_request(
        args.request,
        limit=PIPELINE_JOB_REQUEST_INPUT_LIMIT,
        record_kind="pipeline-job-create-request",
    )
    _require_request_fields(
        request,
        required={
            "requested_route",
            "requested_depth",
            "current_node",
            "input_refs",
            "authority_snapshot",
            "idempotency_key",
        },
        optional={"fixture_origin"},
        record_kind="pipeline-job-create-request",
    )
    result = PipelineJobService(WorkspaceLayout.load(args.workspace)).create(
        requested_route=request["requested_route"],
        requested_depth=request["requested_depth"],
        current_node=request["current_node"],
        input_refs=request["input_refs"],
        authority_snapshot=request["authority_snapshot"],
        idempotency_key=request["idempotency_key"],
        actor=args.actor,
        fixture_origin=request.get("fixture_origin"),
    )
    _write_job_mutation(result)
    return 0


def _job_list(args: argparse.Namespace) -> int:
    service = PipelineJobService(WorkspaceLayout.load(args.workspace))
    _write_json_once(service.list(page_size=args.page_size, cursor=args.cursor))
    return 0


def _job_show(args: argparse.Namespace) -> int:
    _write_json_once(PipelineJobService(WorkspaceLayout.load(args.workspace)).show(args.job_id))
    return 0


def _job_transition(args: argparse.Namespace) -> int:
    request = _read_bounded_request(
        args.request,
        limit=PIPELINE_JOB_REQUEST_INPUT_LIMIT,
        record_kind="pipeline-job-transition-request",
    )
    _require_request_fields(
        request,
        required={"expected_state_id", "expected_state_digest", "status", "current_node"},
        optional={"wait_reason", "output_refs", "retry_increment", "recovery_action"},
        record_kind="pipeline-job-transition-request",
    )
    result = PipelineJobService(WorkspaceLayout.load(args.workspace)).transition(
        args.job_id,
        expected_state_id=request["expected_state_id"],
        expected_state_digest=request["expected_state_digest"],
        status=request["status"],
        current_node=request["current_node"],
        wait_reason=request.get("wait_reason"),
        output_refs=request.get("output_refs", []),
        retry_increment=request.get("retry_increment", 0),
        recovery_action=request.get("recovery_action"),
        actor=args.actor,
    )
    _write_job_mutation(result)
    return 0


def _job_cancel(args: argparse.Namespace) -> int:
    request = _read_bounded_request(
        args.request,
        limit=PIPELINE_JOB_REQUEST_INPUT_LIMIT,
        record_kind="pipeline-job-cancel-request",
    )
    _require_request_fields(
        request,
        required={"expected_state_id", "expected_state_digest"},
        optional=set(),
        record_kind="pipeline-job-cancel-request",
    )
    result = PipelineJobService(WorkspaceLayout.load(args.workspace)).cancel(
        args.job_id,
        expected_state_id=request["expected_state_id"],
        expected_state_digest=request["expected_state_digest"],
        actor=args.actor,
    )
    _write_job_mutation(result)
    return 0


def _job_recover(args: argparse.Namespace) -> int:
    request = _read_bounded_request(
        args.request,
        limit=PIPELINE_JOB_REQUEST_INPUT_LIMIT,
        record_kind="pipeline-job-recover-request",
    )
    _require_request_fields(
        request,
        required={"expected_state_id", "expected_state_digest", "recovery_action"},
        optional=set(),
        record_kind="pipeline-job-recover-request",
    )
    result = PipelineJobService(WorkspaceLayout.load(args.workspace)).recover(
        args.job_id,
        expected_state_id=request["expected_state_id"],
        expected_state_digest=request["expected_state_digest"],
        recovery_action=request["recovery_action"],
        actor=args.actor,
    )
    _write_job_mutation(result)
    return 0


def _write_job_mutation(result: Any) -> None:
    _write_json_once(
        {
            "status": "success",
            "result": "updated" if result.transaction is not None else "no_change",
            "state": result.state,
            "persistent_writes": 1 if result.transaction is not None else 0,
            "event_id": result.transaction.event_id if result.transaction is not None else None,
        }
    )


def _read_bounded_request(path: Path, *, limit: int, record_kind: str) -> dict[str, Any]:
    stream = sys.stdin.buffer if path == Path("-") else path.open("rb")
    try:
        return read_bounded_json_object(stream, limit=limit, record_kind=record_kind)
    finally:
        if path != Path("-"):
            stream.close()


def _require_request_fields(
    request: dict[str, Any],
    *,
    required: set[str],
    optional: set[str],
    record_kind: str,
) -> None:
    fields = set(request)
    if not required <= fields or not fields <= required | optional:
        raise ResearchKBError(
            Diagnostic(
                SCHEMA_VALIDATION_FAILED,
                record_kind,
                None,
                "",
                "request fields do not match the interface contract",
            )
        )


def _question_list(args: argparse.Namespace) -> int:
    layout = WorkspaceLayout.load(args.workspace)
    _write_json(QuestionQueryService(layout).list())
    return 0


def _question_show(args: argparse.Namespace) -> int:
    layout = WorkspaceLayout.load(args.workspace)
    _write_json(QuestionQueryService(layout).show(args.question_id))
    return 0


def _question_render(args: argparse.Namespace) -> int:
    layout = WorkspaceLayout.load(args.workspace)
    content = WorkspaceQuestionReadingViewService(layout).render(args.question_id)
    _write_bytes_once(content)
    return 0


def _step7_context(args: argparse.Namespace) -> int:
    layout = WorkspaceLayout.load(args.workspace)
    _write_json_once(Step7ContextService(layout).show(question_id=args.question_id))
    return 0


def _step7_render(args: argparse.Namespace) -> int:
    layout = WorkspaceLayout.load(args.workspace)
    content = WorkspaceStep7ReadingViewService(layout).render(args.question_id)
    _write_bytes_once(content)
    return 0


def _obsidian_status(args: argparse.Namespace) -> int:
    service, session = _obsidian_application(args.workspace)
    _write_json_once(
        service.status(
            session,
            page_size=args.page_size,
            cursor=args.cursor,
        )
    )
    return 0


def _obsidian_render(args: argparse.Namespace) -> int:
    service, session = _obsidian_application(args.workspace)
    preview = service.preview_render(session, optional_tables=args.optional_tables)
    if args.dry_run:
        if args.discard_managed_edits:
            raise ResearchKBError(
                Diagnostic(
                    SCHEMA_VALIDATION_FAILED,
                    "obsidian-generated-view-cli",
                    None,
                    "/discard_managed_edits",
                    "discard-managed-edits is valid only for an applied render",
                )
            )
        _write_json_once(preview)
        return 0
    result = service.render(
        session,
        {
            "optional_tables": args.optional_tables,
            "expected_state": preview["expected_state"],
            "discard_managed_edits": args.discard_managed_edits,
        },
        actor=args.actor,
    )
    _write_json_once(result)
    return 0


def _obsidian_application(workspace: Path):
    session = WorkspaceSessionService({"cli": workspace.resolve()}).open("cli")
    return ObsidianGeneratedViewsApplicationService(), session


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
