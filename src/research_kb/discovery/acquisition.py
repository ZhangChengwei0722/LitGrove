from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from research_kb.discovery.resolution import ProviderAssetRef


@dataclass(frozen=True, slots=True)
class FileIdentity:
    device: int
    inode: int
    size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class DownloadedAsset:
    content_type: str
    content_size_bytes: int
    sha256: str
    file_identity: FileIdentity


@runtime_checkable
class DiscoveryAcquisitionTransport(Protocol):
    transport_id: str
    network_required: bool

    def download(
        self,
        asset_ref: ProviderAssetRef,
        target: Path,
    ) -> DownloadedAsset:
        ...


__all__ = [
    "DiscoveryAcquisitionTransport",
    "DownloadedAsset",
    "FileIdentity",
]
