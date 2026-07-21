from __future__ import annotations

import os
import threading
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest

from research_kb.discovery.acquisition import DownloadedAsset, FileIdentity
from research_kb.discovery.resolution import ProviderAssetRef, ProviderResolution
from research_kb.errors import Diagnostic, ResearchKBError
from research_kb.guardian import GuardianService
from research_kb.services.discovery_acquisition import (
    DiscoveryAcquisitionService,
    DiscoveryAcquisitionTransportRegistry,
)
from research_kb.services.discovery_candidate import DiscoveryCandidateService
from research_kb.services.discovery_resolution import DiscoveryResolverRegistry
from research_kb.storage.json_io import read_jsonl
from research_kb.storage.transactions import TransactionManager
from tests.discovery_candidate_helpers import discovery_report, selection_request
from tests.runtime_helpers import make_runtime_workspace


CANDIDATE_ID = "discovery_a1111111-1111-4111-8111-111111111111"
EVENT_ID = "event_a1111111-1111-4111-8111-111111111111"
PDF_BYTES = bytes((37, 80, 68, 70, 45)) + b"1.4\nsynthetic acquisition bytes\n%%EOF\n"


class FakeResolver:
    resolver_id = "europe-pmc"
    network_required = True

    def __init__(self, *, status="auto_acquisition_eligible", pmcid="PMC1234567"):
        self.status = status
        self.pmcid = pmcid
        self.calls = []

    def resolve(self, candidate):
        self.calls.append(deepcopy(candidate))
        eligible = self.status == "auto_acquisition_eligible"
        return ProviderResolution(
            provider="europe-pmc",
            provider_api_version="synthetic-6.9",
            lookup_identity={"kind": "doi", "doi": candidate["doi"]},
            resolution_status=self.status,
            provider_asset_ref=(
                ProviderAssetRef(
                    provider="europe-pmc",
                    source="MED",
                    record_id="SYNTH-DISCOVERY-1",
                    pmcid=self.pmcid,
                    asset_kind="pdf",
                    route="europe-pmc-pdf-v1",
                )
                if eligible
                else None
            ),
            access_basis="repository_open_access" if eligible else "unknown",
            license_observation=(
                "provider_oa_policy_no_license_text" if eligible else "not_observed"
            ),
            manual_reason=None if eligible else "no_pdf_route",
        )


class FakeTransport:
    transport_id = "europe-pmc"
    network_required = True

    def __init__(self, content=PDF_BYTES):
        self.content = content
        self.calls = []

    def download(self, asset_ref, target):
        self.calls.append((asset_ref, target))
        with target.open("xb") as stream:
            stream.write(self.content)
            stream.flush()
            os.fsync(stream.fileno())
        stat_result = target.stat()
        import hashlib

        digest = hashlib.sha256(self.content).hexdigest()
        return DownloadedAsset(
            content_type="application/pdf",
            content_size_bytes=len(self.content),
            sha256=digest,
            file_identity=FileIdentity(
                device=stat_result.st_dev,
                inode=stat_result.st_ino,
                size=len(self.content),
                sha256=digest,
            ),
        )


class MalformedTransport(FakeTransport):
    def download(self, asset_ref, target):
        self.calls.append((asset_ref, target))
        target.write_bytes(PDF_BYTES)
        return {"content_type": "application/pdf"}


def prepared_service(
    tmp_path,
    *,
    resolver=None,
    transport=None,
    pdf_preflight=lambda path, candidate_id: None,
    operation_hook=None,
):
    layout = make_runtime_workspace(
        tmp_path,
        local_inbox="./sources/inbox",
        create_local_inbox=True,
    )
    DiscoveryCandidateService(
        layout,
        id_allocator=lambda namespace: CANDIDATE_ID,
    ).select(selection_request(), actor="user")
    resolver = resolver or FakeResolver()
    transport = transport or FakeTransport()
    manager = TransactionManager(
        layout,
        event_id_factory=lambda: EVENT_ID,
        clock=lambda: datetime(2026, 7, 21, 1, 0, tzinfo=timezone.utc),
    )
    service = DiscoveryAcquisitionService(
        layout,
        resolver_registry=DiscoveryResolverRegistry([resolver]),
        transport_registry=DiscoveryAcquisitionTransportRegistry([transport]),
        transaction_manager=manager,
        pdf_preflight=pdf_preflight,
        operation_hook=operation_hook,
    )
    return layout, resolver, transport, service


def stored_candidate(layout):
    return read_jsonl(
        layout.discovery_candidates_path,
        record_kind="discovery-candidate",
        id_field="candidate_id",
    )[0]


def test_acquire_creates_one_pdf_and_one_portable_receipt(tmp_path) -> None:
    layout, resolver, transport, service = prepared_service(tmp_path)

    report = service.acquire(CANDIDATE_ID, provider="europe-pmc", actor="user")

    final = layout.local_inbox / f"{CANDIDATE_ID}.pdf"
    candidate = stored_candidate(layout)
    assert report["status"] == "success"
    assert report["result"] == "acquired"
    assert report["candidate_id"] == CANDIDATE_ID
    assert report["persistent_writes"] == 2
    assert report["event_id"] == EVENT_ID
    assert report["source_ref"] == {
        "root_id": "alpha-sources",
        "relative_path": f"inbox/{CANDIDATE_ID}.pdf",
    }
    assert final.read_bytes() == PDF_BYTES
    assert candidate["acquisition_status"] == "acquired"
    assert candidate["source_status"] == "metadata_only"
    assert candidate["not_evidence"] is True
    assert candidate["acquisition_receipt"]["source_ref"] == report["source_ref"]
    assert candidate["acquisition_receipt"]["source_fingerprint"] == report[
        "source_fingerprint"
    ]
    assert len(resolver.calls) == len(transport.calls) == 1
    assert not list(layout.local_inbox.glob(".research-kb-acquire-*.part.pdf"))
    assert not layout.registry_path.exists()


def test_acquire_exact_rerun_re_resolves_and_writes_nothing(tmp_path) -> None:
    layout, resolver, transport, service = prepared_service(tmp_path)
    first = service.acquire(CANDIDATE_ID, provider="europe-pmc", actor="user")
    before_candidate = layout.discovery_candidates_path.read_bytes()
    before_source = (layout.local_inbox / f"{CANDIDATE_ID}.pdf").read_bytes()

    second = service.acquire(CANDIDATE_ID, provider="europe-pmc", actor="user")

    assert first["result"] == "acquired"
    assert second["result"] == "no_change"
    assert second["persistent_writes"] == 0
    assert second["event_id"] is None
    assert len(resolver.calls) == 2
    assert len(transport.calls) == 1
    assert layout.discovery_candidates_path.read_bytes() == before_candidate
    assert (layout.local_inbox / f"{CANDIDATE_ID}.pdf").read_bytes() == before_source


def test_acquire_rejects_non_user_before_resolution_or_source_write(tmp_path) -> None:
    layout, resolver, transport, service = prepared_service(tmp_path)

    with pytest.raises(ResearchKBError) as error:
        service.acquire(CANDIDATE_ID, provider="europe-pmc", actor="agent")

    assert error.value.diagnostic.code == "RKBC-006"
    assert resolver.calls == transport.calls == []
    assert not (layout.local_inbox / f"{CANDIDATE_ID}.pdf").exists()


def test_acquire_rejects_noneligible_resolution_without_download(tmp_path) -> None:
    resolver = FakeResolver(status="no_supported_oa_route")
    layout, resolver, transport, service = prepared_service(tmp_path, resolver=resolver)

    with pytest.raises(ResearchKBError) as error:
        service.acquire(CANDIDATE_ID, provider="europe-pmc", actor="user")

    assert error.value.diagnostic.code == "RKBC-033"
    assert len(resolver.calls) == 1
    assert transport.calls == []
    assert not (layout.local_inbox / f"{CANDIDATE_ID}.pdf").exists()


def test_acquire_rejects_existing_final_before_network_and_never_replaces_it(tmp_path) -> None:
    layout, resolver, transport, service = prepared_service(tmp_path)
    final = layout.local_inbox / f"{CANDIDATE_ID}.pdf"
    final.write_bytes(b"pre-existing")

    with pytest.raises(ResearchKBError) as error:
        service.acquire(CANDIDATE_ID, provider="europe-pmc", actor="user")

    assert error.value.diagnostic.code == "RKBC-017"
    assert final.read_bytes() == b"pre-existing"
    assert resolver.calls == transport.calls == []


def test_acquire_removes_owned_partial_when_pdf_preflight_fails(tmp_path) -> None:
    def reject(path, candidate_id):
        raise ResearchKBError(
            Diagnostic(
                "RKBC-029",
                "discovery-acquisition",
                candidate_id,
                "",
                "synthetic PDF preflight failed",
            )
        )

    layout, _, _, service = prepared_service(tmp_path, pdf_preflight=reject)

    with pytest.raises(ResearchKBError) as error:
        service.acquire(CANDIDATE_ID, provider="europe-pmc", actor="user")

    assert error.value.diagnostic.code == "RKBC-029"
    assert not list(layout.local_inbox.iterdir())
    assert stored_candidate(layout)["acquisition_status"] == "not_started"


def test_acquire_fails_closed_when_transport_result_has_no_file_identity(tmp_path) -> None:
    layout, _, _, service = prepared_service(tmp_path, transport=MalformedTransport())

    with pytest.raises(ResearchKBError) as error:
        service.acquire(CANDIDATE_ID, provider="europe-pmc", actor="user")

    assert error.value.diagnostic.code == "RKBC-033"
    assert not (layout.local_inbox / f"{CANDIDATE_ID}.pdf").exists()
    partials = list(layout.local_inbox.glob(".research-kb-acquire-*.part.pdf"))
    assert len(partials) == 1
    assert partials[0].read_bytes() == PDF_BYTES
    assert stored_candidate(layout)["acquisition_status"] == "not_started"
    findings = GuardianService(layout).check().report["findings"]
    assert any(
        item["code"] == "RKBC-018"
        and "acquisition partial remains" in item["message"]
        for item in findings
    )


def test_acquire_rolls_back_only_owned_source_on_ordinary_pre_replace_failure(tmp_path) -> None:
    def fail_after_publication(phase):
        if phase == "prepared":
            raise RuntimeError("synthetic post-publication failure")

    layout, _, _, service = prepared_service(tmp_path, operation_hook=fail_after_publication)

    with pytest.raises(RuntimeError):
        service.acquire(CANDIDATE_ID, provider="europe-pmc", actor="user")

    assert not list(layout.local_inbox.iterdir())
    assert stored_candidate(layout)["acquisition_status"] == "not_started"


def test_acquire_crash_after_publication_leaves_guardian_visible_orphan(tmp_path) -> None:
    class Crash(BaseException):
        pass

    def crash(phase):
        if phase == "prepared":
            raise Crash()

    layout, _, _, service = prepared_service(tmp_path, operation_hook=crash)

    with pytest.raises(Crash):
        service.acquire(CANDIDATE_ID, provider="europe-pmc", actor="user")

    assert (layout.local_inbox / f"{CANDIDATE_ID}.pdf").read_bytes() == PDF_BYTES
    assert stored_candidate(layout)["acquisition_status"] == "not_started"
    findings = GuardianService(layout).check().report["findings"]
    assert sum(item["code"] == "RKBC-018" for item in findings) >= 2


def test_acquire_crash_after_candidate_replace_keeps_receipt_and_source(tmp_path) -> None:
    class Crash(BaseException):
        pass

    def crash(phase):
        if phase == "target_replaced":
            raise Crash()

    layout, _, _, service = prepared_service(tmp_path, operation_hook=crash)

    with pytest.raises(Crash):
        service.acquire(CANDIDATE_ID, provider="europe-pmc", actor="user")

    assert (layout.local_inbox / f"{CANDIDATE_ID}.pdf").read_bytes() == PDF_BYTES
    assert stored_candidate(layout)["acquisition_status"] == "acquired"
    findings = GuardianService(layout).check().report["findings"]
    assert any(item["code"] == "RKBC-018" for item in findings)
    assert not any(
        item["code"] == "RKBC-009" and item["record_ref"] == CANDIDATE_ID
        for item in findings
    )


def test_acquire_does_not_delete_racing_preexisting_final(tmp_path) -> None:
    final_holder = {}

    def create_racing_final(path, candidate_id):
        final = path.parent / f"{candidate_id}.pdf"
        final.write_bytes(b"racing pre-existing bytes")
        final_holder["path"] = final

    layout, _, _, service = prepared_service(tmp_path, pdf_preflight=create_racing_final)

    with pytest.raises(ResearchKBError) as error:
        service.acquire(CANDIDATE_ID, provider="europe-pmc", actor="user")

    assert error.value.diagnostic.code == "RKBC-017"
    assert final_holder["path"].read_bytes() == b"racing pre-existing bytes"
    assert stored_candidate(layout)["acquisition_status"] == "not_started"
    assert not list(layout.local_inbox.glob(".research-kb-acquire-*.part.pdf"))


def test_later_candidate_selection_context_preserves_acquisition_receipt(tmp_path) -> None:
    layout, _, _, service = prepared_service(tmp_path)
    service.acquire(CANDIDATE_ID, provider="europe-pmc", actor="user")
    before = deepcopy(stored_candidate(layout)["acquisition_receipt"])
    report = discovery_report(date_from="2026-07-13")

    result = DiscoveryCandidateService(layout).select(
        selection_request(report),
        actor="user",
    )

    assert result.updated_candidate_ids == (CANDIDATE_ID,)
    assert stored_candidate(layout)["acquisition_receipt"] == before


def test_acquired_candidate_rejects_changed_live_asset_without_overwrite(tmp_path) -> None:
    layout, resolver, _, service = prepared_service(tmp_path)
    service.acquire(CANDIDATE_ID, provider="europe-pmc", actor="user")
    resolver.pmcid = "PMC7654321"
    final = layout.local_inbox / f"{CANDIDATE_ID}.pdf"
    before = final.read_bytes()

    with pytest.raises(ResearchKBError) as error:
        service.acquire(CANDIDATE_ID, provider="europe-pmc", actor="user")

    assert error.value.diagnostic.code == "RKBC-017"
    assert final.read_bytes() == before


def test_concurrent_acquisition_publishes_at_most_one_source_and_receipt(tmp_path) -> None:
    layout = make_runtime_workspace(
        tmp_path,
        local_inbox="./sources/inbox",
        create_local_inbox=True,
    )
    DiscoveryCandidateService(
        layout,
        id_allocator=lambda namespace: CANDIDATE_ID,
    ).select(selection_request(), actor="user")
    barrier = threading.Barrier(2)

    def run_one():
        service = DiscoveryAcquisitionService(
            layout,
            resolver_registry=DiscoveryResolverRegistry([FakeResolver()]),
            transport_registry=DiscoveryAcquisitionTransportRegistry([FakeTransport()]),
            pdf_preflight=lambda path, candidate_id: barrier.wait(timeout=10),
        )
        try:
            return service.acquire(
                CANDIDATE_ID,
                provider="europe-pmc",
                actor="user",
            )
        except ResearchKBError as error:
            return error.diagnostic.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: run_one(), range(2)))

    reports = [item for item in outcomes if isinstance(item, dict)]
    errors = [item for item in outcomes if isinstance(item, str)]
    assert len(reports) == 1
    assert reports[0]["result"] == "acquired"
    assert errors == ["RKBC-017"]
    assert stored_candidate(layout)["acquisition_status"] == "acquired"
    assert (layout.local_inbox / f"{CANDIDATE_ID}.pdf").read_bytes() == PDF_BYTES
    assert not list(layout.local_inbox.glob(".research-kb-acquire-*.part.pdf"))

    rerun = DiscoveryAcquisitionService(
        layout,
        resolver_registry=DiscoveryResolverRegistry([FakeResolver()]),
        transport_registry=DiscoveryAcquisitionTransportRegistry([FakeTransport()]),
        pdf_preflight=lambda path, candidate_id: None,
    ).acquire(CANDIDATE_ID, provider="europe-pmc", actor="user")
    assert rerun["result"] == "no_change"
