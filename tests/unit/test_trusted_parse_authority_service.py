from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from research_kb.errors import ResearchKBError
from research_kb.catalog.models import canonical_digest
from research_kb.parse.pdfplumber_adapter import PdfPlumberTextFlowAdapter
from research_kb.services.registry import RegistryService
from research_kb.services.pipeline_job import PipelineJobService
from research_kb.services.trusted_parse_authority import TrustedParseAuthorityService
from research_kb.process_events import read_process_events
from research_kb.storage.json_io import read_jsonl, serialize_jsonl
from research_kb.trusted_parse_authority import trusted_parse_authority_chain_diagnostics
from tests.pdf_helpers import write_synthetic_pdf
from tests.runtime_helpers import make_runtime_workspace


NOW = datetime(2026, 8, 7, 4, 0, tzinfo=UTC)


def _paper(layout):
    source = layout.source_roots["alpha-sources"] / "trusted.pdf"
    write_synthetic_pdf(source, ["Synthetic trusted page."])
    paper, _ = RegistryService(layout).add(
        root_id="alpha-sources",
        relative_path=source.name,
        metadata={"fixture_origin": "synthetic_from_scratch"},
    )
    return paper, source


def _preview(service, paper_id: str):
    adapter = PdfPlumberTextFlowAdapter()
    return service.preview(
        paper_id=paper_id,
        adapter_name=adapter.name,
        adapter_version=adapter.version,
        parser_profile_id="trusted-local-pdf-standard@1.0",
        policy_version="trusted-local-pdf@1.0",
        allowed_operation="parse_run",
        idempotency_key="trust-decision-0001",
        actor="user",
        expires_at=NOW + timedelta(hours=1),
    )


def test_authority_preview_is_zero_write_and_commit_is_idempotent(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    paper, _ = _paper(layout)
    service = TrustedParseAuthorityService(layout, clock=lambda: NOW)
    preview = _preview(service, paper["paper_id"])

    assert not layout.trusted_parse_authorities_path.exists()
    with pytest.raises(ResearchKBError) as missing:
        service.commit(preview, actor="user")
    assert missing.value.diagnostic.code == "RKBC-026"
    with pytest.raises(ResearchKBError) as empty:
        service.commit(preview, preview_digest="", actor="user")
    assert empty.value.diagnostic.code == "RKBC-026"
    committed = service.commit(preview, preview_digest=preview.preview_digest, actor="user")
    retried = service.commit(preview, preview_digest=preview.preview_digest, actor="user")

    assert committed.authority_id == preview.authority_id
    assert retried.result == "no_change"
    assert service.current(committed.authority_id).status == "current"


def test_authority_becomes_stale_when_source_changes(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    paper, source = _paper(layout)
    service = TrustedParseAuthorityService(layout, clock=lambda: NOW)
    preview = _preview(service, paper["paper_id"])
    committed = service.commit(preview, preview_digest=preview.preview_digest, actor="user")

    source.write_bytes(source.read_bytes() + b"changed")

    assert service.current(committed.authority_id).status == "stale"


def test_authority_becomes_stale_when_registry_relinks_identical_bytes(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    paper, source = _paper(layout)
    service = TrustedParseAuthorityService(layout, clock=lambda: NOW)
    preview = _preview(service, paper["paper_id"])
    committed = service.commit(preview, preview_digest=preview.preview_digest, actor="user")
    replacement = source.with_name("trusted-relinked.pdf")
    replacement.write_bytes(source.read_bytes())
    paper["source_ref"] = {"root_id": "alpha-sources", "relative_path": replacement.name}
    layout.registry_path.write_bytes(serialize_jsonl([paper]))

    projection = service.current(committed.authority_id)

    assert projection.status == "stale"
    assert projection.reasons == ("source_reference_changed",)


def test_authority_commit_rechecks_source_after_preview(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    paper, source = _paper(layout)
    service = TrustedParseAuthorityService(layout, clock=lambda: NOW)
    preview = _preview(service, paper["paper_id"])
    source.write_bytes(source.read_bytes() + b"changed")

    with pytest.raises(ResearchKBError) as caught:
        service.commit(preview, preview_digest=preview.preview_digest, actor="user")

    assert caught.value.diagnostic.code == "RKBC-036"
    assert not layout.trusted_parse_authorities_path.exists()


def test_authority_projection_tracks_expiry_policy_profile_and_parser_drift(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    paper, _ = _paper(layout)
    now = [NOW]
    service = TrustedParseAuthorityService(layout, clock=lambda: now[0])
    preview = _preview(service, paper["paper_id"])
    committed = service.commit(preview, preview_digest=preview.preview_digest, actor="user")

    now[0] = NOW + timedelta(hours=2)
    assert service.current(committed.authority_id).status == "expired"
    policy = TrustedParseAuthorityService(layout, clock=lambda: NOW, policy_version="trusted-local-pdf@2.0")
    assert policy.current(committed.authority_id).reasons == ("policy_changed",)
    profile = TrustedParseAuthorityService(layout, clock=lambda: NOW, parser_profiles=())
    assert profile.current(committed.authority_id).reasons == ("parser_profile_changed",)
    parser = TrustedParseAuthorityService(layout, clock=lambda: NOW, parser_version_resolver=lambda _name: "0.0")
    assert parser.current(committed.authority_id).reasons == ("parser_version_changed",)


def test_revoke_appends_successor_and_blocks_current_projection(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    paper, _ = _paper(layout)
    service = TrustedParseAuthorityService(layout, clock=lambda: NOW)
    preview = _preview(service, paper["paper_id"])
    committed = service.commit(preview, preview_digest=preview.preview_digest, actor="user")

    revoked = service.revoke(committed.authority_id, actor="user", reason="user_revoked")

    assert revoked.revision == 2
    assert service.current(committed.authority_id).status == "revoked"


def test_authority_lineage_rejects_expiry_change(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    paper, _ = _paper(layout)
    service = TrustedParseAuthorityService(layout, clock=lambda: NOW)
    preview = _preview(service, paper["paper_id"])
    committed = service.commit(preview, preview_digest=preview.preview_digest, actor="user")
    head = read_jsonl(layout.trusted_parse_authorities_path, record_kind="trusted-parse-authority", id_field="state_id")[0]
    successor = {
        **head,
        "state_id": "parseauthstate_00000000-0000-4000-8000-000000000001",
        "revision": 2,
        "predecessor": {"state_id": head["state_id"], "state_digest": canonical_digest(head)},
        "decision": "revoked",
        "revocation_reason": "expiry tamper",
        "decision_at": "2026-08-07T04:30:00Z",
        "expires_at": "2026-08-07T06:00:00Z",
    }

    diagnostics = trusted_parse_authority_chain_diagnostics([head, successor])

    assert diagnostics
    assert any("expires_at" in item.message for item in diagnostics)
    assert committed.authority_id == head["authority_id"]


def test_agent_cannot_commit_or_revoke_trust(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    paper, _ = _paper(layout)
    service = TrustedParseAuthorityService(layout, clock=lambda: NOW)
    preview = _preview(service, paper["paper_id"])

    with pytest.raises(ResearchKBError) as caught:
        service.commit(preview, actor="agent")
    assert caught.value.diagnostic.code == "RKBC-006"


def test_authority_commit_can_bind_exact_pipeline_job(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    paper, _ = _paper(layout)
    job = PipelineJobService(layout).create(
        requested_route="local_source",
        requested_depth="semantic_gate",
        current_node="trusted_parse_authority_primary",
        input_refs=[],
        authority_snapshot={
            "actor": "user",
            "granted_operations": ["parse_run"],
            "captured_at": "2026-08-08T08:00:00Z",
        },
        idempotency_key="trusted-authority-job",
        actor="user",
    ).state
    service = TrustedParseAuthorityService(layout, clock=lambda: NOW)
    preview = _preview(service, paper["paper_id"])

    committed = service.commit(
        preview,
        preview_digest=preview.preview_digest,
        actor="user",
        job_id=job["job_id"],
    )

    event = next(
        item
        for item in read_process_events(layout.process_events_path)
        if item["event_id"] == committed.event_id
    )
    assert event["job_id"] == job["job_id"]
    assert event["input_refs"] == [paper["paper_id"]]
    assert event["output_refs"] == [committed.authority_id, committed.state_id]
