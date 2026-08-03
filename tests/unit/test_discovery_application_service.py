from __future__ import annotations

import hashlib
import os
import pytest

from research_kb.discovery import DiscoveryCandidate, DiscoveryProviderResult, DiscoverySource
from research_kb.discovery.acquisition import DownloadedAsset, FileIdentity
from research_kb.discovery.resolution import ProviderAssetRef, ProviderResolution
from research_kb.errors import ResearchKBError
from research_kb.services import DiscoveryApplicationService, WorkspaceSession
from tests.runtime_helpers import make_runtime_workspace


PDF_BYTES = bytes((37, 80, 68, 70, 45)) + b"1.4\nsynthetic discovery application source\n%%EOF\n"


class FakeConnector:
    connector_id = "europe-pmc"
    network_required = True

    def search(self, request):
        candidates = (
            DiscoveryCandidate(
                title="Targeted degradation delivery alpha",
                authors=("Alpha Researcher",),
                first_publication_date="2026-07-20",
                journal_or_server="Synthetic Journal",
                doi="10.0000/discovery.alpha",
                paper_type="article",
                publication_types=("Journal Article",),
                abstract="Delivery in a targeted degradation system.",
                discovery_sources=(DiscoverySource("europe-pmc", "MED", "ALPHA-1"),),
                full_text_status="open_access",
            ),
            DiscoveryCandidate(
                title="Targeted degradation delivery beta",
                authors=("Beta Researcher",),
                first_publication_date="2026-07-19",
                journal_or_server="Synthetic Journal",
                doi="10.0000/discovery.beta",
                paper_type="article",
                publication_types=("Journal Article",),
                abstract="Delivery in another targeted degradation system.",
                discovery_sources=(DiscoverySource("europe-pmc", "MED", "BETA-1"),),
                full_text_status="open_access",
            ),
        )
        return DiscoveryProviderResult(
            provider="europe-pmc",
            provider_api_version="synthetic-6.9",
            provider_hit_count=2,
            scanned_result_count=2,
            exhausted=True,
            candidates=candidates,
        )


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
                record_id=candidate["discovery_sources"][0]["record_id"],
                pmcid="PMC1234567",
                asset_kind="pdf",
                route="europe-pmc-pdf-v1",
            ),
            access_basis="repository_open_access",
            license_observation="provider_oa_policy_no_license_text",
            manual_reason=None,
        )


class FakeTransport:
    transport_id = "europe-pmc"
    network_required = True

    def download(self, asset_ref, target):
        with target.open("xb") as stream:
            stream.write(PDF_BYTES)
            stream.flush()
            os.fsync(stream.fileno())
        stat_result = target.stat()
        digest = hashlib.sha256(PDF_BYTES).hexdigest()
        return DownloadedAsset(
            content_type="application/pdf",
            content_size_bytes=len(PDF_BYTES),
            sha256=digest,
            file_identity=FileIdentity(
                device=stat_result.st_dev,
                inode=stat_result.st_ino,
                size=len(PDF_BYTES),
                sha256=digest,
            ),
        )


def _session(tmp_path):
    layout = make_runtime_workspace(
        tmp_path,
        local_inbox="./sources/inbox",
        create_local_inbox=True,
    )
    return layout, WorkspaceSession(
        "alpha",
        layout.workspace_id,
        "domain-alpha",
        "Synthetic Alpha Domain",
        "1.0",
        layout,
    )


def _request():
    return {
        "request_version": "1.0",
        "date_from": "2026-07-14",
        "date_until": "2026-07-21",
        "title_keywords": ["targeted degradation"],
        "abstract_keywords": ["delivery"],
        "keyword_mode": "any",
        "include_preprints": True,
        "max_results": 15,
    }


def _tree_bytes(root):
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _service():
    return DiscoveryApplicationService(
        connectors=(FakeConnector(),),
        resolvers=(FakeResolver(),),
        transports=(FakeTransport(),),
        pdf_preflight=lambda path, candidate_id: None,
    )


def test_search_is_workspace_independent_and_zero_write(tmp_path) -> None:
    layout, _ = _session(tmp_path)
    before = _tree_bytes(layout.config.path.parent)

    service = _service()
    assert service.limits() == {
        "status": "success",
        "interface_version": "1.15",
        "provider": "europe-pmc",
        "max_results": 15,
        "max_date_span_days": 31,
        "max_page_size": 100,
    }
    report = service.search(_request())

    assert report["persistent_writes"] == 0
    assert report["returned_result_count"] == 2
    assert _tree_bytes(layout.config.path.parent) == before


def test_selection_and_candidate_reads_are_explicit_and_paginated(tmp_path) -> None:
    _, session = _session(tmp_path)
    service = _service()
    report = service.search(_request())
    result_keys = [item["result_key"] for item in report["results"]]

    selected = service.select(session, report, result_keys, actor="user")

    assert selected["persistent_writes"] == 1
    first = service.list_candidates(session, page_size=1)
    second = service.list_candidates(session, page_size=1, cursor=first["next_cursor"])
    assert len(first["candidates"]) == len(second["candidates"]) == 1
    assert first["next_cursor"] == first["candidates"][0]["candidate_id"]
    assert second["next_cursor"] is None
    assert first["candidates"][0]["candidate_id"] < second["candidates"][0]["candidate_id"]

    with pytest.raises(ResearchKBError):
        service.select(session, report, result_keys, actor="agent")
    with pytest.raises(ResearchKBError):
        service.list_candidates(session, page_size=101)
    with pytest.raises(ResearchKBError):
        service.list_candidates(session, page_size=1, cursor="not-a-candidate")


def test_resolution_acquisition_and_handoff_stop_before_registry(tmp_path) -> None:
    layout, session = _session(tmp_path)
    service = _service()
    report = service.search(_request())
    selected = service.select(
        session,
        report,
        [report["results"][0]["result_key"]],
        actor="user",
    )
    candidate_id = selected["selected_candidate_ids"][0]
    before_resolution = _tree_bytes(layout.config.path.parent)

    resolution = service.resolve(session, candidate_id)
    assert resolution["resolution_status"] == "auto_acquisition_eligible"
    assert resolution["persistent_writes"] == 0
    assert _tree_bytes(layout.config.path.parent) == before_resolution

    acquired = service.acquire(session, candidate_id, actor="user")
    assert acquired["persistent_writes"] == 2
    assert service.show_candidate(session, candidate_id)["candidate"]["acquisition_status"] == "acquired"

    handoff = service.inspect_acquired(session, candidate_id)
    assert handoff["registration"] == {"state": "unregistered", "paper_ids": []}
    assert handoff["persistent_writes"] == 0
    assert not layout.registry_path.exists()

    replay = service.acquire(session, candidate_id, actor="user")
    assert replay["persistent_writes"] == 0
    assert replay["result"] == "no_change"
