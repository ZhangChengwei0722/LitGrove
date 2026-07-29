from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

import research_kb.services.application_validation as application_validation
from research_kb.cli import main
from research_kb.discovery.base import DiscoveryCandidate, DiscoveryProviderResult, DiscoverySource
from research_kb.errors import PARSE_ADAPTER_UNAVAILABLE, Diagnostic, ResearchKBError
from research_kb.guardian import GuardianService
from research_kb.services.application_validation import (
    ContractValidationService,
    JsonlValidationService,
)
from research_kb.services.bootstrap import WorkspaceBootstrapService
from research_kb.services.capability import CapabilityService
from research_kb.services.discovery import DiscoveryConnectorRegistry, DiscoveryService
from research_kb.services.parse_application import ParseAdapterRegistry, ParseApplicationService
from research_kb.services.privacy_scan import PrivacyScanService
from research_kb.services.question_read import (
    QuestionQueryService,
    WorkspaceQuestionReadingViewService,
)
from research_kb.services.recovery import TransactionRecoveryService
from research_kb.services.step7_render import WorkspaceStep7ReadingViewService
from research_kb.services.question_mapping import QuestionMappingService
from research_kb.services.records import RecordService
from research_kb.services.registry import RegistryService
from research_kb.storage.transactions import TransactionManager
from tests.fixture_factory import make_bundle
from tests.integration.test_step7_runtime import _common, _request
from tests.runtime_helpers import make_runtime_workspace
from tests.unit.test_question_mapping_service import _append_request, _link, _prepare_paper


ROOT = Path(__file__).resolve().parents[2]


def test_contract_validation_service_matches_cli_payload_and_exit(capsys) -> None:
    record = make_bundle("alpha")["records"][0]["record"]
    service_result = ContractValidationService().validate(
        kind="workspace",
        record=record,
        bundle=None,
        actor="cli",
    )

    input_path = ROOT / "templates" / "workspace.example.yaml"
    assert main(["contract", "validate", "--kind", "workspace", "--input", str(input_path)]) == 0
    cli_payload = json.loads(capsys.readouterr().out)

    assert service_result.exit_code == 0
    assert service_result.to_dict() == cli_payload


def test_contract_validation_service_classifies_unknown_schema_and_deduplicates() -> None:
    result = ContractValidationService().validate(
        kind="definitions",
        record={"contract_version": "1.0"},
        bundle=None,
        actor="cli",
    )

    assert result.exit_code == 3
    assert [item["code"] for item in result.to_dict()["diagnostics"]] == ["RKBC-003"]


def test_jsonl_validation_service_reports_valid_and_malformed_store(tmp_path: Path) -> None:
    evidence = next(
        entry["record"] for entry in make_bundle("alpha")["records"] if entry["kind"] == "evidence"
    )
    path = tmp_path / "evidence.jsonl"
    path.write_text(json.dumps(evidence) + "\n", encoding="utf-8", newline="\n")

    valid = JsonlValidationService().check(path=path, kind="evidence", actor="cli")
    assert valid.exit_code == 0
    assert valid.to_dict()["records"] == 1

    path.write_text("{}", encoding="utf-8")
    malformed = JsonlValidationService().check(path=path, kind="evidence", actor="cli")
    assert malformed.exit_code == 1
    assert malformed.to_dict()["diagnostics"][0]["code"] == "RKBC-015"


def test_jsonl_validation_service_preserves_per_record_diagnostics(tmp_path: Path, monkeypatch) -> None:
    evidence = next(
        entry["record"] for entry in make_bundle("alpha")["records"] if entry["kind"] == "evidence"
    )
    path = tmp_path / "evidence.jsonl"
    path.write_text(json.dumps(evidence) + "\n", encoding="utf-8", newline="\n")
    diagnostic = Diagnostic("RKBC-001", "evidence", evidence["evidence_id"], "", "synthetic duplicate")

    monkeypatch.setattr(
        application_validation,
        "validate_record",
        lambda kind, record, *, actor: [diagnostic, diagnostic],
    )

    result = JsonlValidationService().check(path=path, kind="evidence", actor="cli")

    assert result.exit_code == 3
    assert result.diagnostics == (diagnostic, diagnostic)


def test_privacy_scan_service_projects_success_and_failure(tmp_path: Path) -> None:
    clean = PrivacyScanService().scan(root=tmp_path)
    assert clean.exit_code == 0
    assert clean.to_dict()["unexpected_findings"] == []

    (tmp_path / "unsafe.txt").write_text("token" + "=" + "x" * 12, encoding="utf-8")
    unsafe = PrivacyScanService().scan(root=tmp_path)
    assert unsafe.exit_code == 1
    assert unsafe.to_dict()["unexpected_findings"][0]["finding_type"] == "credential_like"


def test_question_query_and_render_services_match_cli(tmp_path: Path, capsys) -> None:
    layout = make_runtime_workspace(tmp_path)
    prepared = _prepare_paper(layout, "application-question.txt")
    mapping, _ = RecordService(layout).promote(_append_request([_link(prepared)]), actor="agent")
    query = QuestionQueryService(layout)

    assert main(["question", "list", "--workspace", str(layout.config.path)]) == 0
    assert query.list() == json.loads(capsys.readouterr().out)

    assert main([
        "question", "show", "--workspace", str(layout.config.path),
        "--question-id", mapping["question_id"],
    ]) == 0
    assert query.show(mapping["question_id"]) == json.loads(capsys.readouterr().out)

    expected = WorkspaceQuestionReadingViewService(layout).render(mapping["question_id"])
    assert main([
        "question", "render", "--workspace", str(layout.config.path),
        "--question-id", mapping["question_id"],
    ]) == 0
    assert expected == capsys.readouterr().out.encode("utf-8")


def test_workspace_step7_render_service_matches_cli(tmp_path: Path, capsys) -> None:
    layout = make_runtime_workspace(tmp_path)
    prepared = _prepare_paper(layout, "application-step7.txt")
    mapping, _ = QuestionMappingService(layout).promote(
        _append_request([_link(prepared)]),
        actor="agent",
    )
    payload = _common(mapping, [prepared], operator="experiment_design")
    payload.update(
        {
            "insight_type": "experimental_idea",
            "hypothesis_or_idea": "One fabricated control may narrow interpretation.",
            "rationale": "The selected synthetic Unit has a bounded claim.",
            "falsification_condition": "The added control does not change interpretation.",
            "minimum_test": "Add one fabricated control arm.",
        }
    )
    RecordService(layout).promote(_request("step7-insight", payload), actor="agent")

    expected = WorkspaceStep7ReadingViewService(layout).render(mapping["question_id"])
    assert main([
        "step7", "render", "--workspace", str(layout.config.path),
        "--question-id", mapping["question_id"],
    ]) == 0
    assert expected == capsys.readouterr().out.encode("utf-8")


def test_parse_application_service_owns_exact_adapter_selection(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    source = layout.source_roots["alpha-sources"] / "application-parse.txt"
    source.write_text("Synthetic parse page.", encoding="utf-8")
    paper, _ = RegistryService(layout).add(
        root_id="alpha-sources",
        relative_path=source.name,
        metadata={
            "bibliography": {"title": "Synthetic Parse Application"},
            "fixture_origin": "synthetic_from_scratch",
        },
    )

    result = ParseApplicationService(layout).run(
        paper_id=paper["paper_id"],
        adapter_name="synthetic-text",
        actor="cli",
    )
    assert result.to_dict()["parser"] == {"adapter": "synthetic-text", "version": "1.0"}

    with pytest.raises(ResearchKBError) as error:
        ParseApplicationService(layout, registry=ParseAdapterRegistry({})).run(
            paper_id=paper["paper_id"],
            adapter_name="synthetic-text",
            actor="cli",
        )
    assert error.value.diagnostic.code == PARSE_ADAPTER_UNAVAILABLE


def test_transaction_recovery_service_owns_resolution_classification(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    event_id = "event_a1111111-1111-4111-8111-111111111111"
    manager = TransactionManager(layout, event_id_factory=lambda: event_id)
    manager.promote_bytes(
        target=layout.registry_path,
        content=b'{"value":1}\n',
        target_store="registry",
        operation="registry_append",
        actor="cli",
        input_refs=[],
        output_refs=["paper_a1111111-1111-4111-8111-111111111111"],
    )
    layout.process_events_path.unlink()

    result = TransactionRecoveryService(layout).recover(dry_run=True)

    assert result.exit_code == 4
    assert result.to_dict() == {
        "status": "needs_resolution",
        "dry_run": True,
        "actions": [{"event_id": event_id, "action": "completed_event_missing"}],
    }


def test_cli_no_longer_imports_p1_business_composition_primitives() -> None:
    source = (ROOT / "src" / "research_kb" / "cli.py").read_text(encoding="utf-8")
    forbidden = {
        "load_workspace_entries",
        "records_of_kind",
        "validate_workspace_entries",
        "SchemaRegistry",
        "validate_bundle",
        "validate_record",
        "scan_repository",
        "TransactionManager",
        "MANUAL_RESOLUTION_ACTIONS",
        "SyntheticTextAdapter",
        "PdfPlumberAdapter",
        "PdfPlumberTextFlowAdapter",
    }

    assert sorted(name for name in forbidden if name in source) == []


def test_capability_service_and_cli_are_field_identical(capsys) -> None:
    expected = CapabilityService().show()

    assert main(["capability", "show"]) == 0

    assert json.loads(capsys.readouterr().out) == expected


def test_application_service_facade_is_public_package_surface() -> None:
    from research_kb import services

    expected = {
        "ContractValidationService",
        "JsonlValidationService",
        "ParseAdapterRegistry",
        "ParseApplicationService",
        "PrivacyScanService",
        "QuestionQueryService",
        "TransactionRecoveryService",
        "WorkspaceQuestionReadingViewService",
        "WorkspaceStep7ReadingViewService",
    }

    assert expected <= set(services.__all__)
    assert all(hasattr(services, name) for name in expected)


def test_workspace_mutation_service_and_cli_are_field_identical(tmp_path: Path, capsys) -> None:
    fixture = ROOT / "tests" / "fixtures" / "workspaces" / "domain_alpha"
    direct_root = tmp_path / "direct"
    cli_root = tmp_path / "cli"
    shutil.copytree(fixture, direct_root)
    shutil.copytree(fixture, cli_root)
    direct_config = direct_root / "workspace.yaml"
    cli_config = cli_root / "workspace.yaml"

    expected = WorkspaceBootstrapService(direct_config).run(dry_run=True)
    assert main(["workspace", "init", "--workspace", str(cli_config), "--dry-run"]) == 0

    assert json.loads(capsys.readouterr().out) == expected.to_dict()
    assert not (direct_root / "knowledge").exists()
    assert not (cli_root / "knowledge").exists()


def test_discovery_service_and_cli_are_field_identical(tmp_path: Path, capsys) -> None:
    class FakeConnector:
        connector_id = "europe-pmc"
        network_required = True

        def search(self, discovery_request):
            return DiscoveryProviderResult(
                provider="europe-pmc",
                provider_api_version="synthetic-1",
                provider_hit_count=1,
                scanned_result_count=1,
                exhausted=True,
                candidates=(
                    DiscoveryCandidate(
                        title="Invented application-service result",
                        authors=("Synthetic Author",),
                        first_publication_date="2026-07-29",
                        journal_or_server="Synthetic Journal",
                        doi="10.0000/synthetic.application",
                        paper_type="article",
                        publication_types=("Journal Article",),
                        abstract="A fabricated abstract for deterministic testing.",
                        discovery_sources=(DiscoverySource("europe-pmc", "MED", "APP-1"),),
                        full_text_status="unknown",
                    ),
                ),
            )

    request = {
        "request_version": "1.0",
        "date_from": "2026-07-22",
        "date_until": "2026-07-29",
        "title_keywords": ["application-service"],
        "abstract_keywords": ["fabricated"],
        "keyword_mode": "any",
        "include_preprints": True,
        "max_results": 15,
    }
    connector = FakeConnector()
    expected = DiscoveryService(DiscoveryConnectorRegistry((connector,))).search(
        "europe-pmc",
        request,
    )
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")

    assert main(
        ["discovery", "search", "--provider", "europe-pmc", "--request", str(request_path)],
        discovery_connectors=(connector,),
    ) == 0

    assert json.loads(capsys.readouterr().out) == expected


def test_guardian_service_and_cli_have_equivalent_read_only_result(tmp_path: Path, capsys) -> None:
    layout = make_runtime_workspace(tmp_path)
    direct = GuardianService(layout).check(write_report=False).report

    assert main(["guardian", "check", "--workspace", str(layout.config.path)]) == 0
    cli = json.loads(capsys.readouterr().out)

    assert cli["status"] == direct["status"]
    assert cli["findings"] == direct["findings"]
    assert cli["report_written"] is False
    assert cli["event_id"] is None
