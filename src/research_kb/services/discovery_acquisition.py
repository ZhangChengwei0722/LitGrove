from __future__ import annotations

import os
import re
import stat
from collections.abc import Callable, Iterable, Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any

from research_kb.acquisition_paths import AcquisitionDestination, acquisition_destination
from research_kb.bundle import load_workspace_entries, records_of_kind, validate_workspace_entries
from research_kb.contracts.validator import validate_record
from research_kb.discovery.acquisition import (
    DiscoveryAcquisitionTransport,
    DownloadedAsset,
    FileIdentity,
)
from research_kb.discovery.resolution import ProviderAssetRef
from research_kb.errors import (
    DISCOVERY_CONNECTOR_ERROR,
    DISCOVERY_OUTPUT_INVALID,
    DUPLICATE_ID,
    GROUNDING_MISMATCH,
    INVALID_AUTHORITY,
    WRITE_CONFLICT,
    Diagnostic,
    ResearchKBError,
)
from research_kb.identifiers import Namespace, validate_id
from research_kb.parse.pdfplumber_adapter import PdfPlumberAdapter
from research_kb.process_events import timestamp
from research_kb.services.discovery_candidate import DiscoveryCandidateService
from research_kb.services.discovery_resolution import (
    DiscoveryResolutionService,
    DiscoveryResolverRegistry,
)
from research_kb.storage.json_io import file_sha256, read_jsonl, serialize_jsonl
from research_kb.storage.transactions import TransactionManager
from research_kb.workspace import WorkspaceLayout


PdfPreflight = Callable[[Path, str], None]
OperationHook = Callable[[str], None]
TRANSPORT_ID = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
MAX_PDF_BYTES = 64 * 1024 * 1024
PDF_SIGNATURE = bytes((37, 80, 68, 70, 45))


class DiscoveryAcquisitionTransportRegistry:
    def __init__(self, transports: Iterable[DiscoveryAcquisitionTransport] = ()):
        self._transports: dict[str, DiscoveryAcquisitionTransport] = {}
        for transport in transports:
            transport_id = _transport_id(transport)
            if transport_id in self._transports:
                raise ResearchKBError(
                    Diagnostic(
                        DUPLICATE_ID,
                        "discovery-acquisition-transport",
                        transport_id,
                        "/transport_id",
                        "duplicate discovery acquisition transport ID",
                    )
                )
            self._transports[transport_id] = transport

    def require(self, transport_id: str) -> DiscoveryAcquisitionTransport:
        transport = self._transports.get(transport_id)
        if transport is None:
            raise ResearchKBError(
                Diagnostic(
                    DISCOVERY_CONNECTOR_ERROR,
                    "discovery-acquisition-transport",
                    transport_id,
                    "/transport_id",
                    "discovery acquisition transport is not explicitly registered",
                )
            )
        return transport


class DiscoveryAcquisitionService:
    def __init__(
        self,
        layout: WorkspaceLayout,
        *,
        resolver_registry: DiscoveryResolverRegistry,
        transport_registry: DiscoveryAcquisitionTransportRegistry,
        transaction_manager: TransactionManager | None = None,
        pdf_preflight: PdfPreflight | None = None,
        operation_hook: OperationHook | None = None,
    ):
        self.layout = layout
        self.resolvers = resolver_registry
        self.transports = transport_registry
        self.transactions = transaction_manager or TransactionManager(layout)
        self.pdf_preflight = pdf_preflight or _default_pdf_preflight
        self.operation_hook = operation_hook

    def acquire(
        self,
        candidate_id: str,
        *,
        provider: str,
        actor: str,
    ) -> dict[str, Any]:
        if actor != "user":
            raise ResearchKBError(
                Diagnostic(
                    INVALID_AUTHORITY,
                    "discovery-acquisition",
                    candidate_id,
                    "/actor",
                    "discovery acquisition requires exact user authority",
                )
            )
        candidate = DiscoveryCandidateService(self.layout).show(candidate_id)["candidate"]
        destination = acquisition_destination(self.layout, candidate_id)
        if candidate["acquisition_status"] == "not_started" and os.path.lexists(
            destination.final_path
        ):
            raise _write_conflict(candidate_id, "acquisition target already exists without a receipt")

        resolution = DiscoveryResolutionService(
            self.layout,
            self.resolvers,
        ).resolve(candidate_id, provider=provider)
        if resolution["resolution_status"] != "auto_acquisition_eligible":
            raise ResearchKBError(
                Diagnostic(
                    DISCOVERY_OUTPUT_INVALID,
                    "discovery-acquisition",
                    candidate_id,
                    "/resolution_status",
                    "candidate does not have one auto-acquisition-eligible OA asset",
                )
            )
        asset_ref = _asset_ref(resolution["provider_asset_ref"])
        if candidate["acquisition_status"] == "acquired":
            return self._no_change_report(
                candidate,
                destination=destination,
                resolution=resolution,
                asset_ref=asset_ref,
            )

        transport = self.transports.require(provider)
        event_id = validate_id(
            self.transactions.event_id_factory(),
            Namespace.PROCESS_EVENT,
        )
        temporary = destination.inbox / f".research-kb-acquire-{event_id}.part.pdf"
        downloaded = transport.download(asset_ref, temporary)
        created_source: dict[str, FileIdentity | None] = {"identity": None}
        receipt: dict[str, Any] | None = None
        try:
            _verify_download(temporary, downloaded, candidate_id)
            self.pdf_preflight(temporary, candidate_id)
            _verify_download(temporary, downloaded, candidate_id)
            acquired_at = timestamp(self.transactions.clock)
            receipt = _receipt(
                resolution,
                destination=destination,
                downloaded=downloaded,
                acquired_at=acquired_at,
            )
            transaction = self._promote_receipt(
                candidate,
                receipt=receipt,
                destination=destination,
                temporary=temporary,
                downloaded=downloaded,
                event_id=event_id,
                created_source_setter=lambda value: created_source.__setitem__(
                    "identity",
                    value,
                ),
            )
        except Exception:
            if isinstance(downloaded, DownloadedAsset):
                _safe_unlink_owned(temporary, downloaded.file_identity)
            if created_source["identity"] is not None:
                candidate_state = _receipt_state(
                    self.layout,
                    candidate_id,
                    receipt,
                )
                if candidate_state is False:
                    _safe_unlink_owned(
                        destination.final_path,
                        created_source["identity"],
                    )
            raise

        assert receipt is not None
        return _success_report(
            candidate_id,
            resolution=resolution,
            receipt=receipt,
            result="acquired",
            persistent_writes=2,
            event_id=transaction.event_id,
        )

    def _promote_receipt(
        self,
        candidate: Mapping[str, Any],
        *,
        receipt: dict[str, Any],
        destination: AcquisitionDestination,
        temporary: Path,
        downloaded: DownloadedAsset,
        event_id: str,
        created_source_setter: Callable[[FileIdentity], None],
    ):
        entries = load_workspace_entries(self.layout)
        validate_workspace_entries(entries)
        existing_records = records_of_kind(entries, "discovery-candidate")
        current = next(
            (item for item in existing_records if item["candidate_id"] == candidate["candidate_id"]),
            None,
        )
        if current != dict(candidate):
            raise _write_conflict(candidate["candidate_id"], "candidate changed during acquisition")
        updated = deepcopy(current)
        updated["acquisition_status"] = "acquired"
        updated["acquisition_receipt"] = deepcopy(receipt)
        updated["updated_at"] = receipt["acquired_at"]
        diagnostics = validate_record("discovery-candidate", updated, actor="stored")
        if diagnostics:
            raise ResearchKBError(diagnostics[0])
        proposed = [
            updated if item["candidate_id"] == candidate["candidate_id"] else item
            for item in existing_records
        ]
        proposed.sort(key=lambda item: item["candidate_id"])
        target = self.layout.discovery_candidates_path
        before_sha256 = file_sha256(target)

        def validate_temp(path: Path) -> None:
            temporary_records = read_jsonl(
                path,
                record_kind="discovery-candidate",
                missing_ok=False,
                id_field="candidate_id",
            )
            temporary_entries = load_workspace_entries(
                self.layout,
                overrides={
                    target: [
                        ("discovery-candidate", item)
                        for item in temporary_records
                    ]
                },
            )
            validate_workspace_entries(temporary_entries)

        def phase_hook(phase: str) -> None:
            if phase == "prepared":
                identity = _publish_source(
                    temporary,
                    destination.final_path,
                    downloaded,
                    candidate["candidate_id"],
                )
                created_source_setter(identity)
            if self.operation_hook is not None:
                self.operation_hook(phase)

        def post_replace_validator() -> None:
            _verify_final_source(
                destination.final_path,
                downloaded,
                candidate["candidate_id"],
            )

        return self.transactions.promote_bytes(
            target=target,
            content=serialize_jsonl(proposed),
            target_store="discovery_candidates",
            operation="discovery_acquire",
            actor="user",
            input_refs=[candidate["candidate_id"]],
            output_refs=[candidate["candidate_id"]],
            validator=validate_temp,
            post_replace_validator=post_replace_validator,
            expected_before_sha256=before_sha256,
            phase_hook=phase_hook,
            event_id=event_id,
        )

    def _no_change_report(
        self,
        candidate: Mapping[str, Any],
        *,
        destination: AcquisitionDestination,
        resolution: Mapping[str, Any],
        asset_ref: ProviderAssetRef,
    ) -> dict[str, Any]:
        receipt = candidate["acquisition_receipt"]
        if receipt["provider_asset_ref"] != asset_ref.to_dict():
            raise _write_conflict(candidate["candidate_id"], "live OA asset differs from receipt")
        if receipt["source_ref"] != destination.source_ref.to_dict():
            raise _source_mismatch(candidate["candidate_id"], "receipt source is not the deterministic inbox target")
        _verify_receipt_source(destination.final_path, receipt, candidate["candidate_id"])
        return _success_report(
            candidate["candidate_id"],
            resolution=resolution,
            receipt=receipt,
            result="no_change",
            persistent_writes=0,
            event_id=None,
        )


def _transport_id(transport: DiscoveryAcquisitionTransport) -> str:
    transport_id = getattr(transport, "transport_id", None)
    if not isinstance(transport_id, str) or not TRANSPORT_ID.fullmatch(transport_id):
        raise ResearchKBError(
            Diagnostic(
                DISCOVERY_CONNECTOR_ERROR,
                "discovery-acquisition-transport",
                None,
                "/transport_id",
                "discovery acquisition transport ID is invalid",
            )
        )
    if not isinstance(getattr(transport, "network_required", None), bool):
        raise ResearchKBError(
            Diagnostic(
                DISCOVERY_CONNECTOR_ERROR,
                "discovery-acquisition-transport",
                transport_id,
                "/network_required",
                "discovery acquisition transport network declaration is invalid",
            )
        )
    if not isinstance(transport, DiscoveryAcquisitionTransport):
        raise ResearchKBError(
            Diagnostic(
                DISCOVERY_CONNECTOR_ERROR,
                "discovery-acquisition-transport",
                transport_id,
                "",
                "discovery acquisition transport does not implement the required protocol",
            )
        )
    return transport_id


def _asset_ref(value: Any) -> ProviderAssetRef:
    if not isinstance(value, Mapping):
        raise ResearchKBError(
            Diagnostic(
                DISCOVERY_OUTPUT_INVALID,
                "discovery-acquisition",
                None,
                "/provider_asset_ref",
                "eligible resolution does not contain a provider asset",
            )
        )
    return ProviderAssetRef(
        provider=value["provider"],
        source=value["source"],
        record_id=value["record_id"],
        pmcid=value["pmcid"],
        asset_kind=value["asset_kind"],
        route=value["route"],
    )


def _receipt(
    resolution: Mapping[str, Any],
    *,
    destination: AcquisitionDestination,
    downloaded: DownloadedAsset,
    acquired_at: str,
) -> dict[str, Any]:
    return {
        "provider": resolution["provider"],
        "provider_api_version": resolution["provider_api_version"],
        "provider_asset_ref": deepcopy(resolution["provider_asset_ref"]),
        "resolution_context_id": resolution["resolution_context_id"],
        "access_basis": resolution["access_basis"],
        "source_ref": destination.source_ref.to_dict(),
        "source_fingerprint": {
            "algorithm": "sha256",
            "value": downloaded.sha256,
        },
        "content_size_bytes": downloaded.content_size_bytes,
        "content_type": downloaded.content_type,
        "acquired_at": acquired_at,
    }


def _success_report(
    candidate_id: str,
    *,
    resolution: Mapping[str, Any],
    receipt: Mapping[str, Any],
    result: str,
    persistent_writes: int,
    event_id: str | None,
) -> dict[str, Any]:
    return {
        "status": "success",
        "interface_version": "1.0",
        "result": result,
        "candidate_id": candidate_id,
        "provider": resolution["provider"],
        "resolution_context_id": resolution["resolution_context_id"],
        "source_ref": deepcopy(receipt["source_ref"]),
        "source_fingerprint": deepcopy(receipt["source_fingerprint"]),
        "content_size_bytes": receipt["content_size_bytes"],
        "content_type": receipt["content_type"],
        "persistent_writes": persistent_writes,
        "event_id": event_id,
    }


def _default_pdf_preflight(path: Path, candidate_id: str) -> None:
    PdfPlumberAdapter().parse(
        path,
        paper_id=candidate_id,
        parse_run_id="acquisition-preflight",
    )


def _verify_download(path: Path, value: DownloadedAsset, candidate_id: str) -> None:
    if (
        not isinstance(value, DownloadedAsset)
        or value.content_type != "application/pdf"
        or not 1 <= value.content_size_bytes <= MAX_PDF_BYTES
        or not SHA256.fullmatch(value.sha256)
        or value.file_identity.size != value.content_size_bytes
        or value.file_identity.sha256 != value.sha256
    ):
        raise ResearchKBError(
            Diagnostic(
                DISCOVERY_OUTPUT_INVALID,
                "discovery-acquisition",
                candidate_id,
                "",
                "acquisition transport returned an invalid download result",
            )
        )
    _verify_file(path, value.file_identity, candidate_id)


def _publish_source(
    temporary: Path,
    final_path: Path,
    downloaded: DownloadedAsset,
    candidate_id: str,
) -> FileIdentity:
    _verify_download(temporary, downloaded, candidate_id)
    if os.path.lexists(final_path):
        raise _write_conflict(candidate_id, "acquisition target appeared before publication")
    try:
        os.link(temporary, final_path)
    except FileExistsError as error:
        raise _write_conflict(candidate_id, "acquisition target appeared before publication") from error
    except OSError as error:
        raise _write_conflict(candidate_id, "acquisition source could not be published create-only") from error
    identity = _published_identity(final_path, downloaded)
    _safe_unlink_owned(temporary, downloaded.file_identity)
    if os.path.lexists(temporary):
        raise _write_conflict(candidate_id, "operation-owned acquisition partial could not be removed")
    return identity


def _published_identity(path: Path, downloaded: DownloadedAsset) -> FileIdentity:
    current = path.stat()
    if (
        current.st_dev != downloaded.file_identity.device
        or current.st_ino != downloaded.file_identity.inode
        or current.st_size != downloaded.content_size_bytes
    ):
        raise OSError("published source does not match the operation-owned partial")
    return FileIdentity(
        device=current.st_dev,
        inode=current.st_ino,
        size=downloaded.content_size_bytes,
        sha256=downloaded.sha256,
    )


def _verify_final_source(
    path: Path,
    downloaded: DownloadedAsset,
    candidate_id: str,
) -> None:
    _verify_file(path, downloaded.file_identity, candidate_id)


def _verify_receipt_source(path: Path, receipt: Mapping[str, Any], candidate_id: str) -> None:
    try:
        current = os.lstat(path)
    except OSError as error:
        raise _source_mismatch(candidate_id, "acquired source is missing") from error
    identity = FileIdentity(
        device=current.st_dev,
        inode=current.st_ino,
        size=receipt["content_size_bytes"],
        sha256=receipt["source_fingerprint"]["value"],
    )
    _verify_file(path, identity, candidate_id)


def _verify_file(path: Path, identity: FileIdentity, candidate_id: str) -> None:
    try:
        current = os.lstat(path)
        if not stat.S_ISREG(current.st_mode) or path.is_symlink():
            raise OSError("source is not a regular file")
        if (
            current.st_dev != identity.device
            or current.st_ino != identity.inode
            or current.st_size != identity.size
            or file_sha256(path) != identity.sha256
        ):
            raise OSError("source identity changed")
        with path.open("rb") as stream:
            if stream.read(len(PDF_SIGNATURE)) != PDF_SIGNATURE:
                raise OSError("source signature changed")
    except OSError as error:
        raise _source_mismatch(candidate_id, "acquisition source identity is missing or changed") from error


def _safe_unlink_owned(path: Path, identity: FileIdentity) -> None:
    try:
        current = os.lstat(path)
        if (
            current.st_dev != identity.device
            or current.st_ino != identity.inode
            or current.st_size != identity.size
            or file_sha256(path) != identity.sha256
        ):
            return
        path.unlink()
    except OSError:
        return


def _receipt_state(
    layout: WorkspaceLayout,
    candidate_id: str,
    receipt: Mapping[str, Any] | None,
) -> bool | None:
    if receipt is None:
        return False
    try:
        candidates = read_jsonl(
            layout.discovery_candidates_path,
            record_kind="discovery-candidate",
            id_field="candidate_id",
        )
    except (ResearchKBError, OSError):
        return None
    candidate = next(
        (item for item in candidates if item["candidate_id"] == candidate_id),
        None,
    )
    if candidate is None:
        return None
    return (
        candidate.get("acquisition_status") == "acquired"
        and candidate.get("acquisition_receipt") == dict(receipt)
    )


def _write_conflict(candidate_id: str, message: str) -> ResearchKBError:
    return ResearchKBError(
        Diagnostic(WRITE_CONFLICT, "discovery-acquisition", candidate_id, "", message)
    )


def _source_mismatch(candidate_id: str, message: str) -> ResearchKBError:
    return ResearchKBError(
        Diagnostic(GROUNDING_MISMATCH, "discovery-candidate", candidate_id, "", message)
    )


__all__ = ["DiscoveryAcquisitionService", "DiscoveryAcquisitionTransportRegistry"]
