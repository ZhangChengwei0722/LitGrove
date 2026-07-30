from __future__ import annotations

import json
import os
from pathlib import Path

from research_kb.catalog.models import canonical_digest
from research_kb.cli import main
from research_kb.services.pipeline_job import PipelineJobService
from research_kb.services.registry import RegistryService
from research_kb.storage.json_io import read_jsonl
from tests.runtime_helpers import make_runtime_workspace


PDF_BYTES = bytes((37, 80, 68, 70, 45)) + b"1.4\nsynthetic CLI source\n%%EOF\n"


def _paper(layout, name: str, content: bytes = PDF_BYTES) -> str:
    source = layout.source_roots["alpha-sources"] / name
    source.write_bytes(content)
    return RegistryService(layout).add(
        root_id="alpha-sources",
        relative_path=name,
        metadata={"fixture_origin": "synthetic_from_scratch"},
    )[0]["paper_id"]


def _job(layout, *operations: str) -> str:
    return PipelineJobService(layout).create(
        requested_route="local_source",
        requested_depth="registry_only",
        current_node="source_intake",
        input_refs=[],
        authority_snapshot={
            "actor": "user",
            "granted_operations": list(operations),
            "captured_at": "2026-07-30T08:00:00Z",
        },
        idempotency_key="source-cli-" + "-".join(operations),
        actor="user",
        fixture_origin="synthetic_from_scratch",
    ).state["job_id"]


def _request(tmp_path: Path, name: str, payload: dict) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8", newline="\n")
    return path


def test_source_reference_relink_observe_and_list_cli(tmp_path: Path, capsys) -> None:
    layout = make_runtime_workspace(
        tmp_path,
        source_roots=[
            {"root_id": "alpha-sources", "path": "./sources", "read_only_assets": True},
            {"root_id": "alpha-alt", "path": "./alternate", "read_only_assets": True},
        ],
    )
    paper_id = _paper(layout, "paper.pdf")
    alternate = layout.source_roots["alpha-alt"] / "same.pdf"
    alternate.write_bytes(PDF_BYTES)
    job_id = _job(layout, "register_by_reference", "same_digest_relink", "observe_source")
    reference = _request(
        tmp_path,
        "reference.json",
        {
            "job_id": job_id,
            "paper_id": paper_id,
            "asset_role": "main_pdf",
            "root_id": "alpha-sources",
            "relative_path": "paper.pdf",
            "fixture_origin": "synthetic_from_scratch",
        },
    )

    assert main([
        "source", "reference", "--workspace", str(layout.config.path),
        "--request", str(reference), "--actor", "cli",
    ]) == 0
    created_output = json.loads(capsys.readouterr().out)
    states = read_jsonl(layout.source_assets_path, record_kind="source-asset-state")
    created = states[-1]
    relink = _request(
        tmp_path,
        "relink.json",
        {
            "source_asset_id": created["source_asset_id"],
            "job_id": job_id,
            "root_id": "alpha-alt",
            "relative_path": "same.pdf",
            "expected_state_id": created["source_asset_state_id"],
            "expected_state_digest": canonical_digest(created),
        },
    )

    assert main([
        "source", "relink", "--workspace", str(layout.config.path),
        "--request", str(relink), "--actor", "cli",
    ]) == 0
    relink_output = json.loads(capsys.readouterr().out)
    relinked = read_jsonl(layout.source_assets_path, record_kind="source-asset-state")[-1]
    alternate.write_bytes(PDF_BYTES + b"changed")
    observe = _request(
        tmp_path,
        "observe.json",
        {
            "source_asset_id": relinked["source_asset_id"],
            "job_id": job_id,
            "expected_state_id": relinked["source_asset_state_id"],
            "expected_state_digest": canonical_digest(relinked),
        },
    )

    assert main([
        "source", "observe", "--workspace", str(layout.config.path),
        "--request", str(observe), "--actor", "cli",
    ]) == 0
    observe_output = json.loads(capsys.readouterr().out)
    assert main(["source", "list", "--workspace", str(layout.config.path)]) == 0
    listed = json.loads(capsys.readouterr().out)

    for output in (created_output, relink_output, observe_output, listed):
        rendered = json.dumps(output)
        assert str(tmp_path) not in rendered
        assert "source_fingerprint" not in rendered
    assert listed["source_assets"][0]["source_currentness"] == "stale_source"
    assert len(read_jsonl(layout.source_assets_path, record_kind="source-asset-state")) == 3


def test_source_copy_cli_requires_user_and_emits_redacted_projection(tmp_path: Path, capsys) -> None:
    layout = make_runtime_workspace(
        tmp_path,
        local_inbox="./sources/inbox",
        create_local_inbox=True,
    )
    source = tmp_path / "user-selected.pdf"
    source.write_bytes(PDF_BYTES)
    job_id = _job(layout, "copy_into_local_inbox")
    request = _request(
        tmp_path,
        "copy.json",
        {
            "source": str(source),
            "job_id": job_id,
            "paper_id": None,
            "asset_role": "main_pdf",
            "fixture_origin": "synthetic_from_scratch",
        },
    )

    assert main([
        "source", "copy", "--workspace", str(layout.config.path),
        "--request", str(request), "--actor", "cli",
    ]) == 2
    denied = capsys.readouterr()
    assert denied.out == ""
    assert json.loads(denied.err)["diagnostic"]["code"] == "RKBC-006"
    assert not list(layout.local_inbox.iterdir())

    assert main([
        "source", "copy", "--workspace", str(layout.config.path),
        "--request", str(request), "--actor", "user",
    ]) == 0
    copied = json.loads(capsys.readouterr().out)
    assert copied["result"] == "copied"
    assert str(tmp_path) not in json.dumps(copied)
    assert "source_fingerprint" not in copied
    assert len(list(layout.local_inbox.glob("*.pdf"))) == 1


def test_source_scan_and_select_cli_revalidate_transient_candidate(tmp_path: Path, capsys) -> None:
    layout = make_runtime_workspace(
        tmp_path,
        local_inbox="./sources/inbox",
        create_local_inbox=True,
    )
    candidate_path = layout.local_inbox / "manual.pdf"
    candidate_path.write_bytes(PDF_BYTES)
    old = 1.0
    os.utime(candidate_path, (old, old))

    assert main([
        "source", "scan", "--workspace", str(layout.config.path),
        "--min-stable-age-seconds", "0",
    ]) == 0
    scanned = json.loads(capsys.readouterr().out)
    candidate = scanned["candidates"][0]
    job_id = _job(
        layout,
        "select_inbox_candidate",
        "register_by_reference",
        "associate_source_asset",
    )
    select = _request(
        tmp_path,
        "select.json",
        {
            "candidate_token": candidate["candidate_token"],
            "job_id": job_id,
            "paper_id": None,
            "asset_role": "supplement",
            "min_stable_age_seconds": 0,
        },
    )

    assert main([
        "source", "select", "--workspace", str(layout.config.path),
        "--request", str(select), "--actor", "cli",
    ]) == 0
    selected = json.loads(capsys.readouterr().out)

    assert selected["result"] == "selected"
    assert selected["source_ref"] == candidate["source_ref"]
    assert str(tmp_path) not in json.dumps(scanned)
    assert "source_fingerprint" not in json.dumps(scanned)

    created = read_jsonl(layout.source_assets_path, record_kind="source-asset-state")[-1]
    paper, _ = RegistryService(layout).add(
        root_id=candidate["source_ref"]["root_id"],
        relative_path=candidate["source_ref"]["relative_path"],
        metadata={"fixture_origin": "synthetic_from_scratch"},
    )
    associate = _request(
        tmp_path,
        "associate.json",
        {
            "source_asset_id": created["source_asset_id"],
            "job_id": job_id,
            "paper_id": paper["paper_id"],
            "expected_state_id": created["source_asset_state_id"],
            "expected_state_digest": canonical_digest(created),
        },
    )

    assert main([
        "source", "associate", "--workspace", str(layout.config.path),
        "--request", str(associate), "--actor", "cli",
    ]) == 0
    associated = json.loads(capsys.readouterr().out)
    assert associated["paper_id"] == paper["paper_id"]
    assert str(tmp_path) not in json.dumps(associated)
    assert "source_fingerprint" not in json.dumps(associated)

    assert main([
        "source", "select", "--workspace", str(layout.config.path),
        "--request", str(select), "--actor", "cli",
    ]) == 0
    replayed = json.loads(capsys.readouterr().out)
    assert replayed["persistent_writes"] == 0
    assert replayed["source_asset_state_id"] == associated["source_asset_state_id"]


def test_identity_correct_and_list_cli_require_user_authority(tmp_path: Path, capsys) -> None:
    layout = make_runtime_workspace(tmp_path)
    first = _paper(layout, "first.pdf", PDF_BYTES + b"first")
    second = _paper(layout, "second.pdf", PDF_BYTES + b"second")
    job_id = _job(layout, "registry_identity_correction")
    request = _request(
        tmp_path,
        "identity.json",
        {
            "job_id": job_id,
            "operation": "confirmed_duplicate_merge",
            "subject_paper_ids": [second, first],
            "retained_paper_id": first,
            "supersedes_correction_id": None,
            "rationale": "Synthetic CLI identity decision.",
            "expected_previous_correction_id": None,
            "expected_previous_correction_digest": None,
            "fixture_origin": "synthetic_from_scratch",
        },
    )

    assert main([
        "identity", "correct", "--workspace", str(layout.config.path),
        "--request", str(request), "--actor", "agent",
    ]) == 2
    denied = capsys.readouterr()
    assert denied.out == ""
    assert json.loads(denied.err)["diagnostic"]["code"] == "RKBC-006"

    assert main([
        "identity", "correct", "--workspace", str(layout.config.path),
        "--request", str(request), "--actor", "user",
    ]) == 0
    corrected = json.loads(capsys.readouterr().out)
    assert corrected["result"] == "updated"
    assert main(["identity", "list", "--workspace", str(layout.config.path)]) == 0
    listed = json.loads(capsys.readouterr().out)

    redirected = next(item for item in listed["items"] if item["paper_id"] == second)
    assert redirected["canonical_paper_id"] == first
    assert str(tmp_path) not in json.dumps(listed)
    assert len(read_jsonl(layout.registry_path, record_kind="registry-paper")) == 2


def test_source_cli_rejects_non_string_ids_and_root_ids_with_json_diagnostics(
    tmp_path: Path,
    capsys,
) -> None:
    layout = make_runtime_workspace(tmp_path)
    malformed_job = _request(
        tmp_path,
        "malformed-job.json",
        {
            "job_id": [],
            "paper_id": None,
            "asset_role": "main_pdf",
            "root_id": "alpha-sources",
            "relative_path": "paper.pdf",
        },
    )

    assert main([
        "source", "reference", "--workspace", str(layout.config.path),
        "--request", str(malformed_job), "--actor", "cli",
    ]) == 2
    rejected_job = capsys.readouterr()
    assert rejected_job.out == ""
    assert json.loads(rejected_job.err)["diagnostic"]["code"] == "RKBC-002"

    malformed_root = _request(
        tmp_path,
        "malformed-root.json",
        {
            "job_id": _job(layout, "register_by_reference"),
            "paper_id": None,
            "asset_role": "main_pdf",
            "root_id": [],
            "relative_path": "paper.pdf",
        },
    )
    assert main([
        "source", "reference", "--workspace", str(layout.config.path),
        "--request", str(malformed_root), "--actor", "cli",
    ]) == 2
    rejected_root = capsys.readouterr()
    assert rejected_root.out == ""
    assert json.loads(rejected_root.err)["diagnostic"]["code"] == "RKBC-007"
