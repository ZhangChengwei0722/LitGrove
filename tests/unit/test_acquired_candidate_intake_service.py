from __future__ import annotations

import json

import pytest

from research_kb.cli import main
from research_kb.errors import ResearchKBError
from research_kb.services.acquired_candidate_intake import AcquiredCandidateIntakeService
from research_kb.services.registry import RegistryService
from research_kb.storage.json_io import read_jsonl, serialize_jsonl
from tests.unit.test_discovery_acquisition_service import (
    CANDIDATE_ID,
    prepared_service,
)


PAPER_ID = "paper_a1111111-1111-4111-8111-111111111111"
SECOND_PAPER_ID = "paper_a2222222-2222-4222-8222-222222222222"
CHANGED_PDF_BYTES = bytes((37, 80, 68, 70, 45)) + b"changed synthetic bytes"


def acquired_workspace(tmp_path):
    layout, _, _, acquisition = prepared_service(tmp_path)
    acquisition.acquire(CANDIDATE_ID, provider="europe-pmc", actor="user")
    return layout


def test_inspect_acquired_projects_receipt_into_existing_intake_contract(tmp_path) -> None:
    layout = acquired_workspace(tmp_path)
    before_candidate = layout.discovery_candidates_path.read_bytes()
    before_events = layout.process_events_path.read_bytes()

    report = AcquiredCandidateIntakeService(layout).inspect(CANDIDATE_ID)

    assert report == {
        "status": "success",
        "interface_version": "1.0",
        "candidate_id": CANDIDATE_ID,
        "source": {
            "root_id": "alpha-sources",
            "relative_path": f"inbox/{CANDIDATE_ID}.pdf",
            "fingerprint_algorithm": "sha256",
        },
        "registration": {"state": "unregistered", "paper_ids": []},
        "domain_profile": report["domain_profile"],
        "registry_metadata": {
            "bibliography": {
                "title": "Targeted degradation delivery in an invented system",
                "authors": ["Alpha Researcher"],
                "year": 2026,
                "doi": "10.0000/synthetic.discovery",
            },
            "fixture_origin": "synthetic_from_scratch",
        },
        "persistent_writes": 0,
    }
    assert layout.discovery_candidates_path.read_bytes() == before_candidate
    assert layout.process_events_path.read_bytes() == before_events
    assert not layout.registry_path.exists()


def test_inspect_acquired_rejects_candidate_without_receipt(tmp_path) -> None:
    layout, _, _, _ = prepared_service(tmp_path)

    with pytest.raises(ResearchKBError) as error:
        AcquiredCandidateIntakeService(layout).inspect(CANDIDATE_ID)

    assert error.value.diagnostic.code == "RKBC-033"
    assert not layout.registry_path.exists()


def test_inspect_acquired_rejects_changed_source(tmp_path) -> None:
    layout = acquired_workspace(tmp_path)
    source = layout.local_inbox / f"{CANDIDATE_ID}.pdf"
    source.write_bytes(CHANGED_PDF_BYTES)

    with pytest.raises(ResearchKBError) as error:
        AcquiredCandidateIntakeService(layout).inspect(CANDIDATE_ID)

    assert error.value.diagnostic.code == "RKBC-009"
    assert not layout.registry_path.exists()


def test_inspect_acquired_rerun_projects_registered_current(tmp_path) -> None:
    layout = acquired_workspace(tmp_path)
    first = AcquiredCandidateIntakeService(layout).inspect(CANDIDATE_ID)
    record, _ = RegistryService(
        layout,
        id_allocator=lambda namespace: PAPER_ID,
    ).add(
        root_id=first["source"]["root_id"],
        relative_path=first["source"]["relative_path"],
        metadata=first["registry_metadata"],
    )

    second = AcquiredCandidateIntakeService(layout).inspect(CANDIDATE_ID)

    assert record["paper_id"] == PAPER_ID
    assert second["registration"] == {
        "state": "registered_current",
        "paper_ids": [PAPER_ID],
    }
    assert second["persistent_writes"] == 0


def test_inspect_acquired_preserves_registered_stale_stop_state(tmp_path) -> None:
    layout = acquired_workspace(tmp_path)
    first = AcquiredCandidateIntakeService(layout).inspect(CANDIDATE_ID)
    RegistryService(layout, id_allocator=lambda namespace: PAPER_ID).add(
        root_id=first["source"]["root_id"],
        relative_path=first["source"]["relative_path"],
        metadata=first["registry_metadata"],
    )
    registry = read_jsonl(
        layout.registry_path,
        record_kind="registry-paper",
        id_field="paper_id",
    )
    registry[0]["source_fingerprint"]["value"] = "0" * 64
    layout.registry_path.write_bytes(serialize_jsonl(registry))

    report = AcquiredCandidateIntakeService(layout).inspect(CANDIDATE_ID)

    assert report["registration"] == {
        "state": "registered_stale",
        "paper_ids": [PAPER_ID],
    }


def test_inspect_acquired_preserves_ambiguous_stop_state(tmp_path) -> None:
    layout = acquired_workspace(tmp_path)
    first = AcquiredCandidateIntakeService(layout).inspect(CANDIDATE_ID)
    paper_ids = iter((PAPER_ID, SECOND_PAPER_ID))
    registry = RegistryService(layout, id_allocator=lambda namespace: next(paper_ids))
    for _ in range(2):
        registry.add(
            root_id=first["source"]["root_id"],
            relative_path=first["source"]["relative_path"],
            metadata=first["registry_metadata"],
        )

    report = AcquiredCandidateIntakeService(layout).inspect(CANDIDATE_ID)

    assert report["registration"] == {
        "state": "ambiguous",
        "paper_ids": [PAPER_ID, SECOND_PAPER_ID],
    }


def test_inspect_acquired_cli_is_read_only_and_has_empty_stdout_on_failure(
    tmp_path,
    capsys,
) -> None:
    layout = acquired_workspace(tmp_path)
    argv = [
        "intake",
        "inspect-acquired",
        "--workspace",
        str(layout.config.path),
        "--candidate-id",
        CANDIDATE_ID,
    ]

    assert main(argv) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["candidate_id"] == CANDIDATE_ID
    assert report["persistent_writes"] == 0

    source = layout.local_inbox / f"{CANDIDATE_ID}.pdf"
    source.write_bytes(CHANGED_PDF_BYTES)
    assert main(argv) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err)["diagnostic"]["code"] == "RKBC-009"
