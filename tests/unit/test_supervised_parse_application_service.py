from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from research_kb.errors import ResearchKBError
from research_kb.parse.pdfplumber_adapter import PdfPlumberTextFlowAdapter
from research_kb.parse.worker_protocol import ParserBudgetProfile, WorkerParseResult
from research_kb.services.registry import RegistryService
from research_kb.services.supervised_parse_application import SupervisedParseApplicationService
from research_kb.services.trusted_parse_authority import TrustedParseAuthorityService
from research_kb.storage.json_io import file_sha256, read_jsonl
from tests.pdf_helpers import write_synthetic_pdf
from tests.runtime_helpers import make_runtime_workspace


NOW = datetime(2026, 8, 7, 4, 0, tzinfo=UTC)


def _trusted_case(tmp_path: Path):
    layout = make_runtime_workspace(tmp_path)
    source = layout.source_roots["alpha-sources"] / "supervised.pdf"
    write_synthetic_pdf(source, ["Synthetic supervised page one.", "Synthetic page two."])
    paper, _ = RegistryService(layout).add(
        root_id="alpha-sources",
        relative_path=source.name,
        metadata={"fixture_origin": "synthetic_from_scratch"},
    )
    adapter = PdfPlumberTextFlowAdapter()
    trust = TrustedParseAuthorityService(layout, clock=lambda: NOW)
    preview = trust.preview(
        paper_id=paper["paper_id"],
        adapter_name=adapter.name,
        adapter_version=adapter.version,
        parser_profile_id="trusted-local-pdf-standard@1.0",
        policy_version="trusted-local-pdf@1.0",
        allowed_operation="parse_run",
        idempotency_key="supervised-trust-0001",
        actor="user",
        expires_at=NOW + timedelta(hours=1),
    )
    authority = trust.commit(preview, preview_digest=preview.preview_digest, actor="user")
    return layout, paper, source, authority


def test_missing_authority_rejects_before_worker_start(tmp_path: Path) -> None:
    layout, paper, _source, _authority = _trusted_case(tmp_path)
    calls: list[str] = []

    def runner(_request):
        calls.append("started")
        raise AssertionError("worker must not start")

    with pytest.raises(ResearchKBError) as caught:
        SupervisedParseApplicationService(layout, worker_runner=runner, clock=lambda: NOW).run(
            paper_id=paper["paper_id"],
            authority_id="parseauth_00000000-0000-4000-8000-000000000000",
            actor="user",
        )
    assert caught.value.diagnostic.code == "RKBC-036"
    assert calls == []


def test_supervised_parse_promotes_worker_pages(tmp_path: Path) -> None:
    layout, paper, source, authority = _trusted_case(tmp_path)
    before = file_sha256(source)
    result = SupervisedParseApplicationService(layout, clock=lambda: NOW).run(
        paper_id=paper["paper_id"],
        authority_id=authority.authority_id,
        actor="user",
    )

    pages = read_jsonl(layout.parse_path(paper["paper_id"]), record_kind="parsed-page")
    assert result.page_count == 2
    assert len(pages) == 2
    assert file_sha256(source) == before


def test_source_drift_after_worker_blocks_promotion(tmp_path: Path) -> None:
    layout, paper, source, authority = _trusted_case(tmp_path)

    def drifting_runner(request):
        source.write_bytes(source.read_bytes() + b"changed")
        return WorkerParseResult(
            pages=(
                {
                    "pdf_page": 1,
                    "printed_page": None,
                    "text": "Synthetic output.",
                    "locator": "page:1:text",
                },
            ),
            source_sha256=request.source_sha256,
            parser={"adapter": request.adapter_name, "version": request.adapter_version},
            output_utf8_bytes=17,
        )

    with pytest.raises(ResearchKBError) as caught:
        SupervisedParseApplicationService(layout, worker_runner=drifting_runner, clock=lambda: NOW).run(
            paper_id=paper["paper_id"],
            authority_id=authority.authority_id,
            actor="user",
        )
    assert caught.value.diagnostic.code == "RKBC-009"
    assert not layout.parse_path(paper["paper_id"]).exists()


def test_source_size_budget_is_enforced_before_worker(tmp_path: Path) -> None:
    layout, paper, _source, authority = _trusted_case(tmp_path)
    profile = ParserBudgetProfile(max_source_bytes=4)

    with pytest.raises(ResearchKBError) as caught:
        SupervisedParseApplicationService(layout, budget=profile, clock=lambda: NOW).run(
            paper_id=paper["paper_id"],
            authority_id=authority.authority_id,
            actor="user",
        )
    assert caught.value.diagnostic.code == "RKBC-030"
    assert not layout.parse_path(paper["paper_id"]).exists()
