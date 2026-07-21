from __future__ import annotations

import json

from research_kb.cli import main
from research_kb.services.discovery_candidate import DiscoveryCandidateService
from tests.discovery_candidate_helpers import selection_request
from tests.pdf_helpers import write_synthetic_pdf
from tests.runtime_helpers import make_runtime_workspace
from tests.unit.test_discovery_acquisition_service import FakeResolver, FakeTransport


def selected_candidate(tmp_path):
    layout = make_runtime_workspace(
        tmp_path,
        local_inbox="./sources/inbox",
        create_local_inbox=True,
    )
    result = DiscoveryCandidateService(layout).select(selection_request(), actor="user")
    return layout, result.selected_candidate_ids[0]


def test_discovery_acquire_cli_creates_only_source_and_receipt(tmp_path, capsys) -> None:
    layout, candidate_id = selected_candidate(tmp_path)
    generated = tmp_path / "generated.pdf"
    write_synthetic_pdf(generated, ["Synthetic acquired PDF text."])
    transport = FakeTransport(generated.read_bytes())
    argv = [
        "discovery",
        "acquire",
        "--workspace",
        str(layout.config.path),
        "--candidate-id",
        candidate_id,
        "--provider",
        "europe-pmc",
        "--actor",
        "user",
    ]

    assert main(
        argv,
        discovery_resolvers=(FakeResolver(),),
        discovery_acquisition_transports=(transport,),
    ) == 0

    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert captured.err == ""
    assert report["result"] == "acquired"
    assert report["persistent_writes"] == 2
    source = layout.source_roots[report["source_ref"]["root_id"]] / report[
        "source_ref"
    ]["relative_path"]
    assert source.read_bytes() == generated.read_bytes()
    assert not layout.registry_path.exists()
    assert not list(
        (layout.knowledge_root / "parse" / "by_paper").glob("*.pages.jsonl")
    )

    assert main(
        argv,
        discovery_resolvers=(FakeResolver(),),
        discovery_acquisition_transports=(transport,),
    ) == 0
    rerun = json.loads(capsys.readouterr().out)
    assert rerun["result"] == "no_change"
    assert rerun["persistent_writes"] == 0


def test_discovery_acquire_cli_non_user_failure_has_empty_stdout(tmp_path, capsys) -> None:
    layout, candidate_id = selected_candidate(tmp_path)

    assert main(
        [
            "discovery",
            "acquire",
            "--workspace",
            str(layout.config.path),
            "--candidate-id",
            candidate_id,
            "--provider",
            "europe-pmc",
            "--actor",
            "agent",
        ],
        discovery_resolvers=(FakeResolver(),),
        discovery_acquisition_transports=(FakeTransport(),),
    ) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err)["diagnostic"]["code"] == "RKBC-006"
    assert not list(layout.local_inbox.iterdir())
