import json
from importlib.metadata import version
from io import BytesIO, StringIO, TextIOWrapper
from pathlib import Path

import pytest
import yaml

from research_kb.cli import _configure_standard_streams, _write_bytes_once, _write_json, main
from research_kb.errors import Diagnostic
from research_kb.discovery.base import (
    DiscoveryCandidate,
    DiscoveryProviderResult,
    DiscoverySource,
)
from research_kb.parse.synthetic_text import SyntheticTextAdapter
from research_kb.services.parse import ParseService
from research_kb.services.registry import RegistryService
from research_kb.services.records import RecordService
from research_kb.storage.json_io import file_sha256, read_jsonl, serialize_json
from research_kb.storage.transactions import TransactionManager
from tests.fixture_factory import make_bundle
from tests.pdf_helpers import write_synthetic_pdf
from tests.runtime_helpers import make_runtime_workspace
from tests.unit.test_question_mapping_service import _append_request, _link, _prepare_paper
from tests.unit.test_review_memory_service import prepare_review_paper, review_payload


ROOT = Path(__file__).resolve().parents[2]


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


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


def test_capability_show_cli_is_workspace_independent(capsys) -> None:
    assert main(["capability", "show"]) == 0

    captured = capsys.readouterr()
    output = json.loads(captured.out)
    assert captured.err == ""
    assert output["status"] == "success"
    assert output["interface_version"] == "1.0"
    assert "intake inspect" in output["read_commands"]
    assert "intake inspect-acquired" in output["read_commands"]
    assert "paper context" in output["read_commands"]
    assert "paper status" in output["read_commands"]
    assert "review context" in output["read_commands"]
    assert "review-memory" in output["mutation_record_kinds"]
    assert output["features"]["review_runtime"] is True
    assert "discovery search" in output["read_commands"]
    assert "discovery list" in output["read_commands"]
    assert "discovery resolve" in output["read_commands"]
    assert "discovery show" in output["read_commands"]
    assert output["features"]["on_demand_discovery"] is True
    assert output["features"]["approved_discovery_candidate_handoff"] is True
    assert output["features"]["explicit_oa_acquisition"] is True
    assert output["features"]["legal_oa_resolution"] is True


def test_discovery_search_cli_stdin_and_file_are_equal_and_read_only(
    tmp_path,
    capsys,
    monkeypatch,
) -> None:
    class FakeConnector:
        connector_id = "europe-pmc"
        network_required = True

        def search(self, discovery_request):
            assert discovery_request.date_from.isoformat() == "2026-07-14"
            return DiscoveryProviderResult(
                provider="europe-pmc",
                provider_api_version="synthetic-1",
                provider_hit_count=1,
                scanned_result_count=1,
                exhausted=True,
                candidates=(
                    DiscoveryCandidate(
                        title="Targeted degradation in an invented system",
                        authors=("Alpha Researcher",),
                        first_publication_date="2026-07-20",
                        journal_or_server="Invented Journal",
                        doi="10.0000/synthetic.discovery",
                        paper_type="article",
                        publication_types=("Journal Article",),
                        abstract="Delivery was measured in the invented system.",
                        discovery_sources=(
                            DiscoverySource("europe-pmc", "MED", "SYNTH-1"),
                        ),
                        full_text_status="unknown",
                    ),
                ),
            )

    request = {
        "request_version": "1.0",
        "date_from": "2026-07-14",
        "date_until": "2026-07-21",
        "title_keywords": ["targeted degradation"],
        "abstract_keywords": ["delivery"],
        "keyword_mode": "any",
        "include_preprints": True,
        "max_results": 15,
    }
    request_bytes = json.dumps(request).encode("utf-8")
    request_path = tmp_path / "request.json"
    request_path.write_bytes(request_bytes)
    before = _tree_bytes(tmp_path)

    stream = TextIOWrapper(BytesIO(request_bytes), encoding="utf-8")
    monkeypatch.setattr("sys.stdin", stream)
    argv = ["discovery", "search", "--provider", "europe-pmc", "--request", "-"]
    assert main(argv, discovery_connectors=(FakeConnector(),)) == 0
    stdin_output = capsys.readouterr()

    argv[-1] = str(request_path)
    assert main(argv, discovery_connectors=(FakeConnector(),)) == 0
    file_output = capsys.readouterr()

    output = json.loads(stdin_output.out)
    assert stdin_output.err == file_output.err == ""
    assert stdin_output.out == file_output.out
    assert output["returned_result_count"] == 1
    assert output["persistent_writes"] == 0
    assert _tree_bytes(tmp_path) == before


def test_discovery_search_cli_failure_has_empty_stdout(tmp_path, capsys) -> None:
    request_path = tmp_path / "request.json"
    request_path.write_text(
        json.dumps(
            {
                "request_version": "1.0",
                "date_from": "2026-07-14",
                "date_until": "2026-07-21",
                "title_keywords": ["synthetic"],
                "abstract_keywords": [],
                "keyword_mode": "any",
                "include_preprints": True,
                "max_results": 1,
            }
        ),
        encoding="utf-8",
    )
    before = _tree_bytes(tmp_path)

    assert main([
        "discovery",
        "search",
        "--provider",
        "missing",
        "--request",
        str(request_path),
    ], discovery_connectors=()) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err)["diagnostic"]["code"] == "RKBC-032"
    assert _tree_bytes(tmp_path) == before


def test_intake_inspect_cli_is_deterministic_bounded_and_read_only(tmp_path, capsys) -> None:
    layout = make_runtime_workspace(tmp_path)
    source = layout.source_roots["alpha-sources"] / "nested" / "intake.pdf"
    source.parent.mkdir()
    source.write_text("Invented intake source.\n", encoding="utf-8", newline="\n")
    before_knowledge = _tree_bytes(layout.knowledge_root)
    before_sources = _tree_bytes(layout.source_roots["alpha-sources"])
    argv = [
        "intake", "inspect", "--workspace", str(layout.config.path), "--source", str(source),
    ]

    assert main(argv) == 0
    first = capsys.readouterr()
    assert main(argv) == 0
    second = capsys.readouterr()

    output = json.loads(first.out)
    assert first.err == second.err == ""
    assert first.out == second.out
    assert output["source"] == {
        "root_id": "alpha-sources",
        "relative_path": "nested/intake.pdf",
        "fingerprint_algorithm": "sha256",
    }
    assert output["registration"] == {"paper_ids": [], "state": "unregistered"}
    assert str(tmp_path) not in first.out
    assert file_sha256(source) not in first.out
    assert _tree_bytes(layout.knowledge_root) == before_knowledge
    assert _tree_bytes(layout.source_roots["alpha-sources"]) == before_sources


def test_intake_inspect_cli_failure_has_empty_stdout_and_no_mutation(tmp_path, capsys) -> None:
    layout = make_runtime_workspace(tmp_path)
    before = _tree_bytes(layout.knowledge_root)

    assert main([
        "intake", "inspect", "--workspace", str(layout.config.path), "--source", "relative.pdf",
    ]) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err)["diagnostic"]["code"] == "RKBC-007"
    assert _tree_bytes(layout.knowledge_root) == before


def test_utf8_output_does_not_inherit_legacy_code_page(monkeypatch) -> None:
    raw = BytesIO()
    stream = TextIOWrapper(raw, encoding="ascii")
    monkeypatch.setattr("sys.stdout", stream)
    _configure_standard_streams()
    _write_json({"value": "\u6d4b\u8bd5"})
    stream.flush()
    assert "\u6d4b\u8bd5" in raw.getvalue().decode("utf-8")


def test_raw_byte_writer_supports_binary_and_text_streams() -> None:
    raw = BytesIO()
    binary_stream = TextIOWrapper(raw, encoding="utf-8")
    _write_bytes_once("Synthetic \u03b1\n".encode("utf-8"), stream=binary_stream)
    assert raw.getvalue() == "Synthetic \u03b1\n".encode("utf-8")

    text_stream = StringIO()
    _write_bytes_once("Synthetic \u03b2\n".encode("utf-8"), stream=text_stream)
    assert text_stream.getvalue() == "Synthetic \u03b2\n"


def test_raw_byte_writer_rejects_short_write() -> None:
    class ShortTextStream(StringIO):
        def write(self, value: str) -> int:
            super().write(value[:-1])
            return len(value) - 1

    with pytest.raises(OSError, match="short stdout write"):
        _write_bytes_once(b"synthetic\n", stream=ShortTextStream())


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
    parse_output = json.loads(capsys.readouterr().out)
    assert parse_output["pages"] == 2
    assert parse_output["parser"] == {"adapter": "synthetic-text", "version": "1.0"}

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
    evidence_id = json.loads(capsys.readouterr().out)["record_id"]
    assert evidence_id.startswith("evidence_")

    before_context = _tree_bytes(layout.knowledge_root)
    assert main([
        "paper", "context", "--workspace", str(layout.config.path),
        "--paper-id", paper_id,
    ]) == 0
    context = json.loads(capsys.readouterr().out)
    assert context["paper_card"] is None
    assert [item["evidence_id"] for item in context["evidence"]] == [evidence_id]
    assert context["review_queue"] == []
    assert str(tmp_path) not in json.dumps(context)
    assert _tree_bytes(layout.knowledge_root) == before_context

    assert main([
        "guardian", "check", "--workspace", str(layout.config.path), "--write-report",
    ]) == 0
    guardian_output = json.loads(capsys.readouterr().out)
    assert guardian_output["status"] == "success"
    assert guardian_output["report_written"] is True


def test_cli_accepts_registry_metadata_and_mutation_request_from_stdin(
    tmp_path,
    capsys,
    monkeypatch,
) -> None:
    layout = make_runtime_workspace(tmp_path)
    source = layout.source_roots["alpha-sources"] / "stdin-study.txt"
    source.write_text("Invented stdin result.\n", encoding="utf-8", newline="\n")
    metadata_stream = TextIOWrapper(
        BytesIO(json.dumps({"fixture_origin": "synthetic_from_scratch"}).encode("utf-8")),
        encoding="utf-8",
    )
    monkeypatch.setattr("sys.stdin", metadata_stream)

    assert main([
        "registry", "add", "--workspace", str(layout.config.path),
        "--root-id", "alpha-sources", "--relative-path", source.name, "--metadata", "-",
    ]) == 0
    paper_id = json.loads(capsys.readouterr().out)["paper_id"]
    assert main([
        "parse", "run", "--workspace", str(layout.config.path),
        "--paper-id", paper_id, "--adapter", "synthetic-text",
    ]) == 0
    capsys.readouterr()

    request = {
        "contract_version": "1.0",
        "operation": "append",
        "record_kind": "evidence",
        "target_record_id": None,
        "context": {"paper_id": paper_id},
        "payload": {
            "claim": "The invented stdin result was reported.",
            "evidence_type": "reported_result",
            "quote": "Invented stdin result.",
            "source_page": {
                "pdf_page": 1,
                "printed_page": None,
                "section": "Synthetic",
                "figure_or_table": None,
            },
            "locator": "page:1:block:1",
            "support_scope": "The invented stdin fixture only.",
            "what_it_does_not_support": ["Other fixtures"],
            "review_status": "ai_checked",
            "fixture_origin": "synthetic_from_scratch",
        },
        "fixture_origin": "synthetic_from_scratch",
    }
    request_stream = TextIOWrapper(BytesIO(json.dumps(request).encode("utf-8")), encoding="utf-8")
    monkeypatch.setattr("sys.stdin", request_stream)

    assert main([
        "record", "promote", "--workspace", str(layout.config.path),
        "--request", "-", "--actor", "agent",
    ]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["record_kind"] == "evidence"
    assert len(read_jsonl(layout.evidence_path(paper_id), record_kind="evidence")) == 1
    assert not list(layout.knowledge_root.rglob("*.tmp"))


@pytest.mark.parametrize(
    "payload",
    [b"[]", b"\xff", b"{" + b" " * (64 * 1024) + b"}"],
    ids=("array", "invalid-utf8", "oversized"),
)
def test_registry_stdin_failure_is_bounded_and_preserves_registry(
    tmp_path,
    capsys,
    monkeypatch,
    payload: bytes,
) -> None:
    layout = make_runtime_workspace(tmp_path)
    source = layout.source_roots["alpha-sources"] / "invalid-stdin.txt"
    source.write_text("Invented input boundary.\n", encoding="utf-8", newline="\n")
    stream = TextIOWrapper(BytesIO(payload), encoding="utf-8")
    monkeypatch.setattr("sys.stdin", stream)

    assert main([
        "registry", "add", "--workspace", str(layout.config.path),
        "--root-id", "alpha-sources", "--relative-path", source.name, "--metadata", "-",
    ]) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err)["diagnostic"]["code"] in {"RKBC-002", "RKBC-030"}
    assert read_jsonl(layout.registry_path, record_kind="registry-paper") == []
    assert not list(layout.knowledge_root.rglob("*.tmp"))


@pytest.mark.parametrize(
    "payload",
    [b"[]", b"{" + b" " * (4 * 1024 * 1024) + b"}"],
    ids=("array", "oversized"),
)
def test_mutation_stdin_failure_preserves_the_complete_workspace(
    tmp_path,
    capsys,
    monkeypatch,
    payload: bytes,
) -> None:
    layout = make_runtime_workspace(tmp_path)
    before = _tree_bytes(layout.knowledge_root)
    stream = TextIOWrapper(BytesIO(payload), encoding="utf-8")
    monkeypatch.setattr("sys.stdin", stream)

    assert main([
        "record", "promote", "--workspace", str(layout.config.path),
        "--request", "-", "--actor", "agent",
    ]) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err)["diagnostic"]["code"] in {"RKBC-002", "RKBC-030"}
    assert _tree_bytes(layout.knowledge_root) == before
    assert not list(layout.knowledge_root.rglob("*.tmp"))


def test_parse_cli_dispatches_pdfplumber_and_reports_exact_identity(tmp_path, capsys) -> None:
    layout = make_runtime_workspace(tmp_path)
    source = write_synthetic_pdf(
        layout.source_roots["alpha-sources"] / "cli-real.pdf",
        ["Invented PDF CLI response."],
    )
    source_before = file_sha256(source)
    metadata = tmp_path / "metadata.json"
    metadata.write_text(json.dumps({"fixture_origin": "synthetic_from_scratch"}), encoding="utf-8")

    assert main([
        "registry", "add", "--workspace", str(layout.config.path),
        "--root-id", "alpha-sources", "--relative-path", source.name, "--metadata", str(metadata),
    ]) == 0
    paper_id = json.loads(capsys.readouterr().out)["paper_id"]

    assert main([
        "parse", "run", "--workspace", str(layout.config.path),
        "--paper-id", paper_id, "--adapter", "pdfplumber",
    ]) == 0
    output = json.loads(capsys.readouterr().out)

    assert output["parser"] == {"adapter": "pdfplumber", "version": version("pdfplumber")}
    assert output["pages"] == 1
    stored = read_jsonl(layout.parse_path(paper_id), record_kind="parsed-page")
    assert stored[0]["parser"] == output["parser"]
    assert file_sha256(source) == source_before


def test_parse_cli_does_not_fallback_when_pdfplumber_source_is_wrong_type(tmp_path, capsys) -> None:
    layout = make_runtime_workspace(tmp_path)
    source = layout.source_roots["alpha-sources"] / "not-pdf.txt"
    source.write_text("Invented non-PDF source.\n", encoding="utf-8", newline="\n")
    metadata = tmp_path / "metadata.json"
    metadata.write_text("{}", encoding="utf-8")
    assert main([
        "registry", "add", "--workspace", str(layout.config.path),
        "--root-id", "alpha-sources", "--relative-path", source.name, "--metadata", str(metadata),
    ]) == 0
    paper_id = json.loads(capsys.readouterr().out)["paper_id"]

    assert main([
        "parse", "run", "--workspace", str(layout.config.path),
        "--paper-id", paper_id, "--adapter", "pdfplumber",
    ]) == 2
    streams = capsys.readouterr()
    diagnostic = json.loads(streams.err)["diagnostic"]
    assert streams.out == ""
    assert diagnostic["code"] == "RKBC-029"
    assert not layout.parse_path(paper_id).exists()


def test_parse_cli_reports_unavailable_pdf_extra_without_target_write(tmp_path, capsys, monkeypatch) -> None:
    layout = make_runtime_workspace(tmp_path)
    source = write_synthetic_pdf(
        layout.source_roots["alpha-sources"] / "missing-extra.pdf",
        ["Invented dependency boundary."],
    )
    metadata = tmp_path / "metadata.json"
    metadata.write_text("{}", encoding="utf-8")
    assert main([
        "registry", "add", "--workspace", str(layout.config.path),
        "--root-id", "alpha-sources", "--relative-path", source.name, "--metadata", str(metadata),
    ]) == 0
    paper_id = json.loads(capsys.readouterr().out)["paper_id"]

    def missing_dependency(name: str):
        raise ModuleNotFoundError(name)

    monkeypatch.setattr("research_kb.parse.pdfplumber_adapter.import_module", missing_dependency)
    assert main([
        "parse", "run", "--workspace", str(layout.config.path),
        "--paper-id", paper_id, "--adapter", "pdfplumber",
    ]) == 2
    diagnostic = json.loads(capsys.readouterr().err)["diagnostic"]
    assert diagnostic["code"] == "RKBC-028"
    assert not layout.parse_path(paper_id).exists()


def test_parse_show_cli_emits_all_or_one_page_without_writes(tmp_path, capsys) -> None:
    layout = make_runtime_workspace(tmp_path)
    source = layout.source_roots["alpha-sources"] / "show-pages.txt"
    source.write_text("Invented first page.\fInvented second page.", encoding="utf-8", newline="\n")
    paper, _ = RegistryService(layout).add(
        root_id="alpha-sources",
        relative_path=source.name,
        metadata={"fixture_origin": "synthetic_from_scratch"},
    )
    ParseService(layout).run(paper_id=paper["paper_id"], adapter=SyntheticTextAdapter())
    before = _tree_bytes(layout.knowledge_root)

    assert main([
        "parse", "show", "--workspace", str(layout.config.path), "--paper-id", paper["paper_id"],
    ]) == 0
    full = json.loads(capsys.readouterr().out)
    assert full["page_count"] == 2
    assert full["returned_page_count"] == 2
    assert main([
        "parse", "show", "--workspace", str(layout.config.path), "--paper-id", paper["paper_id"], "--page", "2",
    ]) == 0
    selected = json.loads(capsys.readouterr().out)
    assert [item["pdf_page"] for item in selected["pages"]] == [2]
    assert _tree_bytes(layout.knowledge_root) == before


def test_parse_show_cli_invalid_page_is_structured_failure(tmp_path, capsys) -> None:
    layout = make_runtime_workspace(tmp_path)

    assert main([
        "parse", "show", "--workspace", str(layout.config.path),
        "--paper-id", "paper_a1111111-1111-4111-8111-111111111111", "--page", "zero",
    ]) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err)["diagnostic"]["code"] == "RKBC-002"


def test_paper_status_cli_is_deterministic_bounded_and_read_only(tmp_path, capsys) -> None:
    layout = make_runtime_workspace(tmp_path)
    prepared = _prepare_paper(layout, "status-cli.txt")
    paper_id = prepared["paper"]["paper_id"]
    before = _tree_bytes(layout.knowledge_root)
    argv = ["paper", "status", "--workspace", str(layout.config.path), "--paper-id", paper_id]

    assert main(argv) == 0
    first = capsys.readouterr()
    assert main(argv) == 0
    second = capsys.readouterr()

    output = json.loads(first.out)
    assert first.err == second.err == ""
    assert first.out == second.out
    assert output["interface_version"] == "1.0"
    assert output["paper_id"] == paper_id
    assert output["paper_card"]["unit_count"] == 3
    assert "Invented" not in first.out
    assert str(tmp_path) not in first.out
    assert _tree_bytes(layout.knowledge_root) == before


def test_paper_status_cli_unknown_paper_has_empty_stdout(tmp_path, capsys) -> None:
    layout = make_runtime_workspace(tmp_path)

    assert main([
        "paper", "status", "--workspace", str(layout.config.path),
        "--paper-id", "paper_a1111111-1111-4111-8111-111111111111",
    ]) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err)["diagnostic"]["code"] == "RKBC-005"


def test_review_memory_stdin_promotion_and_context_cli_are_deterministic(tmp_path, capsys) -> None:
    layout = make_runtime_workspace(tmp_path)
    paper, _ = prepare_review_paper(layout)
    request = tmp_path / "review-memory-request.json"
    request.write_text(
        json.dumps(
            {
                "contract_version": "1.0",
                "operation": "append",
                "record_kind": "review-memory",
                "target_record_id": None,
                "context": {"paper_id": paper["paper_id"]},
                "payload": review_payload(),
                "fixture_origin": "synthetic_from_scratch",
            }
        ),
        encoding="utf-8",
        newline="\n",
    )

    assert main(
        [
            "record",
            "promote",
            "--workspace",
            str(layout.config.path),
            "--request",
            str(request),
            "--actor",
            "agent",
        ]
    ) == 0
    promoted = json.loads(capsys.readouterr().out)
    before = _tree_bytes(layout.knowledge_root)
    argv = [
        "review",
        "context",
        "--workspace",
        str(layout.config.path),
        "--paper-id",
        paper["paper_id"],
    ]

    assert promoted["record_kind"] == "review-memory"
    assert promoted["record_id"].startswith("reviewmem_")
    assert main(argv) == 0
    first = capsys.readouterr()
    assert main(argv) == 0
    second = capsys.readouterr()
    context = json.loads(first.out)
    assert first.err == second.err == ""
    assert first.out == second.out
    assert context["review_memory"]["review_memory_id"] == promoted["record_id"]
    assert context["freshness"] == {"state": "current", "reasons": []}
    assert str(tmp_path) not in first.out
    assert _tree_bytes(layout.knowledge_root) == before


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


def test_question_render_emits_raw_markdown_and_changes_no_workspace_file(tmp_path, capsys) -> None:
    layout = make_runtime_workspace(tmp_path)
    prepared = _prepare_paper(layout, "question-render.txt")
    mapping, _ = RecordService(layout).promote(_append_request([_link(prepared)]), actor="agent")
    before_knowledge = {
        path.relative_to(layout.knowledge_root).as_posix(): path.read_bytes()
        for path in layout.knowledge_root.rglob("*")
        if path.is_file()
    }
    before_sources = {
        path.relative_to(root).as_posix(): path.read_bytes()
        for root in layout.source_roots.values()
        for path in root.rglob("*")
        if path.is_file()
    }

    result = main([
        "question", "render", "--workspace", str(layout.config.path),
        "--question-id", mapping["question_id"],
    ])

    captured = capsys.readouterr()
    assert result == 0
    assert captured.err == ""
    assert captured.out.startswith('---\nview_type: "question_reading_view"\n')
    assert "## Canonical Evidence Trace" in captured.out
    assert "## Review Queue Boundaries" in captured.out
    assert str(tmp_path) not in captured.out
    assert "question-render.txt" not in captured.out
    assert {
        path.relative_to(layout.knowledge_root).as_posix(): path.read_bytes()
        for path in layout.knowledge_root.rglob("*")
        if path.is_file()
    } == before_knowledge
    assert {
        path.relative_to(root).as_posix(): path.read_bytes()
        for root in layout.source_roots.values()
        for path in root.rglob("*")
        if path.is_file()
    } == before_sources
    assert not (layout.knowledge_root / "views").exists()


def test_question_render_missing_id_has_empty_stdout(tmp_path, capsys) -> None:
    layout = make_runtime_workspace(tmp_path)
    missing = "question_f0000000-0000-4000-8000-000000000001"

    result = main([
        "question", "render", "--workspace", str(layout.config.path),
        "--question-id", missing,
    ])

    captured = capsys.readouterr()
    assert result == 2
    assert captured.out == ""
    assert json.loads(captured.err)["diagnostic"]["code"] == "RKBC-005"
    assert str(tmp_path) not in captured.err


@pytest.mark.parametrize("command", ["context", "render"])
def test_step7_read_missing_id_has_empty_stdout(tmp_path, capsys, command) -> None:
    layout = make_runtime_workspace(tmp_path)
    missing = "question_f0000000-0000-4000-8000-000000000000"

    result = main([
        "step7",
        command,
        "--workspace",
        str(layout.config.path),
        "--question-id",
        missing,
    ])

    captured = capsys.readouterr()
    assert result == 2
    assert captured.out == ""
    assert json.loads(captured.err)["diagnostic"]["code"] == "RKBC-005"
    assert str(tmp_path) not in captured.err


@pytest.mark.parametrize(
    "argv",
    [
        ["discovery", "list"],
        ["discovery", "show", "--candidate-id", "discovery_a1111111-1111-4111-8111-111111111111"],
        ["discovery", "select", "--request", "missing.json", "--actor", "user"],
        ["guardian", "check"],
        ["paper", "status", "--paper-id", "paper_a1111111-1111-4111-8111-111111111111"],
        ["parse", "show", "--paper-id", "paper_a1111111-1111-4111-8111-111111111111"],
        ["review", "context", "--paper-id", "paper_a1111111-1111-4111-8111-111111111111"],
        [
            "question",
            "render",
            "--question-id",
            "question_a1111111-1111-4111-8111-111111111111",
        ],
        [
            "step7",
            "context",
            "--question-id",
            "question_a1111111-1111-4111-8111-111111111111",
        ],
        [
            "step7",
            "render",
            "--question-id",
            "question_a1111111-1111-4111-8111-111111111111",
        ],
    ],
)
def test_runtime_cli_reports_old_layout_as_upgrade_required(tmp_path, capsys, argv) -> None:
    layout = make_runtime_workspace(tmp_path)
    marker = json.loads(layout.marker_path.read_text(encoding="utf-8"))
    marker["layout_contract_version"] = "m3b-1"
    layout.marker_path.write_bytes(serialize_json(marker))
    (layout.knowledge_root / "discovery").rmdir()

    result = main([argv[0], argv[1], "--workspace", str(layout.config.path), *argv[2:]])

    captured = capsys.readouterr()
    assert result == 4
    assert captured.out == ""
    assert json.loads(captured.err)["diagnostic"]["code"] == "RKBC-027"
    assert str(tmp_path) not in captured.err


@pytest.mark.parametrize(
    "argv",
    [
        ["discovery", "list"],
        ["discovery", "show", "--candidate-id", "discovery_a1111111-1111-4111-8111-111111111111"],
        ["discovery", "select", "--request", "missing.json", "--actor", "user"],
        ["registry", "add", "--root-id", "alpha-sources", "--relative-path", "x.txt", "--metadata", "missing.json"],
        ["parse", "run", "--paper-id", "paper_a1111111-1111-4111-8111-111111111111", "--adapter", "synthetic-text"],
        ["parse", "show", "--paper-id", "paper_a1111111-1111-4111-8111-111111111111"],
        ["paper", "status", "--paper-id", "paper_a1111111-1111-4111-8111-111111111111"],
        ["review", "context", "--paper-id", "paper_a1111111-1111-4111-8111-111111111111"],
        ["record", "promote", "--request", "missing.json", "--actor", "agent"],
        ["compatibility", "inspect", "--adapter", "missing-adapter"],
        ["guardian", "check"],
        ["question", "list"],
        ["question", "show", "--question-id", "question_a1111111-1111-4111-8111-111111111111"],
        ["question", "render", "--question-id", "question_a1111111-1111-4111-8111-111111111111"],
        ["step7", "context", "--question-id", "question_a1111111-1111-4111-8111-111111111111"],
        ["step7", "render", "--question-id", "question_a1111111-1111-4111-8111-111111111111"],
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
