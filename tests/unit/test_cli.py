import json
from io import BytesIO, TextIOWrapper
from pathlib import Path

import pytest

from research_kb.cli import _configure_standard_streams, _write_json, main
from research_kb.storage.transactions import TransactionManager
from tests.fixture_factory import make_bundle
from tests.runtime_helpers import make_runtime_workspace


ROOT = Path(__file__).resolve().parents[2]


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
