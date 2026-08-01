from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from research_kb.bundle import load_workspace_entries, records_of_kind
from research_kb.catalog.models import canonical_digest
from research_kb.errors import ResearchKBError
from research_kb.parse.synthetic_text import SyntheticTextAdapter
from research_kb.services.parse import ParseService
from research_kb.services.reading_application import ReadingApplicationService
from research_kb.services.registry import RegistryService
from research_kb.services.source_asset import SourceAssetService
from research_kb.storage.json_io import read_json_document
from tests.unit.test_agent_task_application_service import (
    APPROVED_CLASSES,
    _expected,
    _primary_candidate,
    _primary_ready,
    _review_candidate,
    _review_ready,
)
from tests.unit.test_source_asset_service import _create_job


def _tree_snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _commit_primary(tmp_path: Path):
    layout, session, intake, agent, created, text = _primary_ready(tmp_path)
    prepared = agent.prepare_handoff(
        session,
        created["task"]["task_id"],
        _expected(created["task"]),
        "codex_cli",
    )
    submitted = agent.submit_result(
        session,
        prepared["task"]["task_id"],
        _expected(prepared["task"]),
        prepared["lease"],
        _primary_candidate(prepared["task"], text),
    )
    agent.approve_primary_result(
        session,
        submitted["task"]["task_id"],
        _expected(submitted["task"]),
    )
    bundle = read_json_document(
        layout.primary_bundle_path(intake["paper_id"]),
        record_kind="primary-semantic-bundle",
    )
    return layout, session, intake, agent, text, bundle


def _commit_review(tmp_path: Path):
    layout, session, intake, agent, created, _ = _review_ready(tmp_path)
    prepared = agent.prepare_handoff(
        session,
        created["task"]["task_id"],
        _expected(created["task"]),
        "codex_cli",
    )
    submitted = agent.submit_result(
        session,
        prepared["task"]["task_id"],
        _expected(prepared["task"]),
        prepared["lease"],
        _review_candidate(prepared["task"]),
    )
    agent.approve_review_result(
        session,
        submitted["task"]["task_id"],
        _expected(submitted["task"]),
    )
    return layout, session, intake


def test_primary_reading_and_evidence_trace_are_complete_current_and_read_only(
    tmp_path: Path,
) -> None:
    layout, session, intake, _, _, bundle = _commit_primary(tmp_path)
    before = _tree_snapshot(layout.knowledge_root)
    service = ReadingApplicationService()

    reading = service.show_paper(session, intake["paper_id"])
    evidence = bundle["revisions"][0]["evidence"][0]
    trace = service.trace_evidence(session, evidence["evidence_id"])

    assert reading["document_route"] == "primary"
    assert len(reading["primary"]["paper_card"]["sections"]) == 7
    assert reading["primary"]["revision_status"] == "active"
    assert reading["source"]["source_currentness"] == "current"
    assert reading["parse"]["binding_state"] == "current"
    assert reading["persistent_writes"] == 0
    unit_states = {
        item["grounding_status"]: item["factual_support_eligible"]
        for item in reading["primary"]["unit_admissibility"]
    }
    assert unit_states == {"grounded": True, "needs_resolution": False}
    assert trace["evidence"]["evidence_id"] == evidence["evidence_id"]
    assert trace["evidence"]["quote"] == evidence["quote"]
    assert "source_fingerprint" not in trace["evidence"]
    assert trace["primary_revision"]["revision_status"] == "active"
    assert trace["source"]["trace_back_available"] is True
    assert trace["parse"]["binding_state"] == "current"
    assert trace["factual_support_eligible"] is True
    assert "source_ref" not in str(reading)
    assert "source_fingerprint" not in str(reading)
    assert str(tmp_path) not in str(reading)
    assert str(tmp_path) not in str(trace)
    assert _tree_snapshot(layout.knowledge_root) == before


def test_committed_primary_remains_readable_when_source_is_missing(
    tmp_path: Path,
) -> None:
    layout, session, intake, _, _, bundle = _commit_primary(tmp_path)
    source = layout.source_roots["alpha-sources"] / "primary-semantic.txt"
    source.unlink()
    service = ReadingApplicationService()

    reading = service.show_paper(session, intake["paper_id"])
    trace = service.trace_evidence(
        session,
        bundle["revisions"][0]["evidence"][0]["evidence_id"],
    )

    assert reading["primary"]["paper_card"] is not None
    assert reading["source"]["source_availability"] == "missing"
    assert reading["source"]["trace_back_available"] is False
    assert trace["source"]["trace_back_available"] is False
    assert trace["factual_support_eligible"] is False


def test_same_digest_relink_preserves_evidence_trace_back(tmp_path: Path) -> None:
    layout, session, intake, _, _, bundle = _commit_primary(tmp_path)
    original = layout.source_roots["alpha-sources"] / "primary-semantic.txt"
    relocated = layout.source_roots["alpha-sources"] / "primary-relocated.txt"
    relocated.write_bytes(original.read_bytes())
    job = _create_job(layout, "register_by_reference", "same_digest_relink")
    source_service = SourceAssetService(layout)
    created = source_service.register_reference(
        job_id=job["job_id"],
        paper_id=intake["paper_id"],
        asset_role="main_pdf",
        root_id="alpha-sources",
        relative_path=original.name,
        actor="cli",
        fixture_origin="synthetic_from_scratch",
    )
    source_service.relink(
        source_asset_id=created.state["source_asset_id"],
        job_id=job["job_id"],
        root_id="alpha-sources",
        relative_path=relocated.name,
        expected_state_id=created.state["source_asset_state_id"],
        expected_state_digest=canonical_digest(created.state),
        actor="cli",
    )
    original.unlink()

    trace = ReadingApplicationService().trace_evidence(
        session,
        bundle["revisions"][0]["evidence"][0]["evidence_id"],
    )

    assert trace["source"] == {
        "source_availability": "available",
        "source_currentness": "current",
        "trace_back_available": True,
    }


def test_changed_asset_head_does_not_hide_behind_an_older_exact_copy(tmp_path: Path) -> None:
    layout, session, intake, _, _, bundle = _commit_primary(tmp_path)
    original = layout.source_roots["alpha-sources"] / "primary-semantic.txt"
    relocated = layout.source_roots["alpha-sources"] / "primary-relocated.txt"
    relocated.write_bytes(original.read_bytes())
    job = _create_job(
        layout,
        "register_by_reference",
        "same_digest_relink",
        "observe_source",
    )
    source_service = SourceAssetService(layout)
    created = source_service.register_reference(
        job_id=job["job_id"],
        paper_id=intake["paper_id"],
        asset_role="main_pdf",
        root_id="alpha-sources",
        relative_path=original.name,
        actor="cli",
        fixture_origin="synthetic_from_scratch",
    )
    relinked = source_service.relink(
        source_asset_id=created.state["source_asset_id"],
        job_id=job["job_id"],
        root_id="alpha-sources",
        relative_path=relocated.name,
        expected_state_id=created.state["source_asset_state_id"],
        expected_state_digest=canonical_digest(created.state),
        actor="cli",
    )
    relocated.write_text("Changed synthetic source.", encoding="utf-8", newline="\n")
    source_service.observe(
        source_asset_id=created.state["source_asset_id"],
        job_id=job["job_id"],
        expected_state_id=relinked.state["source_asset_state_id"],
        expected_state_digest=canonical_digest(relinked.state),
        actor="cli",
    )

    trace = ReadingApplicationService().trace_evidence(
        session,
        bundle["revisions"][0]["evidence"][0]["evidence_id"],
    )

    assert trace["source"] == {
        "source_availability": "available",
        "source_currentness": "stale_source",
        "trace_back_available": True,
    }
    assert trace["factual_support_eligible"] is False


def test_unobserved_changed_head_does_not_hide_behind_an_older_exact_copy(tmp_path: Path) -> None:
    layout, session, intake, _, _, bundle = _commit_primary(tmp_path)
    original = layout.source_roots["alpha-sources"] / "primary-semantic.txt"
    relocated = layout.source_roots["alpha-sources"] / "primary-relocated.txt"
    relocated.write_bytes(original.read_bytes())
    job = _create_job(layout, "register_by_reference", "same_digest_relink")
    source_service = SourceAssetService(layout)
    created = source_service.register_reference(
        job_id=job["job_id"],
        paper_id=intake["paper_id"],
        asset_role="main_pdf",
        root_id="alpha-sources",
        relative_path=original.name,
        actor="cli",
        fixture_origin="synthetic_from_scratch",
    )
    source_service.relink(
        source_asset_id=created.state["source_asset_id"],
        job_id=job["job_id"],
        root_id="alpha-sources",
        relative_path=relocated.name,
        expected_state_id=created.state["source_asset_state_id"],
        expected_state_digest=canonical_digest(created.state),
        actor="cli",
    )
    relocated.write_text("Changed synthetic source.", encoding="utf-8", newline="\n")

    trace = ReadingApplicationService().trace_evidence(
        session,
        bundle["revisions"][0]["evidence"][0]["evidence_id"],
    )

    assert trace["source"] == {
        "source_availability": "available",
        "source_currentness": "stale_source",
        "trace_back_available": True,
    }
    assert trace["factual_support_eligible"] is False


def test_source_change_during_projection_disables_trace_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, session, intake, _, _, _ = _commit_primary(tmp_path)
    import research_kb.services.reading_application as module

    original = module.inspect_source_ref
    calls = 0

    def changing_source(*args, **kwargs):
        nonlocal calls
        calls += 1
        observation = original(*args, **kwargs)
        return observation if calls % 2 else replace(observation, live_sha256="f" * 64)

    monkeypatch.setattr(module, "inspect_source_ref", changing_source)

    reading = ReadingApplicationService().show_paper(session, intake["paper_id"])

    assert reading["source"] == {
        "source_availability": "inaccessible",
        "source_currentness": "changed_during_read",
        "trace_back_available": False,
    }


def test_historical_evidence_keeps_its_revision_and_parse_binding(tmp_path: Path) -> None:
    layout, session, intake, agent, text, first_bundle = _commit_primary(tmp_path)
    first_revision = first_bundle["revisions"][0]
    first_evidence_id = first_revision["evidence"][0]["evidence_id"]
    correction = agent.create_from_pipeline(
        session,
        intake["pipeline"]["job_id"],
        {
            "paper_id": intake["paper_id"],
            "task_kind": "primary_semantic_processing",
            "executor_id": "claude_code_cli",
            "approved_content_classes": APPROVED_CLASSES,
            "idempotency_key": "p5a-correction",
        },
    )
    prepared = agent.prepare_handoff(
        session,
        correction["task"]["task_id"],
        _expected(correction["task"]),
        "claude_code_cli",
    )
    submitted = agent.submit_result(
        session,
        prepared["task"]["task_id"],
        _expected(prepared["task"]),
        prepared["lease"],
        _primary_candidate(prepared["task"], text),
    )
    agent.approve_primary_result(
        session,
        submitted["task"]["task_id"],
        _expected(submitted["task"]),
    )
    ParseService(layout).run(
        paper_id=intake["paper_id"],
        adapter=SyntheticTextAdapter(),
    )

    trace = ReadingApplicationService().trace_evidence(session, first_evidence_id)

    assert trace["primary_revision"]["revision_id"] == first_revision["revision_id"]
    assert trace["primary_revision"]["revision_status"] == "historical"
    assert trace["parse"]["bound_parse_run_id"] == first_revision["input_snapshot"]["parse_run_id"]
    assert trace["parse"]["binding_state"] == "historical_not_materialized"
    assert trace["factual_support_eligible"] is False


def test_review_reading_is_explicitly_background_only(tmp_path: Path) -> None:
    layout, session, intake = _commit_review(tmp_path)
    before = _tree_snapshot(layout.knowledge_root)

    reading = ReadingApplicationService().show_paper(session, intake["paper_id"])

    assert reading["document_route"] == "review"
    assert reading["review"]["review_memory"]["background_only"] is True
    assert reading["review"]["review_memory"]["can_enter_canonical_evidence"] is False
    assert reading["review"]["factual_support_eligible"] is False
    assert reading["persistent_writes"] == 0
    assert _tree_snapshot(layout.knowledge_root) == before


def test_compare_is_bounded_unique_ordered_and_semantically_inert(tmp_path: Path) -> None:
    layout, session, intake, _, _, _ = _commit_primary(tmp_path)
    source = layout.source_roots["alpha-sources"] / "second.txt"
    source.write_text("Second synthetic paper.", encoding="utf-8", newline="\n")
    second, _ = RegistryService(layout).add(
        root_id="alpha-sources",
        relative_path=source.name,
        metadata={
            "bibliography": {"title": "Second synthetic paper"},
            "fixture_origin": "synthetic_from_scratch",
        },
    )
    service = ReadingApplicationService()

    compared = service.compare_papers(session, [second["paper_id"], intake["paper_id"]])

    assert [item["paper"]["paper_id"] for item in compared["papers"]] == [
        second["paper_id"],
        intake["paper_id"],
    ]
    assert compared["semantic_comparison"] is None
    assert compared["persistent_writes"] == 0
    with pytest.raises(ResearchKBError, match="unique"):
        service.compare_papers(session, [second["paper_id"], second["paper_id"]])
    with pytest.raises(ResearchKBError, match="two to four"):
        service.compare_papers(session, [second["paper_id"]])


def test_legacy_card_is_readable_without_primary_bundle(tmp_path: Path) -> None:
    from tests.unit.test_paper_context_service import _grounded_context, _promote_card

    layout, _, paper, evidence, queue = _grounded_context(tmp_path)
    card = _promote_card(
        layout,
        paper["paper_id"],
        evidence[0]["evidence_id"],
        queue[0]["queue_id"],
    )
    from research_kb.services.workspace_session import WorkspaceSessionService

    session = WorkspaceSessionService({"alpha": layout.config.path}).open("alpha")

    reading = ReadingApplicationService().show_paper(session, paper["paper_id"])

    assert reading["document_route"] == "primary"
    assert reading["primary"]["authority_mode"] == "legacy_unversioned"
    assert reading["primary"]["paper_card"] == card
    assert len(records_of_kind(load_workspace_entries(layout), "paper-card")) == 1
