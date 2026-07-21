from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping
from typing import Any

from research_kb.discovery.resolution import DiscoveryResolver, ProviderAssetRef, ProviderResolution
from research_kb.errors import (
    DISCOVERY_CONNECTOR_ERROR,
    DISCOVERY_OUTPUT_INVALID,
    DUPLICATE_ID,
    Diagnostic,
    ResearchKBError,
)
from research_kb.services.discovery_candidate import DiscoveryCandidateService
from research_kb.storage.json_io import serialize_json
from research_kb.workspace import WorkspaceLayout


RESOLVER_ID = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
API_VERSION = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
PMC_ID = re.compile(r"^PMC[0-9]+$")
RESOLUTION_STATUSES = {
    "auto_acquisition_eligible",
    "manual_review_required",
    "institutional_browser_required",
    "no_supported_oa_route",
}
ACCESS_BASES = {
    "repository_open_access",
    "public_free_to_read",
    "institutional",
    "unknown",
}
LICENSE_OBSERVATIONS = {
    "provider_oa_policy_no_license_text",
    "not_observed",
}
MANUAL_REASONS = {
    "multiple_assets",
    "ambiguous_access",
    "subscription_only",
    "no_pdf_route",
    "no_matching_record",
}


class DiscoveryResolverRegistry:
    def __init__(self, resolvers: Iterable[DiscoveryResolver] = ()):
        self._resolvers: dict[str, DiscoveryResolver] = {}
        for resolver in resolvers:
            resolver_id = _resolver_id(resolver)
            if resolver_id in self._resolvers:
                raise ResearchKBError(
                    Diagnostic(
                        DUPLICATE_ID,
                        "discovery-resolver",
                        resolver_id,
                        "/resolver_id",
                        "duplicate discovery resolver ID",
                    )
                )
            self._resolvers[resolver_id] = resolver

    def require(self, resolver_id: str) -> DiscoveryResolver:
        resolver = self._resolvers.get(resolver_id)
        if resolver is None:
            raise _connector_error("discovery resolver is not explicitly registered")
        return resolver


class DiscoveryResolutionService:
    def __init__(self, layout: WorkspaceLayout, registry: DiscoveryResolverRegistry):
        self.layout = layout
        self.registry = registry

    def resolve(self, candidate_id: str, *, provider: str) -> dict[str, Any]:
        candidate = DiscoveryCandidateService(self.layout).show(candidate_id)["candidate"]
        provider_sources = [
            item for item in candidate["discovery_sources"] if item["provider"] == provider
        ]
        if not provider_sources:
            raise _connector_error("candidate does not contain the requested provider identity")
        resolver = self.registry.require(provider)
        try:
            resolution = resolver.resolve(candidate)
        except ResearchKBError:
            raise
        except Exception as error:
            raise _connector_error("discovery resolver failed") from error
        _validate_resolution(resolution, provider=provider, candidate=candidate)

        asset = (
            resolution.provider_asset_ref.to_dict()
            if resolution.provider_asset_ref is not None
            else None
        )
        identity = {
            "candidate_id": candidate["candidate_id"],
            "result_key": candidate["result_key"],
            "provider": provider,
            "lookup_identity": dict(resolution.lookup_identity),
            "provider_asset_ref": asset,
            "resolution_status": resolution.resolution_status,
            "provider_api_version": resolution.provider_api_version,
        }
        digest = hashlib.sha256(serialize_json(identity)).hexdigest()
        return {
            "status": "success",
            "interface_version": "1.0",
            "candidate_id": candidate["candidate_id"],
            "result_key": candidate["result_key"],
            "provider": provider,
            "provider_api_version": resolution.provider_api_version,
            "resolution_context_id": f"resolution_sha256_{digest}",
            "resolution_status": resolution.resolution_status,
            "provider_asset_ref": asset,
            "access_basis": resolution.access_basis,
            "license_observation": resolution.license_observation,
            "manual_reason": resolution.manual_reason,
            "persistent_writes": 0,
        }


def _resolver_id(resolver: DiscoveryResolver) -> str:
    resolver_id = getattr(resolver, "resolver_id", None)
    if not isinstance(resolver_id, str) or not RESOLVER_ID.fullmatch(resolver_id):
        raise _connector_error("discovery resolver ID is invalid")
    if not isinstance(getattr(resolver, "network_required", None), bool):
        raise _connector_error("discovery resolver network declaration is invalid")
    if not isinstance(resolver, DiscoveryResolver):
        raise _connector_error("discovery resolver does not implement the required protocol")
    return resolver_id


def _validate_resolution(
    value: ProviderResolution,
    *,
    provider: str,
    candidate: Mapping[str, Any],
) -> None:
    if not isinstance(value, ProviderResolution) or value.provider != provider:
        raise _output_error("discovery resolver returned an invalid provider result")
    if not API_VERSION.fullmatch(value.provider_api_version):
        raise _output_error("discovery resolver returned an invalid provider API version")
    if value.resolution_status not in RESOLUTION_STATUSES:
        raise _output_error("discovery resolver returned an invalid resolution status")
    if value.access_basis not in ACCESS_BASES:
        raise _output_error("discovery resolver returned an invalid access basis")
    if value.license_observation not in LICENSE_OBSERVATIONS:
        raise _output_error("discovery resolver returned an invalid license observation")
    if value.manual_reason is not None and value.manual_reason not in MANUAL_REASONS:
        raise _output_error("discovery resolver returned an invalid manual reason")
    expected_lookup = _expected_lookup_identity(candidate, provider)
    if dict(value.lookup_identity) != expected_lookup:
        raise _output_error("discovery resolver lookup identity does not match the candidate")

    asset = value.provider_asset_ref
    expected_policy = {
        "auto_acquisition_eligible": (
            "repository_open_access",
            "provider_oa_policy_no_license_text",
            None,
        ),
        "manual_review_required": (
            value.access_basis,
            value.license_observation,
            value.manual_reason,
        ),
        "institutional_browser_required": (
            "institutional",
            "not_observed",
            "subscription_only",
        ),
        "no_supported_oa_route": (
            "unknown",
            "not_observed",
            value.manual_reason,
        ),
    }[value.resolution_status]
    if (value.access_basis, value.license_observation, value.manual_reason) != expected_policy:
        raise _output_error("discovery resolver returned an inconsistent access policy")
    if value.resolution_status == "manual_review_required":
        valid_manual = (
            value.manual_reason == "multiple_assets"
            and value.access_basis == "repository_open_access"
            and value.license_observation == "provider_oa_policy_no_license_text"
        ) or (
            value.manual_reason == "ambiguous_access"
            and value.access_basis == "public_free_to_read"
            and value.license_observation == "not_observed"
        )
        if not valid_manual:
            raise _output_error("manual resolution returned an inconsistent access policy")
    if value.resolution_status == "no_supported_oa_route" and value.manual_reason not in {
        "no_pdf_route",
        "no_matching_record",
    }:
        raise _output_error("unsupported resolution returned an inconsistent reason")
    if value.resolution_status == "auto_acquisition_eligible":
        if asset is None:
            raise _output_error("eligible resolution must contain one unambiguous provider asset")
    elif asset is not None:
        raise _output_error("non-eligible resolution must not contain a provider asset")
    if asset is not None:
        _validate_asset(asset, provider)


def _expected_lookup_identity(candidate: Mapping[str, Any], provider: str) -> dict[str, str]:
    doi = candidate["doi"]
    if doi is not None:
        return {"kind": "doi", "doi": doi}
    sources = sorted(
        (
            (item["source"], item["record_id"])
            for item in candidate["discovery_sources"]
            if item["provider"] == provider
        )
    )
    if not sources:
        raise _connector_error("candidate does not contain a resolvable provider identity")
    source, record_id = sources[0]
    return {"kind": "source", "source": source, "record_id": record_id}


def _validate_asset(asset: ProviderAssetRef, provider: str) -> None:
    if not isinstance(asset, ProviderAssetRef):
        raise _output_error("provider asset reference is invalid")
    if (
        asset.provider != provider
        or not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", asset.source)
        or not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", asset.record_id)
        or not PMC_ID.fullmatch(asset.pmcid)
        or asset.asset_kind != "pdf"
        or asset.route != "europe-pmc-pdf-v1"
    ):
        raise _output_error("provider asset reference fields are invalid")


def _connector_error(message: str) -> ResearchKBError:
    return ResearchKBError(
        Diagnostic(DISCOVERY_CONNECTOR_ERROR, "discovery-resolver", None, "", message)
    )


def _output_error(message: str) -> ResearchKBError:
    return ResearchKBError(
        Diagnostic(DISCOVERY_OUTPUT_INVALID, "discovery-resolution", None, "", message)
    )


__all__ = ["DiscoveryResolutionService", "DiscoveryResolverRegistry"]
