import json
from io import BytesIO, TextIOWrapper
from pathlib import Path

import pytest
import yaml

from research_kb.cli import _configure_standard_streams, _write_json, main
from research_kb.errors import Diagnostic
from research_kb.services.records import RecordService
from research_kb.storage.json_io import serialize_json
from research_kb.storage.transactions import TransactionManager
from tests.fixture_factory import make_bundle
from tests.runtime_helpers import make_runtime_workspace
from tests.unit.test_question_mapping_service import _append_request, _link, _prepare_paper


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    "private_value",
    [
        "Z:" + "/private/research",
        "\\" * 2 + "server\\share\\file",
        "/" + "home/private/file",
        "~" + "/private/file",
    ],
)
def test_diagnostic_output_redacts_absolute_and_home_paths(private_value: str) -> None:
    output = Diagnostic("RKBC-999", "synthetic", None, "", f"failed at '{private_value}'").to_dict()
    assert private_value not in output["message"]
    assert "<redacted-path>" in output["message"]


def test_contract_validate_cli(capsys) -> None:
    input_path = ROOT / "templates" / "workspace.example.yaml"
    before = input_path.read_bytes()
    result = main([
        "contract", "validate", "--kind", "workspace", "--input", str(input_path),
    ])
    output = json.loads(capsys.readouterr().out)
    assert result == 0
    assert output["status"] == "success"
    assert input_path.read_bytes() == before


def test_privacy_scan_cli(capsys) -> None:
    result = main(["privacy", "scan", "--root", str(ROOT)])
    output = json.loads(capsys.readouterr().out)
    assert result == 0
    assert output["unexpected_findings"] == []


def test_utf8_output_does_not_inherit_legacy_code_page(monkeypatch) -> None:
    raw = BytesIO()
    stream = TextIOWrapper(raw, encoding="ascii")
    monkeypatch.setattr("sys.stdout", stream)
    _configure_standard_streams()
    _write_json({"value": "\u6d4b\u8bd5"})
    stream.flush()
    assert "\u6d4b\u8bd5" in raw.getvalue().decode("utf-8")


def test_unknown_record_kind_returns_contract_registry_exit(capsys) -> None:
    result = main([
        "contract", "validate", "--kind", "definitions", "--input", str(ROOT / "templates" / "workspace.example.yaml"),
    ])
    output = json.loads(capsys.readouterr().out)
    assert result == 3
    assert output["diagnostics"][0]["code"] == "RKBC-003"


def test_cli_validates_cross_record_bundle(tmp_path, capsys) -> None:
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(json.dumps(make_bundle("alpha")), encoding="utf-8")
    result = main([
        "contract", "validate", "--kind", "workspace", "--input", str(ROOT / "templates" / "workspace.example.yaml"),
        "--bundle", str(bundle_path), "--actor", "cli",
    ])
    output = json.loads(capsys.readouterr().out)
    assert result == 0
    assert output["status"] == "success"


def test_cli_exit_codes_distinguish_validation_version_and_input_errors(tmp_path, capsys) -> None:
    schema_invalid = tmp_path / "schema-invalid.json"
    schema_invalid.write_text(json.dumps({"contract_version": "1.0"}), encoding="utf-8")
    assert main(["contract", "validate", "--kind", "workspace", "--input", str(schema_invalid)]) == 1
    capsys.readouterr()

    unsupported = tmp_path / "unsupported.json"
    unsupported.write_text(json.dumps({"contract_version": "2.0"}), encoding="utf-8")
    assert main(["contract", "validate", "--kind", "workspace", "--input", str(unsupported)]) == 3
    capsys.readouterr()

    malformed = tmp_path / "malformed.json"
    malformed.write_text("{", encoding="utf-8")
    assert main(["contract", "validate", "--kind", "workspace", "--input", str(malformed)]) == 2
    assert json.loads(capsys.readouterr().err)["status"] == "error"


def test_data_check_jsonl_reports_records_and_format_failures(tmp_path, capsys) -> None:
    evidence = next(
        entry["record"] for entry in make_bundle("alpha")["records"] if entry["kind"] == "evidence"
    )
    path = tmp_path / "evidence.jsonl"
    path.write_text(json.dumps(evidence) + "\n", encoding="utf-8", newline="\n")
    assert main(["data", "check-jsonl", "--kind", "evidence", "--input", str(path)]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["records"] == 1

    path.write_text("{}", encoding="utf-8")
    assert main(["data", "check-jsonl", "--kind", "evidence", "--input", str(path)]) == 1
    output = json.loads(capsys.readouterr().out)
    assert output["diagnostics"][0]["code"] == "RKBC-015"


def test_transaction_recover_cli_dry_run_is_read_only(tmp_path, capsys) -> None:
    layout = make_runtime_workspace(tmp_path)
    event_id = "event_a1111111-1111-4111-8111-111111111111"

    class Crash(BaseException):
        pass

    def crash(phase: str) -> None:
        if phase == "target_replaced":
            raise Crash()

    manager = TransactionManager(layout, event_id_factory=lambda: event_id)
    with pytest.raises(Crash):
        manager.promote_bytes(
            target=layout.registry_path,
            content=b'{"value":1}\n',
            target_store="registry",
            operation="registry_append",
            actor="cli",
            input_refs=[],
            output_refs=["paper_a1111111-1111-4111-8111-111111111111"],
            phase_hook=crash,
        )
    journal_before = layout.journal_path(event_id).read_bytes()
    result = main(["transaction", "recover", "--workspace", str(layout.config.path), "--dry-run"])
    output = json.loads(capsys.readouterr().out)
    assert result == 0
    assert output["actions"][0]["action"] == "append_missing_success_event"
    assert layout.journal_path(event_id).read_bytes() == journal_before


def test_transaction_recover_cli_reports_missing_completed_event_as_needs_resolution(tmp_path, capsys) -> None:
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
    journal_before = layout.journal_path(event_id).read_bytes()

    result = main(["transaction", "recover", "--workspace", str(layout.config.path), "--dry-run"])
    output = json.loads(capsys.readouterr().out)

    assert result == 4
    assert output["status"] == "needs_resolution"
    assert output["actions"] == [{"event_id": event_id, "action": "completed_event_missing"}]
    assert layout.journal_path(event_id).read_bytes() == journal_before


def test_m1b_cli_runs_registry_parse_record_and_guardian(tmp_path, capsys) -> None:
    layout = make_runtime_workspace(tmp_path)
    source = layout.source_roots["alpha-sources"] / "study.txt"
    source.write_text("Invented CLI page one.\fInvented CLI page two.", encoding="utf-8", newline="\n")
    metadata = tmp_path / "metadata.json"
    metadata.write_text(json.dumps({"fixture_origin": "synthetic_from_scratch"}), encoding="utf-8")

    assert main([
        "registry", "add", "--workspace", str(layout.config.path),
        "--root-id", "alpha-sources", "--relative-path", "study.txt", "--metadata", str(metadata),
    ]) == 0
    paper_id = json.loads(capsys.readouterr().out)["paper_id"]

    assert main([
        "parse", "run", "--workspace", str(layout.config.path),
        "--paper-id", paper_id, "--adapter", "synthetic-text",
    ]) == 0
    assert json.loads(capsys.readouterr().out)["pages"] == 2

    request = tmp_path / "evidence-request.json"
    request.write_text(json.dumps({
        "contract_version": "1.0",
        "operation": "append",
        "record_kind": "evidence",
        "target_record_id": None,
        "context": {"paper_id": paper_id},
        "payload": {
            "claim": "The invented CLI response was observed.",
            "evidence_type": "reported_result",
            "quote": "Invented CLI page one.",
            "source_page": {"pdf_page": 1, "printed_page": None, "section": "Synthetic", "figure_or_table": None},
            "locator": "page:1:block:1",
            "support_scope": "The invented CLI fixture only.",
            "what_it_does_not_support": ["External settings"],
            "review_status": "ai_checked",
            "fixture_origin": "synthetic_from_scratch",
        },
        "fixture_origin": "synthetic_from_scratch",
    }), encoding="utf-8")
    assert main([
        "record", "promote", "--workspace", str(layout.config.path),
        "--request", str(request), "--actor", "agent",
    ]) == 0
    assert json.loads(capsys.readouterr().out)["record_id"].startswith("evidence_")

    assert main([
        "guardian", "check", "--workspace", str(layout.config.path), "--write-report",
    ]) == 0
    guardian_output = json.loads(capsys.readouterr().out)
    assert guardian_output["status"] == "success"
    assert guardian_output["report_written"] is True


def test_guardian_cli_returns_findings_exit_for_changed_source(tmp_path, capsys) -> None:
    layout = make_runtime_workspace(tmp_path)
    source = layout.source_roots["alpha-sources"] / "study.txt"
    source.write_text("Invented original.\n", encoding="utf-8", newline="\n")
    metadata = tmp_path / "metadata.json"
    metadata.write_text("{}", encoding="utf-8")
    assert main([
        "registry", "add", "--workspace", str(layout.config.path),
        "--root-id", "alpha-sources", "--relative-path", "study.txt", "--metadata", str(metadata),
    ]) == 0
    capsys.readouterr()
    source.write_text("Invented changed.\n", encoding="utf-8", newline="\n")

    assert main(["guardian", "check", "--workspace", str(layout.config.path)]) == 1
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "failure"
    assert "RKBC-009" in {item["code"] for item in output["findings"]}


def test_workspace_init_cli_dry_run_apply_and_no_change(tmp_path, capsys) -> None:
    root = tmp_path / "cli-workspace"
    root.mkdir()
    (root / "sources").mkdir()
    fixture_root = ROOT / "tests" / "fixtures" / "workspaces" / "domain_alpha"
    (root / "workspace.yaml").write_bytes((fixture_root / "workspace.yaml").read_bytes())
    (root / "domain-profile.yaml").write_bytes((fixture_root / "domain-profile.yaml").read_bytes())
    config_path = root / "workspace.yaml"

    assert main(["workspace", "init", "--workspace", str(config_path), "--dry-run"]) == 0
    dry_run = json.loads(capsys.readouterr().out)
    assert dry_run["result"] == "planned"
    assert not (root / "knowledge").exists()

    assert main(["workspace", "init", "--workspace", str(config_path)]) == 0
    assert json.loads(capsys.readouterr().out)["result"] == "initialized"
    assert main(["workspace", "init", "--workspace", str(config_path)]) == 0
    assert json.loads(capsys.readouterr().out)["result"] == "no_change"


def test_question_list_and_show_are_deterministic_and_read_only(tmp_path, capsys) -> None:
    layout = make_runtime_workspace(tmp_path)
    prepared = _prepare_paper(layout, "question-cli.txt")
    mapping, _ = RecordService(layout).promote(_append_request([_link(prepared)]), actor="agent")
    before = {
        path.relative_to(layout.knowledge_root).as_posix(): path.read_bytes()
        for path in layout.knowledge_root.rglob("*")
        if path.is_file()
    }

    assert main(["question", "list", "--workspace", str(layout.config.path)]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed["questions"] == [{
        "question_id": mapping["question_id"],
        "question_text": mapping["question_text"],
        "scope": mapping["scope"],
        "mapping_status": mapping["mapping_status"],
        "linked_paper_count": 1,
        "updated_at": mapping["updated_at"],
    }]

    assert main([
        "question", "show", "--workspace", str(layout.config.path),
        "--question-id", mapping["question_id"],
    ]) == 0
    assert json.loads(capsys.readouterr().out)["question"] == mapping
    assert {
        path.relative_to(layout.knowledge_root).as_posix(): path.read_bytes()
        for path in layout.knowledge_root.rglob("*")
        if path.is_file()
    } == before


def test_question_show_missing_id_is_redacted_reference_error(tmp_path, capsys) -> None:
    layout = make_runtime_workspace(tmp_path)
    missing = "question_f0000000-0000-4000-8000-000000000001"

    result = main([
        "question", "show", "--workspace", str(layout.config.path),
        "--question-id", missing,
    ])

    captured = capsys.readouterr()
    assert result == 2
    assert json.loads(captured.err)["diagnostic"]["code"] == "RKBC-005"
    assert str(tmp_path) not in captured.err


def test_runtime_cli_reports_old_layout_as_upgrade_required(tmp_path, capsys) -> None:
    layout = make_runtime_workspace(tmp_path)
    marker = json.loads(layout.marker_path.read_text(encoding="utf-8"))
    marker["layout_contract_version"] = "m2a-1"
    layout.marker_path.write_bytes(serialize_json(marker))
    (layout.knowledge_root / "questions").rmdir()

    result = main(["guardian", "check", "--workspace", str(layout.config.path)])

    captured = capsys.readouterr()
    assert result == 4
    assert json.loads(captured.err)["diagnostic"]["code"] == "RKBC-027"
    assert str(tmp_path) not in captured.err


@pytest.mark.parametrize(
    "argv",
    [
        ["registry", "add", "--root-id", "alpha-sources", "--relative-path", "x.txt", "--metadata", "missing.json"],
        ["parse", "run", "--paper-id", "paper_a1111111-1111-4111-8111-111111111111", "--adapter", "synthetic-text"],
        ["record", "promote", "--request", "missing.json", "--actor", "agent"],
        ["compatibility", "inspect", "--adapter", "missing-adapter"],
        ["guardian", "check"],
        ["question", "list"],
        ["question", "show", "--question-id", "question_a1111111-1111-4111-8111-111111111111"],
        ["transaction", "recover", "--dry-run"],
    ],
)
def test_every_runtime_cli_command_requires_initialized_workspace(tmp_path, capsys, argv) -> None:
    root = tmp_path / "private-root"
    root.mkdir()
    (root / "sources").mkdir()
    fixture_root = ROOT / "tests" / "fixtures" / "workspaces" / "domain_alpha"
    config_path = root / "workspace.yaml"
    config_path.write_bytes((fixture_root / "workspace.yaml").read_bytes())
    (root / "domain-profile.yaml").write_bytes((fixture_root / "domain-profile.yaml").read_bytes())

    result = main([argv[0], argv[1], "--workspace", str(config_path), *argv[2:]])
    captured = capsys.readouterr()
    diagnostic = json.loads(captured.err)["diagnostic"]
    assert result == 4
    assert diagnostic["code"] == "RKBC-019"
    assert str(tmp_path) not in captured.err


def test_workspace_init_blocked_output_is_redacted_and_uses_exit_four(tmp_path, capsys) -> None:
    root = tmp_path / "private-root"
    root.mkdir()
    (root / "sources").mkdir()
    fixture_root = ROOT / "tests" / "fixtures" / "workspaces" / "domain_alpha"
    config_path = root / "workspace.yaml"
    config_path.write_bytes((fixture_root / "workspace.yaml").read_bytes())
    (root / "domain-profile.yaml").write_bytes((fixture_root / "domain-profile.yaml").read_bytes())
    knowledge = root / "knowledge"
    knowledge.mkdir()
    (knowledge / "unknown.txt").write_text("unknown", encoding="utf-8")

    result = main(["workspace", "init", "--workspace", str(config_path)])
    output_text = capsys.readouterr().out
    output = json.loads(output_text)
    assert result == 4
    assert output["status"] == "failure"
    assert output["result"] == "blocked"
    assert str(tmp_path) not in output_text


def test_workspace_init_cli_distinguishes_input_and_version_errors(tmp_path, capsys) -> None:
    missing = tmp_path / "private" / "missing.yaml"
    assert main(["workspace", "init", "--workspace", str(missing)]) == 2
    missing_output = capsys.readouterr().out
    assert str(tmp_path) not in missing_output

    malformed = tmp_path / "malformed.yaml"
    malformed.write_text("workspace: [", encoding="utf-8")
    assert main(["workspace", "init", "--workspace", str(malformed)]) == 2
    assert json.loads(capsys.readouterr().out)["result"] == "blocked"

    unsupported = tmp_path / "unsupported.yaml"
    unsupported.write_text("contract_version: '2.0'\n", encoding="utf-8", newline="\n")
    assert main(["workspace", "init", "--workspace", str(unsupported)]) == 3
    output = json.loads(capsys.readouterr().out)
    assert output["diagnostics"][0]["code"] == "RKBC-001"


def test_workspace_cli_redacts_absolute_paths_from_schema_and_runtime_errors(tmp_path, capsys) -> None:
    private_value = "Z:" + "/private/research"
    invalid_config = tmp_path / "invalid-workspace.yaml"
    invalid_config.write_text(
        yaml.safe_dump(
            {
                "contract_version": "1.0",
                "workspace": {
                    "id": "workspace_a1111111-1111-4111-8111-111111111111",
                    "knowledge_root": "./knowledge",
                    "source_roots": private_value,
                    "local_inbox": "./inbox",
                    "domain_profile": "./domain-profile.yaml",
                },
                "runtime": {
                    "path_serialization": "workspace_relative_posix",
                    "default_encoding": "utf-8",
                    "line_ending": "lf",
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
        newline="\n",
    )

    assert main(["workspace", "init", "--workspace", str(invalid_config)]) == 2
    assert private_value not in capsys.readouterr().out

    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    layout = make_runtime_workspace(runtime_root)
    source = layout.source_roots["alpha-sources"] / "study.txt"
    source.write_text("Invented runtime source.\n", encoding="utf-8", newline="\n")
    metadata = tmp_path / "metadata.json"
    metadata.write_text("{}", encoding="utf-8")
    layout.registry_path.write_bytes(b"{}")

    assert main([
        "registry", "add", "--workspace", str(layout.config.path),
        "--root-id", "alpha-sources", "--relative-path", "study.txt", "--metadata", str(metadata),
    ]) == 2
    assert str(tmp_path) not in capsys.readouterr().err
