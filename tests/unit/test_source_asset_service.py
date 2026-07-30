from __future__ import annotations

from pathlib import Path

import pytest

import research_kb.source_resolution as source_resolution_module
from research_kb.catalog.models import canonical_digest
from research_kb.errors import ResearchKBError
from research_kb.guardian import GuardianService
from research_kb.services.pipeline_job import PipelineJobService
from research_kb.parse.synthetic_text import SyntheticTextAdapter
from research_kb.services.paper_status import PaperStatusService
from research_kb.services.parse import ParseService
from research_kb.services.parse_read import ParseReadService
from research_kb.services.registry import RegistryService
from research_kb.services.source_asset import SourceAssetService
from research_kb.source_assets import (
    current_source_asset_heads,
    source_asset_chain_diagnostics,
    source_asset_projection,
)
from research_kb.storage.json_io import file_sha256, read_jsonl, serialize_jsonl
from research_kb.storage.transactions import TransactionManager
from tests.runtime_helpers import make_runtime_workspace


def _create_job(layout, *operations: str) -> dict:
    return PipelineJobService(layout).create(
        requested_route="local_source",
        requested_depth="registry_only",
        current_node="source_intake",
        input_refs=[],
        authority_snapshot={
            "actor": "user",
            "granted_operations": list(operations),
            "captured_at": "2026-07-30T05:00:00Z",
        },
        idempotency_key="source-asset-test-" + "-".join(operations),
        actor="user",
        fixture_origin="synthetic_from_scratch",
    ).state


def _workspace_with_two_roots(tmp_path: Path):
    layout = make_runtime_workspace(
        tmp_path,
        source_roots=[
            {"root_id": "alpha-sources", "path": "./sources", "read_only_assets": True},
            {"root_id": "alpha-alt", "path": "./alternate", "read_only_assets": True},
        ],
    )
    return layout


def test_reference_source_asset_and_same_digest_relink_preserve_manifestation(tmp_path: Path) -> None:
    layout = _workspace_with_two_roots(tmp_path)
    first = layout.source_roots["alpha-sources"] / "paper.pdf"
    second = layout.source_roots["alpha-alt"] / "paper-copy.pdf"
    content = bytes((37, 80, 68, 70, 45)) + b"1.4\nsynthetic source asset\n%%EOF\n"
    first.write_bytes(content)
    second.write_bytes(content)
    paper, _ = RegistryService(layout).add(
        root_id="alpha-sources",
        relative_path="paper.pdf",
        metadata={"fixture_origin": "synthetic_from_scratch"},
    )
    job = _create_job(layout, "register_by_reference", "same_digest_relink")
    service = SourceAssetService(layout)

    created = service.register_reference(
        job_id=job["job_id"],
        paper_id=paper["paper_id"],
        asset_role="main_pdf",
        root_id="alpha-sources",
        relative_path="paper.pdf",
        actor="cli",
        fixture_origin="synthetic_from_scratch",
    )
    relinked = service.relink(
        source_asset_id=created.state["source_asset_id"],
        job_id=job["job_id"],
        root_id="alpha-alt",
        relative_path="paper-copy.pdf",
        expected_state_id=created.state["source_asset_state_id"],
        expected_state_digest=canonical_digest(created.state),
        actor="cli",
    )

    assert relinked.state["revision"] == 2
    assert relinked.state["manifestation_id"] == created.state["manifestation_id"]
    assert relinked.state["source_ref"] == {
        "root_id": "alpha-alt",
        "relative_path": "paper-copy.pdf",
    }
    assert relinked.state["manifestation_status"] == "active"
    states = read_jsonl(
        layout.source_assets_path,
        record_kind="source-asset-state",
        id_field="source_asset_state_id",
    )
    assert current_source_asset_heads(states) == (relinked.state,)
    assert file_sha256(first) == file_sha256(second)


def test_changed_bytes_create_candidate_and_make_active_projection_stale(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    source = layout.source_roots["alpha-sources"] / "paper.pdf"
    source.write_bytes(bytes((37, 80, 68, 70, 45)) + b"1.4\nfirst synthetic body\n%%EOF\n")
    paper, _ = RegistryService(layout).add(
        root_id="alpha-sources",
        relative_path="paper.pdf",
        metadata={"fixture_origin": "synthetic_from_scratch"},
    )
    job = _create_job(layout, "register_by_reference", "observe_source")
    service = SourceAssetService(layout)
    created = service.register_reference(
        job_id=job["job_id"],
        paper_id=paper["paper_id"],
        asset_role="main_pdf",
        root_id="alpha-sources",
        relative_path="paper.pdf",
        actor="cli",
        fixture_origin="synthetic_from_scratch",
    )
    source.write_bytes(bytes((37, 80, 68, 70, 45)) + b"1.4\nchanged synthetic body\n%%EOF\n")

    observed = service.observe(
        source_asset_id=created.state["source_asset_id"],
        job_id=job["job_id"],
        expected_state_id=created.state["source_asset_state_id"],
        expected_state_digest=canonical_digest(created.state),
        actor="cli",
    )

    assert observed.state["manifestation_status"] == "change_candidate"
    assert observed.state["manifestation_id"] != created.state["manifestation_id"]
    projection = source_asset_projection(
        read_jsonl(layout.source_assets_path, record_kind="source-asset-state")
    )[0]
    assert projection["source_currentness"] == "stale_source"
    assert projection["active_state_id"] == created.state["source_asset_state_id"]
    assert projection["observed_state_id"] == observed.state["source_asset_state_id"]
    findings = GuardianService(layout).check().report["findings"]
    assert any("stale_source" in item["message"] for item in findings)

    source.unlink()
    missing = service.observe(
        source_asset_id=created.state["source_asset_id"],
        job_id=job["job_id"],
        expected_state_id=observed.state["source_asset_state_id"],
        expected_state_digest=canonical_digest(observed.state),
        actor="cli",
    )
    assert missing.state["availability"] == "missing"
    assert missing.state["manifestation_status"] == "active"
    assert missing.state["manifestation_id"] == created.state["manifestation_id"]
    assert source_asset_projection(
        read_jsonl(layout.source_assets_path, record_kind="source-asset-state")
    )[0]["source_currentness"] == "unavailable"


def test_missing_source_preserves_active_manifestation_and_reports_unavailable(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    source = layout.source_roots["alpha-sources"] / "paper.pdf"
    source.write_bytes(bytes((37, 80, 68, 70, 45)) + b"1.4\nmissing-source fixture\n%%EOF\n")
    paper, _ = RegistryService(layout).add(
        root_id="alpha-sources",
        relative_path="paper.pdf",
        metadata={"fixture_origin": "synthetic_from_scratch"},
    )
    job = _create_job(layout, "register_by_reference", "observe_source")
    service = SourceAssetService(layout)
    created = service.register_reference(
        job_id=job["job_id"],
        paper_id=paper["paper_id"],
        asset_role="main_pdf",
        root_id="alpha-sources",
        relative_path="paper.pdf",
        actor="cli",
        fixture_origin="synthetic_from_scratch",
    )
    source.unlink()

    missing = service.observe(
        source_asset_id=created.state["source_asset_id"],
        job_id=job["job_id"],
        expected_state_id=created.state["source_asset_state_id"],
        expected_state_digest=canonical_digest(created.state),
        actor="cli",
    )

    assert missing.state["availability"] == "missing"
    assert missing.state["manifestation_id"] == created.state["manifestation_id"]
    projection = source_asset_projection(
        read_jsonl(layout.source_assets_path, record_kind="source-asset-state")
    )[0]
    assert projection["source_availability"] == "missing"
    assert projection["source_currentness"] == "unavailable"
    findings = GuardianService(layout).check().report["findings"]
    assert any("missing" in item["message"] for item in findings)


def test_source_asset_requires_job_authority_and_rejects_second_active_main(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    first = layout.source_roots["alpha-sources"] / "first.pdf"
    second = layout.source_roots["alpha-sources"] / "second.pdf"
    first.write_bytes(bytes((37, 80, 68, 70, 45)) + b"1.4\nfirst\n%%EOF\n")
    second.write_bytes(bytes((37, 80, 68, 70, 45)) + b"1.4\nsecond\n%%EOF\n")
    paper, _ = RegistryService(layout).add(
        root_id="alpha-sources",
        relative_path="first.pdf",
        metadata={"fixture_origin": "synthetic_from_scratch"},
    )
    denied = _create_job(layout, "observe_source")
    service = SourceAssetService(layout)

    with pytest.raises(ResearchKBError) as authority:
        service.register_reference(
            job_id=denied["job_id"],
            paper_id=paper["paper_id"],
            asset_role="main_pdf",
            root_id="alpha-sources",
            relative_path="first.pdf",
            actor="cli",
        )
    assert authority.value.diagnostic.code == "RKBC-006"

    allowed = _create_job(layout, "register_by_reference")
    service.register_reference(
        job_id=allowed["job_id"],
        paper_id=paper["paper_id"],
        asset_role="main_pdf",
        root_id="alpha-sources",
        relative_path="first.pdf",
        actor="cli",
    )
    with pytest.raises(ResearchKBError) as duplicate:
        service.register_reference(
            job_id=allowed["job_id"],
            paper_id=paper["paper_id"],
            asset_role="main_pdf",
            root_id="alpha-sources",
            relative_path="second.pdf",
            actor="cli",
        )
    assert duplicate.value.diagnostic.code in {"RKBC-004", "RKBC-009", "RKBC-017"}


def test_unassociated_source_blocks_successful_job_completion_until_associated(
    tmp_path: Path,
) -> None:
    layout = make_runtime_workspace(tmp_path)
    source = layout.source_roots["alpha-sources"] / "unassociated.txt"
    source.write_text(
        "Unassociated synthetic source.\n",
        encoding="utf-8",
        newline="\n",
    )
    job = _create_job(layout, "register_by_reference", "associate_source_asset")
    source_service = SourceAssetService(layout)
    created = source_service.register_reference(
        job_id=job["job_id"],
        paper_id=None,
        asset_role="main_pdf",
        root_id="alpha-sources",
        relative_path=source.name,
        actor="cli",
        fixture_origin="synthetic_from_scratch",
    )
    jobs = PipelineJobService(layout)
    running = jobs.transition(
        job["job_id"],
        expected_state_id=job["state_id"],
        expected_state_digest=canonical_digest(job),
        status="running",
        current_node="registry",
        wait_reason=None,
        output_refs=[created.state["source_asset_id"]],
        retry_increment=0,
        recovery_action=None,
        actor="cli",
    )

    with pytest.raises(ResearchKBError) as incomplete:
        jobs.transition(
            job["job_id"],
            expected_state_id=running.state["state_id"],
            expected_state_digest=canonical_digest(running.state),
            status="completed",
            current_node="registry",
            wait_reason=None,
            output_refs=[],
            retry_increment=0,
            recovery_action=None,
            actor="cli",
        )
    assert incomplete.value.diagnostic.code == "RKBC-018"

    paper, _ = RegistryService(layout).add(
        root_id="alpha-sources",
        relative_path=source.name,
        metadata={"fixture_origin": "synthetic_from_scratch"},
    )
    associated = source_service.associate(
        source_asset_id=created.state["source_asset_id"],
        job_id=job["job_id"],
        paper_id=paper["paper_id"],
        expected_state_id=created.state["source_asset_state_id"],
        expected_state_digest=canonical_digest(created.state),
        actor="cli",
    )
    replayed = source_service.register_reference(
        job_id=job["job_id"],
        paper_id=None,
        asset_role="main_pdf",
        root_id="alpha-sources",
        relative_path=source.name,
        actor="cli",
        fixture_origin="synthetic_from_scratch",
    )
    parsed, _ = ParseService(layout).run(
        paper_id=paper["paper_id"],
        adapter=SyntheticTextAdapter(),
    )
    completed = jobs.transition(
        job["job_id"],
        expected_state_id=running.state["state_id"],
        expected_state_digest=canonical_digest(running.state),
        status="completed",
        current_node="registry",
        wait_reason=None,
        output_refs=[paper["paper_id"], associated.state["source_asset_id"]],
        retry_increment=0,
        recovery_action=None,
        actor="cli",
    )

    assert completed.state["terminal_receipt"] is True
    assert associated.state["paper_id"] == paper["paper_id"]
    assert replayed.transaction is None
    assert replayed.state == associated.state
    assert parsed[0]["paper_id"] == paper["paper_id"]


def test_unassociated_source_cannot_be_adopted_by_another_job(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    source = layout.source_roots["alpha-sources"] / "owned-unassociated.pdf"
    source.write_bytes(bytes((37, 80, 68, 70, 45)) + b"1.4\nsynthetic owned source\n%%EOF\n")
    first_job = _create_job(layout, "register_by_reference")
    second_job = PipelineJobService(layout).create(
        requested_route="local_source",
        requested_depth="registry_only",
        current_node="source_intake",
        input_refs=[],
        authority_snapshot={
            "actor": "user",
            "granted_operations": ["register_by_reference"],
            "captured_at": "2026-07-30T05:00:00Z",
        },
        idempotency_key="source-asset-test-second-owner",
        actor="user",
        fixture_origin="synthetic_from_scratch",
    ).state
    SourceAssetService(layout).register_reference(
        job_id=first_job["job_id"],
        paper_id=None,
        asset_role="main_pdf",
        root_id="alpha-sources",
        relative_path=source.name,
        actor="cli",
        fixture_origin="synthetic_from_scratch",
    )

    with pytest.raises(ResearchKBError) as conflict:
        SourceAssetService(layout).register_reference(
            job_id=second_job["job_id"],
            paper_id=None,
            asset_role="main_pdf",
            root_id="alpha-sources",
            relative_path=source.name,
            actor="cli",
            fixture_origin="synthetic_from_scratch",
        )

    assert conflict.value.diagnostic.code == "RKBC-017"
    assert len(read_jsonl(layout.source_assets_path, record_kind="source-asset-state")) == 1


def test_guardian_requires_one_correlated_source_success_event(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    source = layout.source_roots["alpha-sources"] / "event-correlation.pdf"
    source.write_bytes(bytes((37, 80, 68, 70, 45)) + b"1.4\nsynthetic event correlation\n%%EOF\n")
    job = _create_job(layout, "register_by_reference")
    created = SourceAssetService(layout).register_reference(
        job_id=job["job_id"],
        paper_id=None,
        asset_role="main_pdf",
        root_id="alpha-sources",
        relative_path=source.name,
        actor="cli",
        fixture_origin="synthetic_from_scratch",
    )
    events = read_jsonl(layout.process_events_path, record_kind="process-event")
    layout.process_events_path.write_bytes(
        serialize_jsonl(
            [
                event
                for event in events
                if created.state["source_asset_state_id"] not in event["output_refs"]
            ]
        )
    )

    findings = GuardianService(layout).check().report["findings"]
    assert any(
        finding["record_ref"] == created.state["source_asset_state_id"]
        and "exactly one correlated success event; found 0" in finding["message"]
        for finding in findings
    )


def test_unassociated_source_blocks_completed_with_findings(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    source = layout.source_roots["alpha-sources"] / "unassociated-findings.pdf"
    source.write_bytes(bytes((37, 80, 68, 70, 45)) + b"1.4\nsynthetic findings source\n%%EOF\n")
    job = _create_job(layout, "register_by_reference")
    SourceAssetService(layout).register_reference(
        job_id=job["job_id"],
        paper_id=None,
        asset_role="main_pdf",
        root_id="alpha-sources",
        relative_path=source.name,
        actor="cli",
        fixture_origin="synthetic_from_scratch",
    )
    jobs = PipelineJobService(layout)
    running = jobs.transition(
        job["job_id"],
        expected_state_id=job["state_id"],
        expected_state_digest=canonical_digest(job),
        status="running",
        current_node="registry",
        wait_reason=None,
        output_refs=[],
        retry_increment=0,
        recovery_action=None,
        actor="cli",
    )

    with pytest.raises(ResearchKBError) as incomplete:
        jobs.transition(
            job["job_id"],
            expected_state_id=running.state["state_id"],
            expected_state_digest=canonical_digest(running.state),
            status="completed_with_findings",
            current_node="registry",
            wait_reason=None,
            output_refs=[],
            retry_increment=0,
            recovery_action=None,
            actor="cli",
        )

    assert incomplete.value.diagnostic.code == "RKBC-018"


def test_unassociated_source_allows_failed_terminal_receipt(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    source = layout.source_roots["alpha-sources"] / "failed-source.pdf"
    source.write_bytes(bytes((37, 80, 68, 70, 45)) + b"1.4\nsynthetic failed source\n%%EOF\n")
    job = _create_job(layout, "register_by_reference")
    created = SourceAssetService(layout).register_reference(
        job_id=job["job_id"],
        paper_id=None,
        asset_role="main_pdf",
        root_id="alpha-sources",
        relative_path=source.name,
        actor="cli",
        fixture_origin="synthetic_from_scratch",
    )
    jobs = PipelineJobService(layout)
    running = jobs.transition(
        job["job_id"],
        expected_state_id=job["state_id"],
        expected_state_digest=canonical_digest(job),
        status="running",
        current_node="registry",
        wait_reason=None,
        output_refs=[created.state["source_asset_id"]],
        retry_increment=0,
        recovery_action=None,
        actor="cli",
    )
    failed = jobs.transition(
        job["job_id"],
        expected_state_id=running.state["state_id"],
        expected_state_digest=canonical_digest(running.state),
        status="failed",
        current_node="registry",
        wait_reason=None,
        output_refs=[],
        retry_increment=0,
        recovery_action=None,
        actor="cli",
    )

    assert failed.state["terminal_receipt"] is True
    assert any(
        finding["record_ref"] == created.state["source_asset_state_id"]
        and "not yet associated" in finding["message"]
        for finding in GuardianService(layout).check().report["findings"]
    )


def test_unassociated_source_allows_cancel_but_remains_guardian_visible(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    source = layout.source_roots["alpha-sources"] / "cancelled.pdf"
    source.write_bytes(
        bytes((37, 80, 68, 70, 45))
        + b"1.4\ncancelled synthetic source\n%%EOF\n"
    )
    job = _create_job(layout, "register_by_reference")
    created = SourceAssetService(layout).register_reference(
        job_id=job["job_id"],
        paper_id=None,
        asset_role="main_pdf",
        root_id="alpha-sources",
        relative_path=source.name,
        actor="cli",
        fixture_origin="synthetic_from_scratch",
    )
    jobs = PipelineJobService(layout)
    running = jobs.transition(
        job["job_id"],
        expected_state_id=job["state_id"],
        expected_state_digest=canonical_digest(job),
        status="running",
        current_node="registry",
        wait_reason=None,
        output_refs=[created.state["source_asset_id"]],
        retry_increment=0,
        recovery_action=None,
        actor="cli",
    )

    cancelled = jobs.cancel(
        job["job_id"],
        expected_state_id=running.state["state_id"],
        expected_state_digest=canonical_digest(running.state),
        actor="user",
    )

    assert cancelled.state["status"] == "cancelled"
    assert any(
        finding["record_ref"] == created.state["source_asset_state_id"]
        and "not yet associated" in finding["message"]
        for finding in GuardianService(layout).check().report["findings"]
    )


def test_source_association_enforces_authority_actor_cas_paper_and_current_source(
    tmp_path: Path,
) -> None:
    layout = make_runtime_workspace(tmp_path)
    source = layout.source_roots["alpha-sources"] / "association.pdf"
    source.write_bytes(
        bytes((37, 80, 68, 70, 45))
        + b"1.4\nassociation synthetic source\n%%EOF\n"
    )
    denied_job = _create_job(layout, "register_by_reference")
    created = SourceAssetService(layout).register_reference(
        job_id=denied_job["job_id"],
        paper_id=None,
        asset_role="main_pdf",
        root_id="alpha-sources",
        relative_path=source.name,
        actor="cli",
        fixture_origin="synthetic_from_scratch",
    )
    paper, _ = RegistryService(layout).add(
        root_id="alpha-sources",
        relative_path=source.name,
        metadata={"fixture_origin": "synthetic_from_scratch"},
    )
    service = SourceAssetService(layout)

    with pytest.raises(ResearchKBError) as authority:
        service.associate(
            source_asset_id=created.state["source_asset_id"],
            job_id=denied_job["job_id"],
            paper_id=paper["paper_id"],
            expected_state_id=created.state["source_asset_state_id"],
            expected_state_digest=canonical_digest(created.state),
            actor="cli",
        )
    assert authority.value.diagnostic.code == "RKBC-006"

    allowed_job = _create_job(layout, "associate_source_asset")
    for actor, paper_id, digest, expected_code in (
        ("agent", paper["paper_id"], canonical_digest(created.state), "RKBC-006"),
        ("cli", "paper_00000000-0000-4000-8000-000000000001", canonical_digest(created.state), "RKBC-005"),
        ("cli", paper["paper_id"], "0" * 64, "RKBC-017"),
    ):
        with pytest.raises(ResearchKBError) as rejected:
            service.associate(
                source_asset_id=created.state["source_asset_id"],
                job_id=allowed_job["job_id"],
                paper_id=paper_id,
                expected_state_id=created.state["source_asset_state_id"],
                expected_state_digest=digest,
                actor=actor,
            )
        assert rejected.value.diagnostic.code == expected_code

    source.write_bytes(source.read_bytes() + b"changed")
    with pytest.raises(ResearchKBError) as changed:
        service.associate(
            source_asset_id=created.state["source_asset_id"],
            job_id=allowed_job["job_id"],
            paper_id=paper["paper_id"],
            expected_state_id=created.state["source_asset_state_id"],
            expected_state_digest=canonical_digest(created.state),
            actor="cli",
        )
    assert changed.value.diagnostic.code == "RKBC-009"


def test_main_source_association_cannot_silently_replace_registry_manifestation(
    tmp_path: Path,
) -> None:
    layout = make_runtime_workspace(tmp_path)
    registered = layout.source_roots["alpha-sources"] / "registered.pdf"
    alternate = layout.source_roots["alpha-sources"] / "alternate.pdf"
    registered.write_bytes(
        bytes((37, 80, 68, 70, 45)) + b"1.4\nregistered source\n%%EOF\n"
    )
    alternate.write_bytes(
        bytes((37, 80, 68, 70, 45)) + b"1.4\nalternate source\n%%EOF\n"
    )
    paper, _ = RegistryService(layout).add(
        root_id="alpha-sources",
        relative_path=registered.name,
        metadata={"fixture_origin": "synthetic_from_scratch"},
    )
    job = _create_job(layout, "register_by_reference", "associate_source_asset")
    service = SourceAssetService(layout)
    created = service.register_reference(
        job_id=job["job_id"],
        paper_id=None,
        asset_role="main_pdf",
        root_id="alpha-sources",
        relative_path=alternate.name,
        actor="cli",
        fixture_origin="synthetic_from_scratch",
    )

    with pytest.raises(ResearchKBError) as mismatch:
        service.associate(
            source_asset_id=created.state["source_asset_id"],
            job_id=job["job_id"],
            paper_id=paper["paper_id"],
            expected_state_id=created.state["source_asset_state_id"],
            expected_state_digest=canonical_digest(created.state),
            actor="cli",
        )

    assert mismatch.value.diagnostic.code == "RKBC-009"


def test_guardian_detects_tampered_main_source_registry_fingerprint_mismatch(
    tmp_path: Path,
) -> None:
    layout = make_runtime_workspace(tmp_path)
    registered = layout.source_roots["alpha-sources"] / "registered-guardian.pdf"
    alternate = layout.source_roots["alpha-sources"] / "alternate-guardian.pdf"
    registered.write_bytes(bytes((37, 80, 68, 70, 45)) + b"1.4\nregistered guardian source\n%%EOF\n")
    alternate.write_bytes(bytes((37, 80, 68, 70, 45)) + b"1.4\nalternate guardian source\n%%EOF\n")
    paper, _ = RegistryService(layout).add(
        root_id="alpha-sources",
        relative_path=registered.name,
        metadata={"fixture_origin": "synthetic_from_scratch"},
    )
    job = _create_job(layout, "register_by_reference")
    created = SourceAssetService(layout).register_reference(
        job_id=job["job_id"],
        paper_id=paper["paper_id"],
        asset_role="supplement",
        root_id="alpha-sources",
        relative_path=alternate.name,
        actor="cli",
        fixture_origin="synthetic_from_scratch",
    )
    tampered = {**created.state, "asset_role": "main_pdf"}
    layout.source_assets_path.write_bytes(serialize_jsonl([tampered]))

    findings = GuardianService(layout).check().report["findings"]

    assert any(
        finding["code"] == "RKBC-009"
        and "Registry paper fingerprint" in finding["message"]
        for finding in findings
    )


def test_source_asset_rejects_hardlinked_reference_and_projects_later_ambiguity(
    tmp_path: Path,
) -> None:
    layout = make_runtime_workspace(tmp_path)
    source = layout.source_roots["alpha-sources"] / "paper.pdf"
    source.write_bytes(bytes((37, 80, 68, 70, 45)) + b"1.4\nlink boundary\n%%EOF\n")
    paper, _ = RegistryService(layout).add(
        root_id="alpha-sources",
        relative_path=source.name,
        metadata={"fixture_origin": "synthetic_from_scratch"},
    )
    job = _create_job(layout, "register_by_reference", "observe_source")
    link = layout.source_roots["alpha-sources"] / "paper-link.pdf"
    link.hardlink_to(source)

    with pytest.raises(ResearchKBError) as ambiguous:
        SourceAssetService(layout).register_reference(
            job_id=job["job_id"],
            paper_id=paper["paper_id"],
            asset_role="main_pdf",
            root_id="alpha-sources",
            relative_path=source.name,
            actor="cli",
        )
    assert ambiguous.value.diagnostic.code == "RKBC-007"
    link.unlink()

    created = SourceAssetService(layout).register_reference(
        job_id=job["job_id"],
        paper_id=paper["paper_id"],
        asset_role="main_pdf",
        root_id="alpha-sources",
        relative_path=source.name,
        actor="cli",
    )
    link.hardlink_to(source)
    observed = SourceAssetService(layout).observe(
        source_asset_id=created.state["source_asset_id"],
        job_id=job["job_id"],
        expected_state_id=created.state["source_asset_state_id"],
        expected_state_digest=canonical_digest(created.state),
        actor="cli",
    )

    assert observed.state["availability"] == "relink_required"
    assert source_asset_projection(
        read_jsonl(layout.source_assets_path, record_kind="source-asset-state")
    )[0]["source_currentness"] == "unavailable"
    findings = GuardianService(layout).check().report["findings"]
    assert any("relink_required" in item["message"] for item in findings)


def test_source_asset_rejects_a_declared_root_that_traverses_a_link(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    layout = make_runtime_workspace(tmp_path)
    source = layout.source_roots["alpha-sources"] / "root-link.pdf"
    source.write_bytes(bytes((37, 80, 68, 70, 45)) + b"1.4\nsynthetic root link\n%%EOF\n")
    job = _create_job(layout, "register_by_reference")
    original = source_resolution_module._is_unsafe_link
    monkeypatch.setattr(
        source_resolution_module,
        "_is_unsafe_link",
        lambda path: path == layout.source_roots["alpha-sources"] or original(path),
    )

    with pytest.raises(ResearchKBError) as unsafe:
        SourceAssetService(layout).register_reference(
            job_id=job["job_id"],
            paper_id=None,
            asset_role="main_pdf",
            root_id="alpha-sources",
            relative_path=source.name,
            actor="cli",
        )

    assert unsafe.value.diagnostic.code == "RKBC-007"
    assert not layout.source_assets_path.exists()


def test_source_asset_revalidates_link_safety_after_canonical_replacement(
    tmp_path: Path,
) -> None:
    layout = make_runtime_workspace(tmp_path)
    source = layout.source_roots["alpha-sources"] / "transaction-link.pdf"
    source.write_bytes(
        bytes((37, 80, 68, 70, 45)) + b"1.4\ntransaction link\n%%EOF\n"
    )
    paper, _ = RegistryService(layout).add(
        root_id="alpha-sources",
        relative_path=source.name,
        metadata={"fixture_origin": "synthetic_from_scratch"},
    )
    job = _create_job(layout, "register_by_reference")
    hard_link = layout.source_roots["alpha-sources"] / "transaction-link-copy.pdf"

    class LinkingTransactionManager(TransactionManager):
        def promote_bytes(self, **kwargs):
            def link_after_prepare(phase: str) -> None:
                if phase == "prepared":
                    hard_link.hardlink_to(source)

            return super().promote_bytes(**kwargs, phase_hook=link_after_prepare)

    with pytest.raises(ResearchKBError) as changed:
        SourceAssetService(
            layout,
            transaction_manager=LinkingTransactionManager(layout),
        ).register_reference(
            job_id=job["job_id"],
            paper_id=paper["paper_id"],
            asset_role="main_pdf",
            root_id="alpha-sources",
            relative_path=source.name,
            actor="cli",
            fixture_origin="synthetic_from_scratch",
        )

    assert changed.value.diagnostic.code == "RKBC-018"
    assert hard_link.exists()
    assert any(
        "relink_required" in finding["message"]
        for finding in GuardianService(layout).check().report["findings"]
    )


def test_source_asset_chain_rejects_association_that_changes_manifestation(
    tmp_path: Path,
) -> None:
    layout = make_runtime_workspace(tmp_path)
    source = layout.source_roots["alpha-sources"] / "tampered-chain.pdf"
    source.write_bytes(
        bytes((37, 80, 68, 70, 45)) + b"1.4\ntampered chain\n%%EOF\n"
    )
    job = _create_job(layout, "register_by_reference")
    root = SourceAssetService(layout).register_reference(
        job_id=job["job_id"],
        paper_id=None,
        asset_role="main_pdf",
        root_id="alpha-sources",
        relative_path=source.name,
        actor="cli",
        fixture_origin="synthetic_from_scratch",
    ).state
    tampered = {
        **root,
        "source_asset_state_id": "sourceassetstate_a1111111-1111-4111-8111-111111111111",
        "revision": 2,
        "predecessor": {
            "state_id": root["source_asset_state_id"],
            "state_digest": canonical_digest(root),
        },
        "paper_id": "paper_a1111111-1111-4111-8111-111111111111",
        "source_fingerprint": {"algorithm": "sha256", "value": "b" * 64},
        "manifestation_id": "sha256:" + "b" * 64,
        "reason": "paper_associated",
    }

    diagnostics = source_asset_chain_diagnostics([root, tampered])

    assert any(
        "association cannot change source manifestation" in item.message
        for item in diagnostics
    )


def test_guardian_reports_malformed_source_asset_without_crashing(tmp_path: Path) -> None:
    layout = make_runtime_workspace(
        tmp_path,
        local_inbox="./sources/inbox",
        create_local_inbox=True,
    )
    source = layout.source_roots["alpha-sources"] / "malformed-guardian.pdf"
    source.write_bytes(bytes((37, 80, 68, 70, 45)) + b"1.4\nmalformed guardian\n%%EOF\n")
    job = _create_job(layout, "register_by_reference")
    created = SourceAssetService(layout).register_reference(
        job_id=job["job_id"],
        paper_id=None,
        asset_role="main_pdf",
        root_id="alpha-sources",
        relative_path=source.name,
        actor="cli",
        fixture_origin="synthetic_from_scratch",
    ).state
    malformed = dict(created)
    malformed.pop("source_ref")
    layout.source_assets_path.write_bytes(serialize_jsonl([malformed]))

    report = GuardianService(layout).check().report

    assert any(
        finding["record_ref"] == created["source_asset_state_id"]
        and finding["code"] == "RKBC-002"
        for finding in report["findings"]
    )


def test_same_digest_relink_preserves_parse_and_changed_head_blocks_reuse(tmp_path: Path) -> None:
    layout = _workspace_with_two_roots(tmp_path)
    first = layout.source_roots["alpha-sources"] / "paper.txt"
    second = layout.source_roots["alpha-alt"] / "paper-copy.txt"
    content = b"Synthetic page one.\fSynthetic page two."
    first.write_bytes(content)
    second.write_bytes(content)
    paper, _ = RegistryService(layout).add(
        root_id="alpha-sources",
        relative_path=first.name,
        metadata={"fixture_origin": "synthetic_from_scratch"},
    )
    job = _create_job(
        layout,
        "register_by_reference",
        "same_digest_relink",
        "observe_source",
    )
    service = SourceAssetService(layout)
    created = service.register_reference(
        job_id=job["job_id"],
        paper_id=paper["paper_id"],
        asset_role="main_pdf",
        root_id="alpha-sources",
        relative_path=first.name,
        actor="cli",
    )
    ParseService(layout).run(paper_id=paper["paper_id"], adapter=SyntheticTextAdapter())
    relinked = service.relink(
        source_asset_id=created.state["source_asset_id"],
        job_id=job["job_id"],
        root_id="alpha-alt",
        relative_path=second.name,
        expected_state_id=created.state["source_asset_state_id"],
        expected_state_digest=canonical_digest(created.state),
        actor="cli",
    )
    first.unlink()

    assert ParseReadService(layout).show(paper_id=paper["paper_id"])["page_count"] == 2

    second.write_bytes(b"Changed synthetic source.")
    changed = service.observe(
        source_asset_id=created.state["source_asset_id"],
        job_id=job["job_id"],
        expected_state_id=relinked.state["source_asset_state_id"],
        expected_state_digest=canonical_digest(relinked.state),
        actor="cli",
    )
    assert changed.state["manifestation_status"] == "change_candidate"
    assert PaperStatusService(layout).show(paper_id=paper["paper_id"])["parse"]["state"] == "stale_source"
    with pytest.raises(ResearchKBError) as stale:
        ParseReadService(layout).show(paper_id=paper["paper_id"])
    assert stale.value.diagnostic.code == "RKBC-009"
