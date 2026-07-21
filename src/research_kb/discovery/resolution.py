from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class ProviderAssetRef:
    provider: str
    source: str
    record_id: str
    pmcid: str
    asset_kind: str
    route: str

    def to_dict(self) -> dict[str, str]:
        return {
            "provider": self.provider,
            "source": self.source,
            "record_id": self.record_id,
            "pmcid": self.pmcid,
            "asset_kind": self.asset_kind,
            "route": self.route,
        }


@dataclass(frozen=True, slots=True)
class ProviderResolution:
    provider: str
    provider_api_version: str
    lookup_identity: Mapping[str, str]
    resolution_status: str
    provider_asset_ref: ProviderAssetRef | None
    access_basis: str
    license_observation: str
    manual_reason: str | None


@runtime_checkable
class DiscoveryResolver(Protocol):
    resolver_id: str
    network_required: bool

    def resolve(self, candidate: Mapping[str, Any]) -> ProviderResolution:
        ...


__all__ = ["DiscoveryResolver", "ProviderAssetRef", "ProviderResolution"]
