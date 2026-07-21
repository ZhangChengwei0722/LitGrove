from __future__ import annotations

import json

from research_kb.cli import main
from research_kb.discovery.resolution import ProviderAssetRef, ProviderResolution
from tests.discovery_candidate_helpers import selection_request
from tests.runtime_helpers import make_runtime_workspace


class FakeResolver:
    resolver_id = "europe-pmc"
    network_required = True

    def resolve(self, candidate):
        return ProviderResolution(
            provider="europe-pmc",
            provider_api_version="synthetic-6.9",
            lookup_identity={"kind": "doi", "doi": candidate["doi"]},
            resolution_status="auto_acquisition_eligible",
            provider_asset_ref=ProviderAssetRef(
                provider="europe-pmc",
                source="MED",
                record_id="SYNTH-DISCOVERY-1",
                pmcid="PMC1234567",
                asset_kind="pdf",
                route="europe-pmc-pdf-v1",
            ),
            access_basis="repository_open_access",
            license_observation="provider_oa_policy_no_license_text",
            manual_reason=None,
        )


def tree_bytes(root):
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def selected_candidate(tmp_path, capsys):
    layout = make_runtime_workspace(tmp_path)
    request_path = tmp_path / "selection.json"
    request_path.write_text(
        json.dumps(selection_request()),
        encoding="utf-8",
        newline="\n",
    )
    assert main(
        [
            "discovery",
            "select",
            "--workspace",
            str(layout.config.path),
            "--request",
            str(request_path),
            "--actor",
            "user",
        ]
    ) == 0
    candidate_id = json.loads(capsys.readouterr().out)["selected_candidate_ids"][0]
    return layout, candidate_id


def test_discovery_resolve_cli_is_deterministic_and_zero_write(tmp_path, capsys) -> None:
    layout, candidate_id = selected_candidate(tmp_path, capsys)
    before = tree_bytes(layout.config.path.parent)
    argv = [
        "discovery",
        "resolve",
        "--workspace",
        str(layout.config.path),
        "--candidate-id",
        candidate_id,
        "--provider",
        "europe-pmc",
    ]

    assert main(argv, discovery_resolvers=(FakeResolver(),)) == 0
    first = capsys.readouterr()
    assert main(argv, discovery_resolvers=(FakeResolver(),)) == 0
    second = capsys.readouterr()

    report = json.loads(first.out)
    assert first.err == second.err == ""
    assert first.out == second.out
    assert report["resolution_status"] == "auto_acquisition_eligible"
    assert report["persistent_writes"] == 0
    assert "url" not in first.out.casefold()
    assert tree_bytes(layout.config.path.parent) == before

def test_discovery_resolve_cli_failure_has_empty_stdout(tmp_path, capsys) -> None:
    layout, candidate_id = selected_candidate(tmp_path, capsys)
    before = tree_bytes(layout.config.path.parent)

    assert main(
        [
            "discovery",
            "resolve",
            "--workspace",
            str(layout.config.path),
            "--candidate-id",
            candidate_id,
            "--provider",
            "europe-pmc",
        ],
        discovery_resolvers=(),
    ) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err)["diagnostic"]["code"] == "RKBC-032"
    assert tree_bytes(layout.config.path.parent) == before
