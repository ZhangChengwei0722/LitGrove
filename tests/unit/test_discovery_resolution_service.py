from __future__ import annotations

from copy import deepcopy

import pytest

from research_kb.discovery.resolution import ProviderAssetRef, ProviderResolution
from research_kb.errors import ResearchKBError
from research_kb.services.discovery_candidate import DiscoveryCandidateService
from research_kb.services.discovery_resolution import (
    DiscoveryResolutionService,
    DiscoveryResolverRegistry,
)
from tests.discovery_candidate_helpers import selection_request
from tests.runtime_helpers import make_runtime_workspace


CANDIDATE_ID = "discovery_a1111111-1111-4111-8111-111111111111"


class FakeResolver:
    resolver_id = "europe-pmc"
    network_required = True

    def __init__(self):
        self.calls = []

    def resolve(self, candidate):
        self.calls.append(deepcopy(candidate))
        return ProviderResolution(
            provider="europe-pmc",
            provider_api_version="synthetic-6.9",
            lookup_identity={"kind": "doi", "doi": candidate["doi"]},
            resolution_status="auto_acquisition_eligible",
            provider_asset_ref=ProviderAssetRef(
                provider="europe-pmc",
                source="MED",
                record_id="SYNTH-DISCOVERY-1",
                pmcid="PMC1234567",
                asset_kind="pdf",
                route="europe-pmc-pdf-v1",
            ),
            access_basis="repository_open_access",
            license_observation="provider_oa_policy_no_license_text",
            manual_reason=None,
        )


def tree_bytes(root):
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def prepared_service(tmp_path):
    layout = make_runtime_workspace(tmp_path)
    DiscoveryCandidateService(
        layout,
        id_allocator=lambda namespace: CANDIDATE_ID,
    ).select(selection_request(), actor="user")
    resolver = FakeResolver()
    service = DiscoveryResolutionService(
        layout,
        DiscoveryResolverRegistry([resolver]),
    )
    return layout, resolver, service


def test_resolution_report_is_deterministic_and_workspace_is_unchanged(tmp_path) -> None:
    layout, resolver, service = prepared_service(tmp_path)
    before = tree_bytes(layout.config.path.parent)

    first = service.resolve(CANDIDATE_ID, provider="europe-pmc")
    second = service.resolve(CANDIDATE_ID, provider="europe-pmc")

    assert first == second
    assert first == {
        "status": "success",
        "interface_version": "1.0",
        "candidate_id": CANDIDATE_ID,
        "result_key": "doi:10.0000/synthetic.discovery",
        "provider": "europe-pmc",
        "provider_api_version": "synthetic-6.9",
        "resolution_context_id": first["resolution_context_id"],
        "resolution_status": "auto_acquisition_eligible",
        "provider_asset_ref": {
            "provider": "europe-pmc",
            "source": "MED",
            "record_id": "SYNTH-DISCOVERY-1",
            "pmcid": "PMC1234567",
            "asset_kind": "pdf",
            "route": "europe-pmc-pdf-v1",
        },
        "access_basis": "repository_open_access",
        "license_observation": "provider_oa_policy_no_license_text",
        "manual_reason": None,
        "persistent_writes": 0,
    }
    assert first["resolution_context_id"].startswith("resolution_sha256_")
    assert len(resolver.calls) == 2
    assert resolver.calls[0]["selection_status"] == "user_selected"
    assert tree_bytes(layout.config.path.parent) == before


def test_resolution_rejects_provider_mismatch_before_resolver_dispatch(tmp_path) -> None:
    layout, resolver, service = prepared_service(tmp_path)

    with pytest.raises(ResearchKBError) as error:
        service.resolve(CANDIDATE_ID, provider="other-provider")

    assert error.value.diagnostic.code == "RKBC-032"
    assert resolver.calls == []


def test_resolution_rejects_missing_candidate(tmp_path) -> None:
    _, _, service = prepared_service(tmp_path)

    with pytest.raises(ResearchKBError) as error:
        service.resolve(
            "discovery_b2222222-2222-4222-8222-222222222222",
            provider="europe-pmc",
        )

    assert error.value.diagnostic.code == "RKBC-005"


def test_resolution_rejects_inconsistent_provider_policy(tmp_path) -> None:
    layout, _, _ = prepared_service(tmp_path)

    class InconsistentResolver(FakeResolver):
        def resolve(self, candidate):
            value = super().resolve(candidate)
            return ProviderResolution(
                provider=value.provider,
                provider_api_version=value.provider_api_version,
                lookup_identity=value.lookup_identity,
                resolution_status=value.resolution_status,
                provider_asset_ref=value.provider_asset_ref,
                access_basis="institutional",
                license_observation=value.license_observation,
                manual_reason=value.manual_reason,
            )

    service = DiscoveryResolutionService(
        layout,
        DiscoveryResolverRegistry([InconsistentResolver()]),
    )
    with pytest.raises(ResearchKBError) as error:
        service.resolve(CANDIDATE_ID, provider="europe-pmc")

    assert error.value.diagnostic.code == "RKBC-033"
