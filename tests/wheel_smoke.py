from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.docx_helpers import write_synthetic_docx


def _run_json(python: Path, cwd: Path, *args: str) -> dict:
    completed = subprocess.run(
        [str(python), "-m", "research_kb", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
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
        raise SystemExit("base wheel stdin command wrote unexpected stderr")
    return json.loads(completed.stdout)


def _tree_snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def _synthetic_discovery_selection() -> dict:
    result = {
        "result_key": "doi:10.0000/synthetic.wheel.discovery",
        "title": "Targeted degradation delivery in an invented wheel system",
        "authors": ["Alpha Researcher"],
        "first_publication_date": "2026-07-20",
        "journal_or_server": "Invented Journal",
        "doi": "10.0000/synthetic.wheel.discovery",
        "paper_type": "article",
        "publication_types": ["Journal Article"],
        "abstract": "Delivery was measured in the fabricated wheel study.",
        "matched_keywords": ["targeted degradation", "delivery"],
        "match_location": "both",
        "discovery_sources": [
            {"provider": "europe-pmc", "source": "MED", "record_id": "SYNTH-WHEEL-1"}
        ],
        "full_text_status": "unknown",
        "version_relationship": {"status": "unresolved", "related_doi": None},
        "possible_duplicate_result_keys": [],
    }
    return {
        "request_version": "1.0",
        "report": {
            "status": "success",
            "interface_version": "1.0",
            "provider": "europe-pmc",
            "provider_api_version": "synthetic-6.9",
            "query": {
                "date_from": "2026-07-14",
                "date_until": "2026-07-21",
                "title_keywords": ["targeted degradation"],
                "abstract_keywords": ["delivery"],
                "keyword_mode": "any",
                "include_preprints": True,
                "max_results": 15,
            },
            "provider_hit_count": 1,
            "scanned_result_count": 1,
            "returned_result_count": 1,
            "truncated": False,
            "persistent_writes": 0,
            "results": [result],
        },
        "selections": [{"result_key": result["result_key"], "target_question_ids": []}],
        "fixture_origin": "synthetic_from_scratch",
    }


def main() -> int:
    root = ROOT
    wheels = sorted((root / "dist").glob("research_kb_core-*.whl"))
    if not wheels:
        raise SystemExit("build a wheel before running the smoke test")
    with tempfile.TemporaryDirectory(prefix="research-kb-wheel-smoke-") as temporary:
        environment = Path(temporary) / "venv"
        venv.EnvBuilder(with_pip=True).create(environment)
        python = environment / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        subprocess.run([str(python), "-m", "pip", "install", str(wheels[-1])], check=True)
        subprocess.run([str(python), "-m", "research_kb", "--version"], cwd=temporary, check=True)
        capability = _run_json(python, Path(temporary), "capability", "show")
        pdf_capability = next(item for item in capability["parse_adapters"] if item["adapter"] == "pdfplumber")
        if pdf_capability != {
            "adapter": "pdfplumber",
            "availability": "dependency_missing",
            "version": None,
            "diagnostic_code": "RKBC-028",
        }:
            raise SystemExit("base wheel capability report did not expose the missing PDF dependency")
        if not {"adequacy gate", "adequacy show", "discovery search", "discovery list", "discovery show", "discovery resolve", "identity list", "intake inspect", "intake inspect-acquired", "job list", "job show", "manuscript inspect", "paper context", "review context", "source list", "source scan", "step7 context", "step7 render"}.issubset(capability["read_commands"]):
            raise SystemExit("base wheel capability report lacks deterministic intake/context reads")
        if not {"adequacy assess", "trunk advance"}.issubset(capability["write_commands"]):
            raise SystemExit("base wheel capability report lacks Source Adequacy writes")
        if capability["discovery_connectors"] != [{"connector": "europe-pmc", "availability": "available", "network_required": True}]:
            raise SystemExit("base wheel capability report lacks the Europe PMC connector")
        if capability["features"]["review_runtime"] is not True or capability["features"]["step7_runtime"] is not True or capability["features"]["on_demand_discovery"] is not True or capability["features"]["approved_discovery_candidate_handoff"] is not True or capability["features"]["legal_oa_resolution"] is not True or capability["features"]["explicit_oa_acquisition"] is not True or capability["features"]["manuscript_projection"] is not True or capability["features"]["pipeline_jobs"] is not True or capability["features"]["source_asset_runtime"] is not True or capability["features"]["registry_identity_correction"] is not True or capability["features"]["source_adequacy"] is not True or capability["features"]["deterministic_trunk"] is not True or capability["features"]["deterministic_intake_application"] is not True:
            raise SystemExit("base wheel capability report lacks Review Memory, Step 7 or discovery runtime")
        if (
            capability["features"]["agent_task_staging"] is not True
            or capability["features"]["knowledge_query_agent_tasks"] is not True
            or capability["features"]["organization_proposal_agent_tasks"] is not True
            or capability["agent_task_registry_version"] != "p7b-v1"
        ):
            raise SystemExit("base wheel capability report lacks current Agent Task staging")
        if capability["features"]["reading_evidence_source_access"] is not True:
            raise SystemExit("base wheel capability report lacks P5-B Evidence source access")
        subprocess.run(
            [
                str(python), "-c",
                "from research_kb.compatibility import CompatibilitySourceRef, LegacyReaderAdapter; "
                "from research_kb.contracts.registry import SchemaRegistry; "
                "from research_kb.guardian import GuardianService; "
                "from research_kb.discovery.europe_pmc import EuropePmcConnector, EuropePmcResolver; "
                "from research_kb.discovery.europe_pmc_pdf import EuropePmcPdfTransport; "
                "from research_kb.services import AcquiredCandidateIntakeService, CompatibilityAdapterRegistry, CompatibilityInspectionService, DeterministicTrunkService, DiscoveryAcquisitionService, DiscoveryAcquisitionTransportRegistry, DiscoveryCandidateService, DiscoveryConnectorRegistry, DiscoveryResolutionService, DiscoveryResolverRegistry, DiscoveryService, GuardianFindingDispositionService, IntakeInspectService, ManuscriptProjectionService, PaperContextService, ParseService, PipelineJobService, QuestionMappingService, QuestionReadingViewService, RecordService, RegistryService, ReviewContextService, ReviewMemoryService, SourceAdequacyService, Step7CandidateService, Step7ContextService, Step7ReadingViewService; "
                "registry = SchemaRegistry(); "
                "assert registry.schema('mutation-request')['$id'].endswith('mutation-request'); "
                "assert registry.schema('compatibility-difference')['$id'].endswith('compatibility-difference'); "
                "assert registry.schema('compatibility-report')['$id'].endswith('compatibility-report'); "
                "assert registry.schema('question-mapping')['$id'].endswith('question-mapping'); "
                "assert registry.schema('review-memory')['$id'].endswith('review-memory'); "
                "assert registry.schema('discovery-candidate')['$id'].endswith('discovery-candidate'); "
                "assert registry.schema('pipeline-job-state')['$id'].endswith('pipeline-job-state'); "
                "assert registry.schema('guardian-finding-disposition')['$id'].endswith('guardian-finding-disposition'); "
                "assert registry.schema('source-asset-state')['$id'].endswith('source-asset-state'); "
                "assert registry.schema('registry-identity-correction')['$id'].endswith('registry-identity-correction'); "
                "assert registry.schema('source-adequacy-profile')['$id'].endswith('source-adequacy-profile'); "
                "assert LegacyReaderAdapter.__name__ == 'LegacyReaderAdapter'; "
                "assert CompatibilitySourceRef.__name__ == 'CompatibilitySourceRef'; "
                "assert CompatibilityAdapterRegistry.__name__ == 'CompatibilityAdapterRegistry'; "
                "assert CompatibilityInspectionService.__name__ == 'CompatibilityInspectionService'; "
                "assert AcquiredCandidateIntakeService.__name__ == 'AcquiredCandidateIntakeService'; "
                "assert DiscoveryConnectorRegistry.__name__ == 'DiscoveryConnectorRegistry'; "
                "assert DiscoveryCandidateService.__name__ == 'DiscoveryCandidateService'; "
                "assert DiscoveryAcquisitionService.__name__ == 'DiscoveryAcquisitionService'; "
                "assert DiscoveryAcquisitionTransportRegistry.__name__ == 'DiscoveryAcquisitionTransportRegistry'; "
                "assert DiscoveryResolutionService.__name__ == 'DiscoveryResolutionService'; "
                "assert DiscoveryResolverRegistry.__name__ == 'DiscoveryResolverRegistry'; "
                "assert DiscoveryService.__name__ == 'DiscoveryService'; "
                "assert EuropePmcConnector.connector_id == 'europe-pmc'; "
                "assert EuropePmcResolver.resolver_id == 'europe-pmc'; "
                "assert EuropePmcPdfTransport.transport_id == 'europe-pmc'; "
                "assert IntakeInspectService.__name__ == 'IntakeInspectService'; "
                "assert ManuscriptProjectionService.__name__ == 'ManuscriptProjectionService'; "
                "assert PipelineJobService.__name__ == 'PipelineJobService'; "
                "assert DeterministicTrunkService.__name__ == 'DeterministicTrunkService'; "
                "assert SourceAdequacyService.__name__ == 'SourceAdequacyService'; "
                "assert GuardianFindingDispositionService.__name__ == 'GuardianFindingDispositionService'; "
                "assert PaperContextService.__name__ == 'PaperContextService'; "
                "assert ReviewContextService.__name__ == 'ReviewContextService'; "
                "assert ReviewMemoryService.__name__ == 'ReviewMemoryService'; "
                "assert QuestionMappingService.__name__ == 'QuestionMappingService'; "
                "assert QuestionReadingViewService.__name__ == 'QuestionReadingViewService'; "
                "assert Step7CandidateService.__name__ == 'Step7CandidateService'; "
                "assert Step7ContextService.__name__ == 'Step7ContextService'; "
                "assert Step7ReadingViewService.__name__ == 'Step7ReadingViewService'",
            ],
            cwd=temporary,
            check=True,
        )
        subprocess.run(
            [str(python), "-m", "research_kb", "discovery", "search", "--help"],
            cwd=temporary,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            [str(python), "-m", "research_kb", "discovery", "resolve", "--help"],
            cwd=temporary,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            [str(python), "-m", "research_kb", "discovery", "acquire", "--help"],
            cwd=temporary,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            [str(python), "-m", "research_kb", "intake", "inspect-acquired", "--help"],
            cwd=temporary,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            [str(python), "-m", "research_kb", "job", "create", "--help"],
            cwd=temporary,
            check=True,
            capture_output=True,
        )
        for group, command in (
            ("adequacy", "assess"),
            ("adequacy", "show"),
            ("adequacy", "gate"),
            ("trunk", "advance"),
            ("source", "list"),
            ("source", "associate"),
            ("source", "copy"),
            ("source", "select"),
            ("source", "observe"),
            ("source", "relink"),
            ("identity", "list"),
            ("identity", "correct"),
        ):
            subprocess.run(
                [str(python), "-m", "research_kb", group, command, "--help"],
                cwd=temporary,
                check=True,
                capture_output=True,
            )
        subprocess.run(
            [str(python), "-m", "research_kb", "manuscript", "inspect", "--help"],
            cwd=temporary,
            check=True,
            capture_output=True,
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
            "agent_policy": {
                "registry_version": "p7b-v1",
                "allowed_content_classes": ["metadata", "parsed_excerpt", "canonical_evidence", "paper_card_content", "operational_context", "review_background", "research_routing_context"],
                "execution_scope": "cloud_allowed",
                "max_prompt_bytes": 262_144,
                "max_result_bytes": 65_536,
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
        if marker["layout_contract_version"] != "p7a-1":
            raise SystemExit("wheel workspace did not initialize at p7a-1")
        if not (workspace_root / "knowledge" / "questions").is_dir():
            raise SystemExit("wheel workspace lacks questions directory")
        if (workspace_root / "knowledge" / "questions" / "mappings.jsonl").exists():
            raise SystemExit("workspace init created an empty question store")
        if not (workspace_root / "knowledge" / "review_memories" / "by_paper").is_dir():
            raise SystemExit("wheel workspace lacks review memory directories")
        if any((workspace_root / "knowledge" / "review_memories" / "by_paper").iterdir()):
            raise SystemExit("workspace init created a review memory record")
        if not (workspace_root / "knowledge" / "primary_bundles" / "by_paper").is_dir():
            raise SystemExit("wheel workspace lacks Primary bundle directories")
        if any((workspace_root / "knowledge" / "primary_bundles" / "by_paper").iterdir()):
            raise SystemExit("workspace init created a Primary bundle record")
        if not (workspace_root / "knowledge" / "review_bundles" / "by_paper").is_dir():
            raise SystemExit("wheel workspace lacks Review bundle directories")
        if any((workspace_root / "knowledge" / "review_bundles" / "by_paper").iterdir()):
            raise SystemExit("workspace init created a Review bundle record")
        if not (workspace_root / "knowledge" / "step7").is_dir():
            raise SystemExit("wheel workspace lacks Step 7 directory")
        if any((workspace_root / "knowledge" / "step7").iterdir()):
            raise SystemExit("workspace init created an empty Step 7 store")
        if not (workspace_root / "knowledge" / "discovery").is_dir():
            raise SystemExit("wheel workspace lacks discovery directory")
        if any((workspace_root / "knowledge" / "discovery").iterdir()):
            raise SystemExit("workspace init created an empty discovery store")
        if _run_json(
            python, Path(temporary), "source", "list", "--workspace", str(config_path)
        )["source_assets"] != []:
            raise SystemExit("base wheel source list was not empty after bootstrap")
        if _run_json(
            python, Path(temporary), "identity", "list", "--workspace", str(config_path)
        )["items"] != []:
            raise SystemExit("base wheel identity list was not empty after bootstrap")

        manuscript_docx = write_synthetic_docx(
            sources / "wheel-manuscript.docx",
            body_xml=(
                "<w:p><w:pPr><w:pStyle w:val=\"Heading1\"/></w:pPr>"
                "<w:r><w:t>Invented wheel manuscript heading</w:t></w:r></w:p>"
                "<w:p><w:r><w:t>Invented wheel manuscript paragraph.</w:t></w:r></w:p>"
            ),
        )
        manuscript_pdf = sources / "wheel-manuscript.pdf"
        manuscript_pdf.write_bytes(
            bytes((37, 80, 68, 70, 45)) + b"1.7\nsynthetic base-wheel dependency boundary\n"
        )
        manuscript_knowledge_before = _tree_snapshot(workspace_root / "knowledge")
        manuscript_sources_before = _tree_snapshot(sources)
        manuscript = _run_json(
            python,
            Path(temporary),
            "manuscript",
            "inspect",
            "--workspace",
            str(config_path),
            "--source",
            str(manuscript_docx),
        )
        if manuscript["document"]["parser"] != {"adapter": "ooxml-stdlib", "version": "1.0"}:
            raise SystemExit("base wheel DOCX manuscript parser identity is incorrect")
        if manuscript["document"]["unit_count"] != 2 or manuscript["persistent_writes"] != 0:
            raise SystemExit("base wheel DOCX manuscript projection is incomplete")
        unavailable_manuscript = subprocess.run(
            [
                str(python), "-m", "research_kb", "manuscript", "inspect",
                "--workspace", str(config_path), "--source", str(manuscript_pdf),
            ],
            cwd=temporary,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if unavailable_manuscript.returncode != 2 or unavailable_manuscript.stdout:
            raise SystemExit("base wheel manuscript PDF did not fail closed without pdfplumber")
        if json.loads(unavailable_manuscript.stderr)["diagnostic"]["code"] != "RKBC-028":
            raise SystemExit("base wheel manuscript PDF returned the wrong dependency diagnostic")
        if _tree_snapshot(workspace_root / "knowledge") != manuscript_knowledge_before:
            raise SystemExit("base wheel manuscript projection changed managed workspace files")
        if _tree_snapshot(sources) != manuscript_sources_before:
            raise SystemExit("base wheel manuscript projection changed source files")

        selection = _run_json_stdin(
            python,
            Path(temporary),
            _synthetic_discovery_selection(),
            "discovery",
            "select",
            "--workspace",
            str(config_path),
            "--request",
            "-",
            "--actor",
            "user",
        )
        candidate_id = selection["selected_candidate_ids"][0]
        listed_candidates = _run_json(
            python,
            Path(temporary),
            "discovery",
            "list",
            "--workspace",
            str(config_path),
        )
        shown_candidate = _run_json(
            python,
            Path(temporary),
            "discovery",
            "show",
            "--workspace",
            str(config_path),
            "--candidate-id",
            candidate_id,
        )
        if listed_candidates["candidate_count"] != 1 or shown_candidate["candidate"]["candidate_id"] != candidate_id:
            raise SystemExit("base wheel discovery candidate handoff failed")

        source = sources / "wheel-study.txt"
        source.write_text("The invented wheel response increased.\n", encoding="utf-8", newline="\n")
        intake = _run_json(
            python,
            Path(temporary),
            "intake",
            "inspect",
            "--workspace",
            str(config_path),
            "--source",
            str(source),
        )
        if intake["registration"] != {"paper_ids": [], "state": "unregistered"}:
            raise SystemExit("base wheel intake did not report an unregistered source")
        paper_id = _run_json_stdin(
            python,
            Path(temporary),
            {
                "bibliography": {
                    "title": "Synthetic Wheel Study",
                    "authors": ["Fixture Author"],
                    "year": 2026,
                    "doi": None,
                },
                "fixture_origin": "synthetic_from_scratch",
            },
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
            Path(temporary),
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
            raise SystemExit("base wheel intake did not recover the registered paper")
        source_before_parse = source.read_bytes()
        unavailable = subprocess.run(
            [
                str(python), "-m", "research_kb", "parse", "run",
                "--workspace", str(config_path),
                "--paper-id", paper_id,
                "--adapter", "pdfplumber",
            ],
            cwd=temporary,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if unavailable.returncode != 2 or unavailable.stdout:
            raise SystemExit("base wheel did not fail closed for unavailable PDF adapter")
        unavailable_payload = json.loads(unavailable.stderr)
        if unavailable_payload["diagnostic"]["code"] != "RKBC-028":
            raise SystemExit("base wheel returned the wrong unavailable-adapter diagnostic")
        parse_output = _run_json(
            python,
            Path(temporary),
            "parse",
            "run",
            "--workspace",
            str(config_path),
            "--paper-id",
            paper_id,
            "--adapter",
            "synthetic-text",
        )
        if parse_output["parser"] != {"adapter": "synthetic-text", "version": "1.0"}:
            raise SystemExit("base wheel synthetic parser identity is incorrect")
        parse_read = _run_json(
            python,
            Path(temporary),
            "parse",
            "show",
            "--workspace",
            str(config_path),
            "--paper-id",
            paper_id,
            "--page",
            "1",
        )
        if parse_read["returned_page_count"] != 1:
            raise SystemExit("base wheel parse show did not return one selected page")
        if source.read_bytes() != source_before_parse:
            raise SystemExit("base wheel parse changed the source asset")

        def promote(request: dict) -> dict:
            return _run_json_stdin(
                python,
                Path(temporary),
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

        common_context = {"paper_id": paper_id}
        evidence_id = promote(
            {
                "contract_version": "1.0",
                "operation": "append",
                "record_kind": "evidence",
                "target_record_id": None,
                "context": common_context,
                "payload": {
                    "claim": "The synthetic wheel response increased.",
                    "evidence_type": "reported_result",
                    "quote": "The invented wheel response increased.",
                    "source_page": {
                        "pdf_page": 1,
                        "printed_page": None,
                        "section": "Results",
                        "figure_or_table": None,
                    },
                    "locator": "page:1:block:1",
                    "support_scope": "The invented wheel case only.",
                    "what_it_does_not_support": ["Other synthetic cases"],
                    "review_status": "ai_checked",
                    "fixture_origin": "synthetic_from_scratch",
                },
            },
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
                    "claim_candidate": "The synthetic wheel response is universal.",
                    "reason": "The invented source contains one case.",
                    "source_page": {
                        "pdf_page": 1,
                        "printed_page": None,
                        "section": "Discussion",
                        "figure_or_table": None,
                    },
                    "locator": "page:1:block:2",
                    "resolution_status": "needs_resolution",
                    "review_status": "ai_checked",
                    "fixture_origin": "synthetic_from_scratch",
                },
            },
        )["record_id"]
        partial_context = _run_json(
            python,
            Path(temporary),
            "paper",
            "context",
            "--workspace",
            str(config_path),
            "--paper-id",
            paper_id,
        )
        if partial_context["paper_card"] is not None:
            raise SystemExit("base wheel partial context unexpectedly contains a Paper Card")
        if [item["evidence_id"] for item in partial_context["evidence"]] != [evidence_id]:
            raise SystemExit("base wheel partial context did not recover Evidence")
        if [item["queue_id"] for item in partial_context["review_queue"]] != [queue_id]:
            raise SystemExit("base wheel partial context did not recover review queue")
        sections = [
            {"section_id": item["section_id"], "units": []}
            for item in intake["domain_profile"]["paper_card_sections"]
        ]
        sections[1]["units"].append(
            {
                "section_id": sections[1]["section_id"],
                "statement": "The synthetic wheel study asks whether the response increases.",
                "statement_type": "reported_result",
                "grounding_status": "grounded",
                "evidence_ids": [evidence_id],
                "boundary_refs": [queue_id],
                "source_page": {
                    "pdf_page": 1,
                    "printed_page": None,
                    "section": "Results",
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
            },
        )
        promoted_context = _run_json(
            python,
            Path(temporary),
            "paper",
            "context",
            "--workspace",
            str(config_path),
            "--paper-id",
            paper_id,
        )
        unit_id = next(
            unit["unit_id"]
            for section in promoted_context["paper_card"]["sections"]
            for unit in section["units"]
        )
        question_id = promote(
            {
                "contract_version": "1.0",
                "operation": "append",
                "record_kind": "question-mapping",
                "target_record_id": None,
                "context": {"paper_id": None, "question_origin": "user_supplied"},
                "payload": {
                    "question_text": "How does the synthetic wheel response change?",
                    "scope": "The invented wheel fixture only.",
                    "mapping_status": "ai_checked",
                    "paper_links": [
                        {
                            "paper_id": paper_id,
                            "selected_card_unit_ids": [unit_id],
                            "role_in_question": "comparison",
                            "relevance_rationale": "The fabricated unit addresses the wheel question.",
                            "boundary_refs": [],
                        }
                    ],
                },
                "fixture_origin": "synthetic_from_scratch",
            },
        )["record_id"]
        insight_id = promote(
            {
                "contract_version": "1.0",
                "operation": "append",
                "record_kind": "step7-insight",
                "target_record_id": None,
                "context": {"paper_id": None, "question_origin": "existing_question"},
                "payload": {
                    "question_id": question_id,
                    "title": "Synthetic wheel insight",
                    "candidate_status": "keep",
                    "analysis_operator": "experiment_design",
                    "paper_card_base": [{"paper_id": paper_id, "card_unit_ids": [unit_id]}],
                    "missing_evidence": ["One independent fabricated wheel observation"],
                    "assumptions": ["The selected synthetic record remains comparable"],
                    "risk": ["The wheel fixture represents one setting"],
                    "testability": "Add one fabricated wheel observation.",
                    "next_action": "Retain for installed-wheel validation.",
                    "trace_status": "traceable",
                    "insight_type": "experimental_idea",
                    "hypothesis_or_idea": "One added wheel control may narrow interpretation.",
                    "rationale": "The selected Unit has canonical Evidence and an explicit boundary.",
                    "falsification_condition": "The added control leaves interpretation unchanged.",
                    "minimum_test": "Add one fabricated control arm."
                },
                "fixture_origin": "synthetic_from_scratch"
            }
        )["record_id"]

        before_knowledge = _tree_snapshot(workspace_root / "knowledge")
        before_sources = _tree_snapshot(sources)
        status = _run_json(
            python,
            Path(temporary),
            "paper",
            "status",
            "--workspace",
            str(config_path),
            "--paper-id",
            paper_id,
        )
        context = _run_json(
            python,
            Path(temporary),
            "paper",
            "context",
            "--workspace",
            str(config_path),
            "--paper-id",
            paper_id,
        )
        if status["paper_card"]["unit_count"] != 1 or status["question_mappings"]["linked_count"] != 1:
            raise SystemExit("base wheel paper status did not project the completed synthetic chain")
        if not status["integrity"]["mutation_safe"]:
            raise SystemExit("base wheel paper status unexpectedly blocked mutation")
        if next(
            unit["unit_id"]
            for section in context["paper_card"]["sections"]
            for unit in section["units"]
        ) != unit_id:
            raise SystemExit("base wheel paper context did not preserve the Card Unit ID")
        if [item["evidence_id"] for item in context["evidence"]] != [evidence_id]:
            raise SystemExit("base wheel paper context did not preserve Evidence")
        if [item["queue_id"] for item in context["review_queue"]] != [queue_id]:
            raise SystemExit("base wheel paper context did not preserve review queue")
        if _tree_snapshot(workspace_root / "knowledge") != before_knowledge:
            raise SystemExit("base wheel deterministic reads changed managed workspace files")
        if _tree_snapshot(sources) != before_sources:
            raise SystemExit("base wheel deterministic reads changed source files")
        rendered = subprocess.run(
            [
                str(python),
                "-m",
                "research_kb",
                "question",
                "render",
                "--workspace",
                str(config_path),
                "--question-id",
                question_id,
            ],
            cwd=temporary,
            check=True,
            capture_output=True,
        )
        if rendered.stderr:
            raise SystemExit("wheel render wrote stderr")
        if b"\r" in rendered.stdout or not rendered.stdout.endswith(b"\n"):
            raise SystemExit("wheel render is not UTF-8/LF output")
        rendered_text = rendered.stdout.decode("utf-8")
        for required in (
            'view_type: "question_reading_view"',
            "## Linked Papers And Selected Card Units",
            "## Canonical Evidence Trace",
            "## Review Queue Boundaries",
            evidence_id,
            queue_id,
        ):
            if required not in rendered_text:
                raise SystemExit(f"wheel render lacks required trace: {required}")
        if _tree_snapshot(workspace_root / "knowledge") != before_knowledge:
            raise SystemExit("wheel render changed managed workspace files")
        if _tree_snapshot(sources) != before_sources:
            raise SystemExit("wheel render changed source files")
        if (workspace_root / "knowledge" / "views").exists():
            raise SystemExit("wheel render created a views directory")

        step7_context = _run_json(
            python,
            Path(temporary),
            "step7",
            "context",
            "--workspace",
            str(config_path),
            "--question-id",
            question_id,
        )
        if step7_context["summary"]["total"] != 1 or step7_context["candidates"][0]["candidate"]["candidate_id"] != insight_id:
            raise SystemExit("base wheel Step 7 context did not recover the candidate")
        step7_rendered = subprocess.run(
            [
                str(python),
                "-m",
                "research_kb",
                "step7",
                "render",
                "--workspace",
                str(config_path),
                "--question-id",
                question_id,
            ],
            cwd=temporary,
            check=True,
            capture_output=True,
        )
        if step7_rendered.stderr or insight_id.encode("utf-8") not in step7_rendered.stdout:
            raise SystemExit("base wheel Step 7 render did not expose the candidate")
        if b"canonical: false" not in step7_rendered.stdout or b"Review Queue Boundaries (Not Evidence)" not in step7_rendered.stdout:
            raise SystemExit("base wheel Step 7 render lacks candidate boundaries")
        if _tree_snapshot(workspace_root / "knowledge") != before_knowledge:
            raise SystemExit("base wheel Step 7 reads changed managed workspace files")

        review_source = sources / "wheel-review.txt"
        review_source.write_text(
            "The fabricated wheel review repeats an existing orientation.\n",
            encoding="utf-8",
            newline="\n",
        )
        review_intake = _run_json(
            python,
            Path(temporary),
            "intake",
            "inspect",
            "--workspace",
            str(config_path),
            "--source",
            str(review_source),
        )
        review_paper_id = _run_json_stdin(
            python,
            Path(temporary),
            {
                "bibliography": {"title": "Synthetic Wheel Review"},
                "fixture_origin": "synthetic_from_scratch",
            },
            "registry",
            "add",
            "--workspace",
            str(config_path),
            "--root-id",
            review_intake["source"]["root_id"],
            "--relative-path",
            review_intake["source"]["relative_path"],
            "--metadata",
            "-",
        )["paper_id"]
        _run_json(
            python,
            Path(temporary),
            "parse",
            "run",
            "--workspace",
            str(config_path),
            "--paper-id",
            review_paper_id,
            "--adapter",
            "synthetic-text",
        )
        review_sections = [
            {"section_id": section_id, "units": []}
            for section_id in (
                "review_objective_scope",
                "review_question_search_boundaries",
                "taxonomy_field_structure",
                "major_synthesis",
                "methods_metrics_guardrails",
                "gaps_frontiers",
                "primary_leads_reuse",
            )
        ]
        review_memory_id = promote(
            {
                "contract_version": "1.0",
                "operation": "append",
                "record_kind": "review-memory",
                "target_record_id": None,
                "context": {"paper_id": review_paper_id},
                "payload": {
                    "review_subtype": "narrative_review",
                    "review_subtype_source": "agent_high_confidence",
                    "review_subtype_reason": "The synthetic source is explicitly a secondary orientation.",
                    "read_status": "deep_read",
                    "scope_tags": ["synthetic_review"],
                    "one_sentence_reuse_value": "Records that the fabricated review is redundant.",
                    "memory_value": {
                        "status": "low_value",
                        "reason": "The fabricated orientation duplicates existing synthetic context.",
                    },
                    "coverage_limits": {
                        "unread_sections": [],
                        "weakly_read_sections": [],
                        "reason": "The one-line synthetic source was read completely.",
                    },
                    "sections": review_sections,
                    "non_reusable_notes": [
                        {"content": "The orientation is duplicated.", "reason": "duplicate"}
                    ],
                    "review_status": "ai_checked",
                    "fixture_origin": "synthetic_from_scratch",
                },
                "fixture_origin": "synthetic_from_scratch",
            }
        )["record_id"]
        review_before = _tree_snapshot(workspace_root / "knowledge")
        review_context = _run_json(
            python,
            Path(temporary),
            "review",
            "context",
            "--workspace",
            str(config_path),
            "--paper-id",
            review_paper_id,
        )
        if review_context["review_memory"]["review_memory_id"] != review_memory_id:
            raise SystemExit("base wheel review context did not recover Review Memory")
        if review_context["freshness"]["state"] != "current":
            raise SystemExit("base wheel Review Memory is unexpectedly stale")
        if review_context["review_memory"]["memory_value"]["status"] != "low_value":
            raise SystemExit("base wheel low-value Review Memory was not preserved")
        if _tree_snapshot(workspace_root / "knowledge") != review_before:
            raise SystemExit("base wheel review context changed managed workspace files")
        guardian = _run_json(
            python,
            Path(temporary),
            "guardian",
            "check",
            "--workspace",
            str(config_path),
        )
        if guardian["status"] != "success":
            raise SystemExit("base wheel Guardian rejected valid Review Memory")
        subprocess.run(
            [
                str(python), "-m", "research_kb", "contract", "validate",
                "--kind", "workspace", "--input", str(root / "templates" / "workspace.example.yaml"),
            ],
            cwd=temporary,
            check=True,
        )
        subprocess.run(
            [
                str(python),
                "-c",
                (
                    "from pathlib import Path; "
                    "from research_kb.application import APPLICATION_SERVICE_INTERFACE_VERSION; "
                    "from research_kb.services import CatalogCapabilityService, "
                    "CatalogProjectionService, CatalogQueryService, WorkspaceSession, "
                    "WorkspaceSessionService, SourceAssetService, RegistryIdentityCorrectionService, "
                    "AgentTaskApplicationService, DeterministicIntakeApplicationService, ReadingApplicationService, "
                    "DeterministicTrunkService, PipelineJobService; "
                    "assert APPLICATION_SERVICE_INTERFACE_VERSION == '1.12'; "
                    f"session = WorkspaceSessionService({{'wheel': Path({str(config_path)!r})}}).open('wheel'); "
                    "limits = DeterministicIntakeApplicationService().limits(session); "
                    "assert limits['interface_version'] == '1.12'; "
                    "assert limits['ingress_modes'] == ['upload', 'watched_inbox']; "
                    f"reading = ReadingApplicationService().show_paper(session, {paper_id!r}); "
                    "assert reading['persistent_writes'] == 0 and 'source_ref' not in str(reading); "
                    "layout = session._layout; "
                    "job = PipelineJobService(layout).create(requested_route='local_source', "
                    "requested_depth='semantic_gate', current_node='source_check', "
                    f"input_refs=[{paper_id!r}], "
                    "authority_snapshot={'actor':'user','granted_operations':['advance_deterministic_trunk','assess_source_adequacy','observe_source','parse_run'],'captured_at':'2026-07-31T00:00:00Z'}, "
                    "idempotency_key='installed-wheel-agent-task', actor='user', fixture_origin='synthetic_from_scratch').state; "
                    f"waiting = DeterministicTrunkService(layout).advance(job_id=job['job_id'], paper_id={paper_id!r}, requested_operation='basic_paper_card', adapter_name='synthetic-text', actor='user'); "
                    "assert waiting.state['status'] == 'waiting_user'; "
                    "tasks = AgentTaskApplicationService(); "
                    "registry = tasks.registry(session); "
                    "assert registry['registry_version'] == 'p7b-v1'; "
                    "assert registry['embedded_agent_runtime'] is False; "
                    f"created = tasks.create_from_pipeline(session, job['job_id'], {{'paper_id':{paper_id!r},'task_kind':'document_route_resolution','executor_id':'codex_cli','approved_content_classes':['metadata','parsed_excerpt','operational_context'],'idempotency_key':'installed-wheel-route-task'}}); "
                    "expected = {'state_id':created['task']['state_id'],'state_digest':created['task']['state_digest']}; "
                    "inspected = tasks.inspect_handoff(session, created['task']['task_id'], expected, 'codex_cli'); "
                    "assert inspected['persistent_writes'] == 0 and 'prompt' not in inspected['handoff_preview'] and 'lease' not in inspected; "
                    "prepared = tasks.prepare_handoff(session, created['task']['task_id'], expected, 'codex_cli'); "
                    "expected = {'state_id':prepared['task']['state_id'],'state_digest':prepared['task']['state_digest']}; "
                    "recovered = tasks.prepare_handoff(session, prepared['task']['task_id'], expected, 'codex_cli'); "
                    "assert recovered['handoff'] == prepared['handoff'] and recovered['lease'] == prepared['lease'] and recovered['persistent_writes'] == 0; "
                    "result = {'contract_version':'p4a-document-route-decision@1.0','task_id':prepared['task']['task_id'],'input_basis_digest':prepared['task']['input_basis_digest'],'document_route':'primary','route_reason':None,'confidence':'high','rationale':'Synthetic installed-wheel route decision.'}; "
                    "submitted = tasks.submit_result(session, prepared['task']['task_id'], expected, prepared['lease'], result); "
                    "assert tasks.preview_result(session, submitted['task']['task_id'])['candidate']['canonical_scientific_write'] is False; "
                    "expected = {'state_id':submitted['task']['state_id'],'state_digest':submitted['task']['state_digest']}; "
                    "approved = tasks.approve_route_result(session, submitted['task']['task_id'], expected); "
                    "assert approved['task']['status'] == 'approved'; "
                    "assert approved['pipeline']['current_node'] == 'primary_semantic_gate'"
                ),
            ],
            cwd=temporary,
            check=True,
        )
        primary_bundle_source = sources / "wheel-primary-bundle.txt"
        primary_bundle_source.write_bytes(
            bytes((37, 80, 68, 70, 45))
            + b"1.7\nThe invented Primary bundle response increased.\n%%EOF\n"
        )
        subprocess.run(
            [
                str(python),
                "-c",
                (
                    "from pathlib import Path; "
                    "from research_kb.guardian import GuardianService; "
                    "from research_kb.services import AgentTaskApplicationService, DeterministicTrunkService, PipelineJobService, ReadingApplicationService, RegistryService, WorkspaceSessionService; "
                    "from research_kb.storage.json_io import read_json_document; "
                    f"session = WorkspaceSessionService({{'wheel': Path({str(config_path)!r})}}).open('wheel'); "
                    "layout = session._layout; "
                    f"paper, _ = RegistryService(layout).add(root_id='wheel-sources', relative_path={primary_bundle_source.name!r}, metadata={{'bibliography':{{'title':'Synthetic Primary Bundle Study','authors':['Fixture Author'],'year':2026,'doi':None}},'fixture_origin':'synthetic_from_scratch'}}); "
                    "origin = PipelineJobService(layout).create(requested_route='local_source', requested_depth='semantic_gate', current_node='source_check', input_refs=[paper['paper_id']], authority_snapshot={'actor':'user','granted_operations':['advance_deterministic_trunk','assess_source_adequacy','observe_source','parse_run'],'captured_at':'2026-07-31T00:00:00Z'}, idempotency_key='installed-wheel-primary-origin', actor='user', fixture_origin='synthetic_from_scratch').state; "
                    "completed = DeterministicTrunkService(layout).advance(job_id=origin['job_id'], paper_id=paper['paper_id'], requested_operation='basic_paper_card', adapter_name='synthetic-text', actor='user', document_route='primary', route_reason=None); "
                    "assert completed.state['status'] == 'completed' and completed.state['current_node'] == 'primary_semantic_gate', 'primary trunk'; "
                    "tasks = AgentTaskApplicationService(); "
                    "created = tasks.create_from_pipeline(session, origin['job_id'], {'paper_id':paper['paper_id'],'task_kind':'primary_semantic_processing','executor_id':'claude_code_cli','approved_content_classes':['metadata','parsed_excerpt','operational_context'],'idempotency_key':'installed-wheel-primary-task'}); "
                    "expected = {'state_id':created['task']['state_id'],'state_digest':created['task']['state_digest']}; "
                    "prepared = tasks.prepare_handoff(session, created['task']['task_id'], expected, 'claude_code_cli'); "
                    "section_ids = prepared['handoff']['payload']['operational_context']['paper_card_sections']; "
                    "source_page = {'pdf_page':1,'printed_page':None,'section':'Synthetic results','figure_or_table':None}; "
                    "sections = [{'section_id':section_id,'units':[]} for section_id in section_ids]; "
                    "sections[3]['units'] = [{'statement':'The invented Primary bundle response increased.','statement_type':'reported_result','grounding_status':'grounded','evidence_aliases':['ev_result'],'boundary_aliases':[],'source_page':source_page,'confidence':'high'}]; "
                    "candidate = {'contract_version':'p4b-primary-semantic-candidate@1.0','task_id':prepared['task']['task_id'],'input_basis_digest':prepared['task']['input_basis_digest'],'evidence':[{'alias':'ev_result','claim':'The invented Primary bundle response increased.','evidence_type':'reported_result','quote':'The invented Primary bundle response increased.','source_page':source_page,'locator':'page:1:block:1','support_scope':'The installed-wheel synthetic source only.','what_it_does_not_support':['Other sources'],'requested_operation':'continuous_text_evidence'}],'review_boundaries':[],'sections':sections}; "
                    "expected = {'state_id':prepared['task']['state_id'],'state_digest':prepared['task']['state_digest']}; "
                    "submitted = tasks.submit_result(session, prepared['task']['task_id'], expected, prepared['lease'], candidate); "
                    "assert tasks.preview_result(session, submitted['task']['task_id'])['candidate']['canonical_scientific_write'] is False, 'primary preview'; "
                    "expected = {'state_id':submitted['task']['state_id'],'state_digest':submitted['task']['state_digest']}; "
                    "approved = tasks.approve_primary_result(session, submitted['task']['task_id'], expected); "
                    "assert approved['task']['status'] == 'approved', 'primary approval'; "
                    "assert approved['primary_bundle']['revision_number'] == 1 and approved['primary_bundle']['evidence_count'] == 1, 'primary bundle counts'; "
                    "assert layout.primary_bundle_path(paper['paper_id']).is_file(), 'primary bundle file'; "
                    "bundle = read_json_document(layout.primary_bundle_path(paper['paper_id']), record_kind='primary-semantic-bundle'); "
                    "evidence_id = bundle['revisions'][0]['evidence'][0]['evidence_id']; "
                    "unit = next(unit for section in bundle['revisions'][0]['paper_card']['sections'] for unit in section['units'] if unit['evidence_ids']); "
                    "query = tasks.create_knowledge_query(session, {'query_type':'single_paper_explanation','query_text':'Explain the bounded synthetic finding.','paper_ids':[paper['paper_id']],'include_review_background':False,'include_routing_context':False,'executor_id':'codex_cli','approved_content_classes':['metadata','canonical_evidence','paper_card_content','operational_context'],'idempotency_key':'installed-wheel-query-task'}); "
                    "query_expected = {'state_id':query['task']['state_id'],'state_digest':query['task']['state_digest']}; "
                    "query_handoff = tasks.prepare_handoff(session, query['task']['task_id'], query_expected, 'codex_cli'); "
                    "query_result = {'contract_version':'p5c-knowledge-query-report@1.0','task_id':query_handoff['task']['task_id'],'input_basis_digest':query_handoff['task']['input_basis_digest'],'query_type':'single_paper_explanation','answer_blocks':[{'block_role':'factual','text':'The installed-wheel synthetic response increased.','support_refs':[{'paper_id':paper['paper_id'],'card_unit_id':unit['unit_id'],'evidence_ids':[evidence_id]}],'background_refs':[],'background_only':False}],'unresolved_items':[],'persistence_status':'report_only','canonical_scientific_write':False}; "
                    "query_expected = {'state_id':query_handoff['task']['state_id'],'state_digest':query_handoff['task']['state_digest']}; "
                    "query_submitted = tasks.submit_result(session, query['task']['task_id'], query_expected, query_handoff['lease'], query_result); "
                    "assert tasks.preview_result(session, query['task']['task_id'])['candidate']['retention_class'] == 'current_task_report', 'query preview'; "
                    "query_expected = {'state_id':query_submitted['task']['state_id'],'state_digest':query_submitted['task']['state_digest']}; "
                    "query_accepted = tasks.accept_report(session, query['task']['task_id'], query_expected); "
                    "assert query_accepted['task']['status'] == 'approved' and query_accepted['canonical_scientific_write'] is False, 'query acceptance'; "
                    "organization = tasks.create_organization_proposal(session, {'target_kind':'direction','target_id':None,'proposal_goal':'Create one installed-wheel synthetic direction.','paper_ids':[paper['paper_id']],'include_review_background':False,'executor_id':'codex_cli','approved_content_classes':['metadata','canonical_evidence','paper_card_content','research_routing_context','operational_context'],'idempotency_key':'installed-wheel-organization-task'}); "
                    "organization_expected = {'state_id':organization['task']['state_id'],'state_digest':organization['task']['state_digest']}; "
                    "organization_handoff = tasks.prepare_handoff(session, organization['task']['task_id'], organization_expected, 'codex_cli'); "
                    "organization_candidate = {'contract_version':'p7b-organization-proposal@1.0','task_id':organization_handoff['task']['task_id'],'input_basis_digest':organization_handoff['task']['input_basis_digest'],'target_kind':'direction','target_id':None,'proposal':{'name':'Installed-wheel synthetic direction','scope':'The generated installed-wheel records only.','status':'active','unit_links':[{'source_kind':'primary','paper_id':paper['paper_id'],'unit_id':unit['unit_id'],'role':'factual_example','rationale':'The current grounded synthetic Unit is a bounded example.'}],'gap_notes':[]},'duplicate_notes':[],'unresolved_conflicts':[]}; "
                    "organization_expected = {'state_id':organization_handoff['task']['state_id'],'state_digest':organization_handoff['task']['state_digest']}; "
                    "organization_submitted = tasks.submit_result(session, organization['task']['task_id'], organization_expected, organization_handoff['lease'], organization_candidate); "
                    "assert tasks.preview_result(session, organization['task']['task_id'])['candidate']['approval_blocked'] is False, 'organization preview'; "
                    "organization_expected = {'state_id':organization_submitted['task']['state_id'],'state_digest':organization_submitted['task']['state_digest']}; "
                    "organization_approved = tasks.approve_organization_result(session, organization['task']['task_id'], organization_expected); "
                    "assert organization_approved['task']['status'] == 'approved' and organization_approved['canonical_scientific_write'] is True, 'organization approval'; "
                    "source_access = ReadingApplicationService().prepare_evidence_source(session, evidence_id); "
                    "assert source_access.descriptor['application_service_interface_version'] == '1.12', 'source interface'; "
                    "assert source_access.descriptor['media_type'] == 'application/pdf' and source_access.descriptor['persistent_writes'] == 0, 'source descriptor'; "
                    "assert 'source_ref' not in str(source_access.descriptor) and 'fingerprint' not in str(source_access.descriptor), 'source redaction'; "
                    "opened = ReadingApplicationService().open_evidence_source(session, source_access.handle); "
                    "assert opened.stream.read(5) == bytes((37,80,68,70,45)) and opened.pdf_page == 1, 'source bytes'; "
                    "opened.close(); "
                    "assert GuardianService(layout).check().report['status'] == 'success', 'primary guardian'"
                ),
            ],
            cwd=temporary,
            check=True,
        )
        review_bundle_source = sources / "wheel-review-bundle.txt"
        review_bundle_source.write_text(
            "The invented review separates two response classes.\n",
            encoding="utf-8",
            newline="\n",
        )
        subprocess.run(
            [
                str(python),
                "-c",
                (
                    "from pathlib import Path; "
                    "from research_kb.guardian import GuardianService; "
                    "from research_kb.services import AgentTaskApplicationService, DeterministicTrunkService, PipelineJobService, RegistryService, ReviewContextService, WorkspaceSessionService; "
                    f"session = WorkspaceSessionService({{'wheel': Path({str(config_path)!r})}}).open('wheel'); "
                    "layout = session._layout; "
                    f"paper, _ = RegistryService(layout).add(root_id='wheel-sources', relative_path={review_bundle_source.name!r}, metadata={{'bibliography':{{'title':'Synthetic Review Bundle Study','authors':['Fixture Author'],'year':2026,'doi':None}},'fixture_origin':'synthetic_from_scratch'}}); "
                    "origin = PipelineJobService(layout).create(requested_route='local_source', requested_depth='semantic_gate', current_node='source_check', input_refs=[paper['paper_id']], authority_snapshot={'actor':'user','granted_operations':['advance_deterministic_trunk','assess_source_adequacy','observe_source','parse_run'],'captured_at':'2026-07-31T00:00:00Z'}, idempotency_key='installed-wheel-review-origin', actor='user', fixture_origin='synthetic_from_scratch').state; "
                    "completed = DeterministicTrunkService(layout).advance(job_id=origin['job_id'], paper_id=paper['paper_id'], requested_operation='basic_review_memory', adapter_name='synthetic-text', actor='user', document_route='review', route_reason=None); "
                    "assert completed.state['status'] == 'completed' and completed.state['current_node'] == 'review_semantic_gate'; "
                    "tasks = AgentTaskApplicationService(); "
                    "created = tasks.create_from_pipeline(session, origin['job_id'], {'paper_id':paper['paper_id'],'task_kind':'review_semantic_processing','executor_id':'codex_cli','approved_content_classes':['metadata','parsed_excerpt','operational_context'],'idempotency_key':'installed-wheel-review-task'}); "
                    "expected = {'state_id':created['task']['state_id'],'state_digest':created['task']['state_digest']}; "
                    "prepared = tasks.prepare_handoff(session, created['task']['task_id'], expected, 'codex_cli'); "
                    "section_ids = prepared['handoff']['payload']['operational_context']['review_sections']; "
                    "sections = [{'section_id':section_id,'units':[]} for section_id in section_ids]; "
                    "sections[2]['units'] = [{'section_id':'taxonomy_field_structure','unit_type':'field_axis','content':'The invented review separates two response classes.','source_notes':[{'pdf_page':1,'printed_page':None,'section':'Synthetic taxonomy','figure_or_table':None,'note_type':'paraphrase','text':'The invented review presents two response classes.','locator':None,'reopen_priority':'high','requested_operation':'continuous_text_evidence'}],'workflow_impacts':[{'target':'primary_paper_reading','action':'Separate the two invented response classes during later reading.'}],'evidence_use':{'can_support_canonical_evidence':False,'can_guide_primary_grounding':True,'primary_grounding_required_before':['comparative_claim']},'reuse_quality':{'reuse_confidence':'medium','staleness_risk':'low','reason':'The taxonomy is explicit in the synthetic review.'},'primary_paper_lead':None}]; "
                    "candidate = {'contract_version':'p4c-review-semantic-candidate@1.0','task_id':prepared['task']['task_id'],'input_basis_digest':prepared['task']['input_basis_digest'],'review_subtype':'narrative_review','review_subtype_source':'agent_high_confidence','review_subtype_reason':'The synthetic document is a secondary synthesis.','read_status':'targeted_read','scope_tags':['synthetic_review'],'one_sentence_reuse_value':'Provides a fabricated taxonomy for later reading.','memory_value':{'status':'reusable','reason':'One actionable taxonomy is retained.'},'coverage_limits':{'unread_sections':[],'weakly_read_sections':[],'reason':'The complete synthetic source was read.'},'sections':sections,'non_reusable_notes':[]}; "
                    "expected = {'state_id':prepared['task']['state_id'],'state_digest':prepared['task']['state_digest']}; "
                    "submitted = tasks.submit_result(session, prepared['task']['task_id'], expected, prepared['lease'], candidate); "
                    "assert tasks.preview_result(session, submitted['task']['task_id'])['candidate']['background_only'] is True; "
                    "expected = {'state_id':submitted['task']['state_id'],'state_digest':submitted['task']['state_digest']}; "
                    "approved = tasks.approve_review_result(session, submitted['task']['task_id'], expected); "
                    "assert approved['task']['status'] == 'approved'; "
                    "assert approved['review_bundle']['revision_number'] == 1 and approved['review_bundle']['review_unit_count'] == 1; "
                    "assert layout.review_bundle_path(paper['paper_id']).is_file(); "
                    "assert ReviewContextService(layout).show(paper_id=paper['paper_id'])['review_memory']['background_only'] is True; "
                    "assert GuardianService(layout).check().report['status'] == 'success'"
                ),
            ],
            cwd=temporary,
            check=True,
        )
    print("wheel_smoke=success")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
