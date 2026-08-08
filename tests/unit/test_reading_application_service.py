from __future__ import annotations

import hashlib
import io
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from shutil import copytree, rmtree

import pytest
import research_kb.source_resolution as source_resolution_module

from research_kb.bundle import load_workspace_entries, records_of_kind
from research_kb.catalog.models import canonical_digest
from research_kb.errors import DUPLICATE_ID, ResearchKBError
from research_kb.parse.synthetic_text import SyntheticTextAdapter
from research_kb.services.parse import ParseService
from research_kb.services.agent_task_application import AgentTaskApplicationService
from research_kb.services.reading_application import ReadingApplicationService
from research_kb.services.registry import RegistryService
from research_kb.services.source_asset import SourceAssetService
from research_kb.services.source_adequacy import SourceAdequacyService
from research_kb.services.workspace_session import WorkspaceSessionService
from research_kb.source_assets import current_source_asset_heads
from research_kb.storage.json_io import read_json_document, read_jsonl, serialize_json, serialize_jsonl
from research_kb.workspace import WorkspaceLayout
from tests.unit.test_agent_task_application_service import (
    APPROVED_CLASSES,
    NOW,
    P4B_POLICY,
    _expected,
    _primary_candidate,
    _build_primary_ready,
    _review_candidate,
    _build_review_ready,
)
from tests.pdf_helpers import write_synthetic_pdf
from tests.runtime_helpers import make_runtime_workspace
from tests.unit.test_source_asset_service import _create_job


def _tree_snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _build_committed_primary(tmp_path: Path):
    layout, session, intake, agent, created, text = _build_primary_ready(tmp_path)
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
    assert submitted["status"] == "success", submitted
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


def _build_committed_review(tmp_path: Path):
    layout, session, intake, agent, created, _ = _build_review_ready(tmp_path)
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


def _build_committed_primary_pdf(tmp_path: Path):
    from research_kb.services.deterministic_intake_application import (
        DeterministicIntakeApplicationService,
    )

    text = "Synthetic intervention reduced the measured signal by 42 percent in the fabricated assay."
    layout = make_runtime_workspace(
        tmp_path,
        local_inbox="./sources/inbox",
        create_local_inbox=True,
        agent_policy=P4B_POLICY,
    )
    session = WorkspaceSessionService({"alpha": layout.config.path}).open("alpha")
    source = write_synthetic_pdf(tmp_path / "p5b-primary.pdf", [text])
    payload = source.read_bytes()
    intake = DeterministicIntakeApplicationService(clock=lambda: NOW).start_upload(
        session,
        io.BytesIO(payload),
        {
            "idempotency_key": "p5b-primary-pdf",
            "requested_operation": "basic_paper_card",
            "document_route": "primary",
            "route_reason": None,
            "bibliography": {
                "title": "Synthetic P5-B Primary Study",
                "authors": ["Fixture Author"],
                "year": 2026,
                "doi": None,
            },
            "expected_sha256": hashlib.sha256(payload).hexdigest(),
            "expected_size_bytes": len(payload),
        },
    )
    assert intake["pipeline"]["current_node"] == "primary_semantic_gate"
    agent = AgentTaskApplicationService(clock=lambda: NOW)
    created = agent.create_from_pipeline(
        session,
        intake["pipeline"]["job_id"],
        {
            "paper_id": intake["paper_id"],
            "task_kind": "primary_semantic_processing",
            "executor_id": "codex_cli",
            "approved_content_classes": APPROVED_CLASSES,
            "idempotency_key": "p5b-primary-task",
        },
    )
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
    if submitted["status"] == "blocked":
        basis = submitted["source_adequacy"]
        SourceAdequacyService(layout).assess(
            paper_id=intake["paper_id"],
            job_id=submitted["pipeline"]["job_id"],
            requested_operation="continuous_text_evidence",
            actor="user",
            basis_profile_id=basis["profile_id"],
            user_decision={
                "decision": "accept_uncertainty",
                "capabilities": ["continuous_text_citation"],
                "reason": "The synthetic single-column PDF is accepted for this bounded test.",
            },
        )
        refreshed = agent.refresh_primary_task(
            session,
            submitted["task"]["task_id"],
            _expected(submitted["task"]),
        )
        successor = refreshed["successor_task"]
        prepared = agent.prepare_handoff(
            session,
            successor["task_id"],
            _expected(successor),
            "codex_cli",
        )
        submitted = agent.submit_result(
            session,
            prepared["task"]["task_id"],
            _expected(prepared["task"]),
            prepared["lease"],
            _primary_candidate(prepared["task"], text),
        )
    assert submitted["status"] == "success", submitted
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


def _restore_workspace(snapshot_root: Path, active_root: Path):
    if active_root.exists():
        rmtree(active_root)
    copytree(snapshot_root, active_root)
    config_path = active_root / "workspace.yaml"
    layout = WorkspaceLayout.load(config_path)
    session = WorkspaceSessionService({"alpha": config_path}).open("alpha")
    return layout, session


@pytest.fixture(scope="module")
def committed_primary_template(tmp_path_factory: pytest.TempPathFactory):
    root = tmp_path_factory.mktemp("reading-primary-template")
    active_parent = root / "active"
    active_parent.mkdir()
    layout, _, intake, _, text, _ = _build_committed_primary(active_parent)
    active_root = layout.config.path.parent
    snapshot_root = root / "snapshot"
    copytree(active_root, snapshot_root)
    return snapshot_root, active_root, deepcopy(intake), text


@pytest.fixture(scope="module")
def committed_primary_pdf_template(tmp_path_factory: pytest.TempPathFactory):
    root = tmp_path_factory.mktemp("reading-primary-pdf-template")
    active_parent = root / "active"
    active_parent.mkdir()
    layout, _, intake, _, text, _ = _build_committed_primary_pdf(active_parent)
    active_root = layout.config.path.parent
    snapshot_root = root / "snapshot"
    copytree(active_root, snapshot_root)
    return snapshot_root, active_root, deepcopy(intake), text


@pytest.fixture(scope="module")
def committed_review_template(tmp_path_factory: pytest.TempPathFactory):
    root = tmp_path_factory.mktemp("reading-review-template")
    active_parent = root / "active"
    active_parent.mkdir()
    layout, _, intake = _build_committed_review(active_parent)
    active_root = layout.config.path.parent
    snapshot_root = root / "snapshot"
    copytree(active_root, snapshot_root)
    return snapshot_root, active_root, deepcopy(intake)


@pytest.fixture
def committed_primary(committed_primary_template):
    snapshot_root, active_root, intake, text = committed_primary_template
    layout, session = _restore_workspace(snapshot_root, active_root)
    cloned_intake = deepcopy(intake)
    bundle = read_json_document(
        layout.primary_bundle_path(cloned_intake["paper_id"]),
        record_kind="primary-semantic-bundle",
    )
    return (
        layout,
        session,
        cloned_intake,
        AgentTaskApplicationService(clock=lambda: NOW),
        text,
        bundle,
    )


@pytest.fixture
def committed_primary_pdf(committed_primary_pdf_template):
    snapshot_root, active_root, intake, text = committed_primary_pdf_template
    layout, session = _restore_workspace(snapshot_root, active_root)
    cloned_intake = deepcopy(intake)
    bundle = read_json_document(
        layout.primary_bundle_path(cloned_intake["paper_id"]),
        record_kind="primary-semantic-bundle",
    )
    return (
        layout,
        session,
        cloned_intake,
        AgentTaskApplicationService(clock=lambda: NOW),
        text,
        bundle,
    )


@pytest.fixture
def committed_review(committed_review_template):
    snapshot_root, active_root, intake = committed_review_template
    layout, session = _restore_workspace(snapshot_root, active_root)
    return layout, session, deepcopy(intake)


@pytest.mark.reading_source
def test_evidence_source_handle_opens_exact_synthetic_pdf_without_writes(
    committed_primary_pdf,
) -> None:
    layout, session, _, _, _, bundle = committed_primary_pdf
    evidence = bundle["revisions"][0]["evidence"][0]
    before_knowledge = _tree_snapshot(layout.knowledge_root)
    before_sources = {
        root_id: _tree_snapshot(root) for root_id, root in layout.source_roots.items()
    }
    service = ReadingApplicationService()

    prepared = service.prepare_evidence_source(session, evidence["evidence_id"])

    assert prepared.descriptor == {
        "status": "success",
        "interface_version": "1.0",
        "application_service_interface_version": "1.22",
        "evidence_id": evidence["evidence_id"],
        "paper_id": evidence["paper_id"],
        "pdf_page": evidence["source_page"]["pdf_page"],
        "locator": evidence["locator"],
        "media_type": "application/pdf",
        "size_bytes": prepared.descriptor["size_bytes"],
        "source_currentness": "current",
        "persistent_writes": 0,
        "canonical_scientific_write": False,
    }
    assert prepared.descriptor["size_bytes"] > 100
    assert "source_ref" not in str(prepared.descriptor)
    assert "fingerprint" not in str(prepared.descriptor)
    assert str(layout.config.base_dir) not in str(prepared.descriptor)

    with service.open_evidence_source(session, prepared.handle) as opened:
        assert opened.stream.read(5) == bytes((37, 80, 68, 70, 45))
        assert opened.size_bytes == prepared.descriptor["size_bytes"]
        assert opened.pdf_page == evidence["source_page"]["pdf_page"]
        assert opened.locator == evidence["locator"]

    assert _tree_snapshot(layout.knowledge_root) == before_knowledge
    assert {
        root_id: _tree_snapshot(root) for root_id, root in layout.source_roots.items()
    } == before_sources


@pytest.mark.reading_source
def test_evidence_source_handle_rechecks_changed_bytes_before_open(committed_primary_pdf) -> None:
    layout, session, _, _, _, bundle = committed_primary_pdf
    evidence_id = bundle["revisions"][0]["evidence"][0]["evidence_id"]
    service = ReadingApplicationService()
    prepared = service.prepare_evidence_source(session, evidence_id)
    source = next(layout.local_inbox.glob("*.pdf"))
    source.write_bytes(source.read_bytes() + b"changed")

    with pytest.raises(ResearchKBError, match="source fingerprint changed"):
        service.open_evidence_source(session, prepared.handle)


@pytest.mark.reading_source
def test_evidence_source_handle_rejects_non_pdf_source(committed_primary) -> None:
    _, session, _, _, _, bundle = committed_primary
    evidence_id = bundle["revisions"][0]["evidence"][0]["evidence_id"]

    with pytest.raises(ResearchKBError, match="PDF"):
        ReadingApplicationService().prepare_evidence_source(session, evidence_id)


@pytest.mark.reading_source
def test_evidence_source_handle_rejects_same_digest_ref_outside_lineage(
    committed_primary_pdf,
) -> None:
    layout, session, _, _, _, bundle = committed_primary_pdf
    evidence_id = bundle["revisions"][0]["evidence"][0]["evidence_id"]
    service = ReadingApplicationService()
    prepared = service.prepare_evidence_source(session, evidence_id)
    source_root = layout.source_roots[prepared.handle.source_root_id]
    source = source_root / prepared.handle.source_relative_path
    forged = source_root / "p5b-forged-copy.pdf"
    forged.write_bytes(source.read_bytes())
    forged_handle = replace(
        prepared.handle,
        source_relative_path=forged.relative_to(source_root).as_posix(),
    )

    with pytest.raises(ResearchKBError, match="provenance lineage"):
        service.open_evidence_source(session, forged_handle)

    wrong_workspace = replace(prepared.handle, workspace_id="workspace-other")
    with pytest.raises(ResearchKBError, match="different workspace"):
        service.open_evidence_source(session, wrong_workspace)


@pytest.mark.reading_source
def test_evidence_source_handle_enforces_size_budget(
    committed_primary_pdf,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, session, _, _, _, bundle = committed_primary_pdf
    evidence_id = bundle["revisions"][0]["evidence"][0]["evidence_id"]
    monkeypatch.setattr(
        "research_kb.services.reading_application.MAX_EVIDENCE_SOURCE_BYTES",
        64,
    )

    with pytest.raises(ResearchKBError, match="size budget"):
        ReadingApplicationService().prepare_evidence_source(session, evidence_id)


@pytest.mark.reading_source
def test_evidence_source_handle_uses_current_same_digest_relink(committed_primary_pdf) -> None:
    layout, session, intake, _, _, bundle = committed_primary_pdf
    evidence_id = bundle["revisions"][0]["evidence"][0]["evidence_id"]
    states = read_jsonl(
        layout.source_assets_path,
        record_kind="source-asset-state",
        id_field="source_asset_state_id",
    )
    head = next(
        item
        for item in current_source_asset_heads(states)
        if item.get("paper_id") == intake["paper_id"]
    )
    original_root = layout.source_roots[head["source_ref"]["root_id"]]
    original = original_root / Path(head["source_ref"]["relative_path"])
    relocated = layout.source_roots["alpha-sources"] / "p5b-relocated.pdf"
    relocated.write_bytes(original.read_bytes())
    job = _create_job(layout, "same_digest_relink")
    relinked = SourceAssetService(layout).relink(
        source_asset_id=head["source_asset_id"],
        job_id=job["job_id"],
        root_id="alpha-sources",
        relative_path=relocated.relative_to(layout.source_roots["alpha-sources"]).as_posix(),
        expected_state_id=head["source_asset_state_id"],
        expected_state_digest=canonical_digest(head),
        actor="cli",
    )
    original.unlink()

    prepared = ReadingApplicationService().prepare_evidence_source(session, evidence_id)

    assert prepared.handle.source_root_id == "alpha-sources"
    assert prepared.handle.source_relative_path == relinked.state["source_ref"]["relative_path"]
    with ReadingApplicationService().open_evidence_source(session, prepared.handle) as opened:
        assert opened.stream.read(5) == bytes((37, 80, 68, 70, 45))


@pytest.mark.reading_source
def test_historical_evidence_source_keeps_its_own_revision_lineage(
    committed_primary_pdf,
) -> None:
    layout, session, intake, agent, text, first_bundle = committed_primary_pdf
    first_revision = first_bundle["revisions"][0]
    first_evidence = first_revision["evidence"][0]
    correction = agent.create_from_pipeline(
        session,
        intake["pipeline"]["job_id"],
        {
            "paper_id": intake["paper_id"],
            "task_kind": "primary_semantic_processing",
            "executor_id": "claude_code_cli",
            "approved_content_classes": APPROVED_CLASSES,
            "idempotency_key": "p5b-primary-correction",
        },
    )
    prepared_correction = agent.prepare_handoff(
        session,
        correction["task"]["task_id"],
        _expected(correction["task"]),
        "claude_code_cli",
    )
    submitted = agent.submit_result(
        session,
        prepared_correction["task"]["task_id"],
        _expected(prepared_correction["task"]),
        prepared_correction["lease"],
        _primary_candidate(prepared_correction["task"], text),
    )
    agent.approve_primary_result(
        session,
        submitted["task"]["task_id"],
        _expected(submitted["task"]),
    )

    prepared = ReadingApplicationService().prepare_evidence_source(
        session,
        first_evidence["evidence_id"],
    )

    assert prepared.handle.revision_id == first_revision["revision_id"]
    assert prepared.descriptor["pdf_page"] == first_evidence["source_page"]["pdf_page"]
    with ReadingApplicationService().open_evidence_source(session, prepared.handle) as opened:
        assert opened.stream.read(5) == bytes((37, 80, 68, 70, 45))


@pytest.mark.reading_source
def test_evidence_source_handle_rejects_changed_evidence_lineage(committed_primary_pdf) -> None:
    layout, session, intake, _, _, bundle = committed_primary_pdf
    evidence = bundle["revisions"][0]["evidence"][0]
    service = ReadingApplicationService()
    prepared = service.prepare_evidence_source(session, evidence["evidence_id"])
    changed = deepcopy(bundle)
    changed["revisions"][0]["evidence"][0]["support_scope"] += " Altered after handle issue."
    layout.primary_bundle_path(intake["paper_id"]).write_bytes(serialize_json(changed))

    with pytest.raises(ResearchKBError, match="lineage changed"):
        service.open_evidence_source(session, prepared.handle)


@pytest.mark.reading_source
def test_evidence_source_handle_rejects_missing_or_unsafe_source(
    committed_primary_pdf,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    layout, session, _, _, _, bundle = committed_primary_pdf
    evidence_id = bundle["revisions"][0]["evidence"][0]["evidence_id"]
    service = ReadingApplicationService()
    prepared = service.prepare_evidence_source(session, evidence_id)
    source = layout.source_roots[prepared.handle.source_root_id] / Path(
        prepared.handle.source_relative_path
    )
    original = source_resolution_module._is_unsafe_link
    monkeypatch.setattr(
        source_resolution_module,
        "_is_unsafe_link",
        lambda path: path == source or original(path),
    )
    with pytest.raises(ResearchKBError, match="unavailable"):
        service.open_evidence_source(session, prepared.handle)

    monkeypatch.setattr(source_resolution_module, "_is_unsafe_link", original)
    source.unlink()
    with pytest.raises(ResearchKBError, match="unavailable"):
        service.open_evidence_source(session, prepared.handle)


@pytest.mark.reading_source
def test_evidence_source_handle_rejects_duplicate_provenance_ownership(
    committed_primary_pdf,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    layout, session, intake, _, _, bundle = committed_primary_pdf
    evidence = bundle["revisions"][0]["evidence"][0]
    layout.evidence_path(intake["paper_id"]).write_bytes(serialize_jsonl([evidence]))
    monkeypatch.setattr(
        "research_kb.services.reading_application.validate_workspace_entries",
        lambda entries: None,
    )

    with pytest.raises(ResearchKBError) as duplicate:
        ReadingApplicationService().prepare_evidence_source(session, evidence["evidence_id"])

    assert duplicate.value.diagnostic.code == DUPLICATE_ID


@pytest.mark.reading_projection
def test_primary_reading_and_evidence_trace_are_complete_current_and_read_only(
    committed_primary,
) -> None:
    layout, session, intake, _, _, bundle = committed_primary
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
    assert str(layout.config.base_dir) not in str(reading)
    assert str(layout.config.base_dir) not in str(trace)
    assert _tree_snapshot(layout.knowledge_root) == before


@pytest.mark.reading_projection
def test_committed_primary_remains_readable_when_source_is_missing(
    committed_primary,
) -> None:
    layout, session, intake, _, _, bundle = committed_primary
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


@pytest.mark.reading_projection
def test_same_digest_relink_preserves_evidence_trace_back(committed_primary) -> None:
    layout, session, intake, _, _, bundle = committed_primary
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


@pytest.mark.reading_projection
def test_changed_asset_head_does_not_hide_behind_an_older_exact_copy(committed_primary) -> None:
    layout, session, intake, _, _, bundle = committed_primary
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


@pytest.mark.reading_projection
def test_unobserved_changed_head_does_not_hide_behind_an_older_exact_copy(
    committed_primary,
) -> None:
    layout, session, intake, _, _, bundle = committed_primary
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


@pytest.mark.reading_projection
def test_source_change_during_projection_disables_trace_back(
    committed_primary,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, session, intake, _, _, _ = committed_primary
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


@pytest.mark.reading_projection
def test_historical_evidence_keeps_its_revision_and_parse_binding(committed_primary) -> None:
    layout, session, intake, agent, text, first_bundle = committed_primary
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


@pytest.mark.reading_projection
def test_review_reading_is_explicitly_background_only(committed_review) -> None:
    layout, session, intake = committed_review
    before = _tree_snapshot(layout.knowledge_root)

    reading = ReadingApplicationService().show_paper(session, intake["paper_id"])

    assert reading["document_route"] == "review"
    assert reading["review"]["review_memory"]["background_only"] is True
    assert reading["review"]["review_memory"]["can_enter_canonical_evidence"] is False
    assert reading["review"]["factual_support_eligible"] is False
    assert reading["persistent_writes"] == 0
    assert _tree_snapshot(layout.knowledge_root) == before


@pytest.mark.reading_projection
def test_compare_is_bounded_unique_ordered_and_semantically_inert(committed_primary) -> None:
    layout, session, intake, _, _, _ = committed_primary
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


@pytest.mark.reading_projection
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
