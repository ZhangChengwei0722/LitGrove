import json
from io import BytesIO, TextIOWrapper
from pathlib import Path

from research_kb.cli import _configure_standard_streams, _write_json, main
from tests.fixture_factory import make_bundle


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
