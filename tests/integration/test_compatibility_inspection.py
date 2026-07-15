from __future__ import annotations

import json
from pathlib import Path

import pytest

from research_kb.cli import main
from research_kb.compatibility import CompatibilitySourceRef
from research_kb.services.compatibility import CompatibilityAdapterRegistry, CompatibilityInspectionService
from tests.compatibility_helpers import CleanLegacyAdapter, SyntheticLegacyAdapter, make_compatibility_workspace


@pytest.mark.parametrize("domain", ["alpha", "beta"])
def test_two_synthetic_domains_use_same_read_only_compatibility_service(tmp_path: Path, domain: str) -> None:
    layout = make_compatibility_workspace(tmp_path, domain)
    adapter = SyntheticLegacyAdapter(domain)
    result = CompatibilityInspectionService(
        layout, CompatibilityAdapterRegistry([adapter])
    ).inspect(adapter.adapter_id)
    assert result.report["adapter_id"] == adapter.adapter_id
    assert result.report["source_system"] == "synthetic-legacy"
    assert result.report["protected_inputs_unchanged"] is True
    assert result.report["items"]


def test_compatibility_cli_supports_explicit_in_process_injection(tmp_path: Path, capsys) -> None:
    layout = make_compatibility_workspace(tmp_path)
    adapter = CleanLegacyAdapter()
    result = main(
        [
            "compatibility",
            "inspect",
            "--workspace",
            str(layout.config.path),
            "--adapter",
            adapter.adapter_id,
        ],
        compatibility_adapters=(adapter,),
    )
    output_text = capsys.readouterr().out
    output = json.loads(output_text)
    assert result == 0
    assert output["status"] == "success"
    assert output["adapter_id"] == adapter.adapter_id
    assert str(tmp_path) not in output_text


def test_compatibility_cli_returns_one_for_blocking_report(tmp_path: Path, capsys) -> None:
    layout = make_compatibility_workspace(tmp_path)
    adapter = SyntheticLegacyAdapter()
    result = main(
        ["compatibility", "inspect", "--workspace", str(layout.config.path), "--adapter", adapter.adapter_id],
        compatibility_adapters=(adapter,),
    )
    output = json.loads(capsys.readouterr().out)
    assert result == 1
    assert output["status"] == "blocking_differences"
    assert output["blocking_difference_count"] > 0


def test_generic_cli_has_no_dynamic_adapter_discovery(tmp_path: Path, capsys) -> None:
    layout = make_compatibility_workspace(tmp_path)
    private_adapter_id = "Z:" + "/private/missing-adapter"
    result = main(
        ["compatibility", "inspect", "--workspace", str(layout.config.path), "--adapter", private_adapter_id]
    )
    captured = capsys.readouterr()
    output = json.loads(captured.err)
    assert result == 2
    assert output["diagnostic"]["code"] == "RKBC-024"
    assert private_adapter_id not in captured.err
    assert captured.out == ""


def test_compatibility_cli_returns_four_when_protected_input_changes(tmp_path: Path, capsys) -> None:
    layout = make_compatibility_workspace(tmp_path)

    class MutatingAdapter(CleanLegacyAdapter):
        def iter_inventory(self, context):
            source = context.resolve_source(CompatibilitySourceRef(self.root_id, "legacy.jsonl"))
            source.write_text("synthetic mutation\n", encoding="utf-8", newline="\n")
            yield from ()

    adapter = MutatingAdapter()
    result = main(
        ["compatibility", "inspect", "--workspace", str(layout.config.path), "--adapter", adapter.adapter_id],
        compatibility_adapters=(adapter,),
    )
    captured = capsys.readouterr()
    output = json.loads(captured.err)
    assert result == 4
    assert output["diagnostic"]["code"] == "RKBC-026"
    assert captured.out == ""
    assert str(tmp_path) not in captured.err
