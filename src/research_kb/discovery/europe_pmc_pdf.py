from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from research_kb import __version__
from research_kb.discovery.acquisition import DownloadedAsset, FileIdentity
from research_kb.discovery.resolution import ProviderAssetRef
from research_kb.errors import (
    DISCOVERY_CONNECTOR_ERROR,
    DISCOVERY_OUTPUT_INVALID,
    INPUT_TOO_LARGE,
    WRITE_CONFLICT,
    Diagnostic,
    ResearchKBError,
)


EUROPE_PMC_PDF_ENDPOINT = "https://europepmc.org/api/getPdf"
EUROPE_PMC_PDF_TIMEOUT_SECONDS = 30
EUROPE_PMC_PDF_RESPONSE_LIMIT = 64 * 1024 * 1024
EUROPE_PMC_PDF_CHUNK_SIZE = 64 * 1024
PDF_SIGNATURE = bytes((37, 80, 68, 70, 45))


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise HTTPError(newurl, code, "redirect rejected", headers, fp)


class EuropePmcPdfTransport:
    transport_id = "europe-pmc"
    network_required = True

    def __init__(
        self,
        *,
        opener=None,
        response_limit: int = EUROPE_PMC_PDF_RESPONSE_LIMIT,
        chunk_size: int = EUROPE_PMC_PDF_CHUNK_SIZE,
    ):
        self.opener = build_opener(_RejectRedirects()) if opener is None else opener
        self.response_limit = response_limit
        self.chunk_size = chunk_size

    def download(
        self,
        asset_ref: ProviderAssetRef,
        target: Path,
    ) -> DownloadedAsset:
        _validate_asset_ref(asset_ref)
        if os.path.lexists(target):
            raise _write_conflict("acquisition partial target already exists")
        url = EUROPE_PMC_PDF_ENDPOINT + "?" + urlencode({"pmcid": asset_ref.pmcid})
        request = Request(
            url,
            headers={
                "Accept": "application/pdf",
                "User-Agent": f"research-kb-core/{__version__}",
            },
            method="GET",
        )
        try:
            response = self.opener.open(
                request,
                timeout=EUROPE_PMC_PDF_TIMEOUT_SECONDS,
            )
        except (HTTPError, URLError, TimeoutError, OSError) as error:
            raise _connector_error("Europe PMC PDF request failed") from error

        owned_identity: FileIdentity | None = None
        try:
            with response:
                if getattr(response, "status", None) != 200:
                    raise _connector_error("Europe PMC PDF request returned an unsuccessful status")
                if response.geturl() != url:
                    raise _connector_error("Europe PMC PDF request changed endpoint")
                content_type = str(response.headers.get("Content-Type", ""))
                normalized_type = content_type.split(";", 1)[0].strip().casefold()
                if normalized_type != "application/pdf":
                    raise _output_error("Europe PMC PDF response has an invalid media type")
                _validate_content_length(
                    response.headers.get("Content-Length"),
                    self.response_limit,
                )

                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
                flags |= getattr(os, "O_BINARY", 0)
                try:
                    descriptor = os.open(target, flags, 0o600)
                except FileExistsError as error:
                    raise _write_conflict("acquisition partial target already exists") from error
                try:
                    with os.fdopen(descriptor, "wb") as stream:
                        opened = os.fstat(stream.fileno())
                        digest = hashlib.sha256()
                        total = 0
                        prefix = bytearray()
                        owned_identity = FileIdentity(
                            device=opened.st_dev,
                            inode=opened.st_ino,
                            size=0,
                            sha256=digest.hexdigest(),
                        )
                        while True:
                            chunk = response.read(self.chunk_size)
                            if not chunk:
                                break
                            if not isinstance(chunk, bytes):
                                raise _output_error("Europe PMC PDF response returned non-byte content")
                            next_total = total + len(chunk)
                            if next_total > self.response_limit:
                                raise _too_large()
                            written = stream.write(chunk)
                            if written != len(chunk):
                                raise OSError("Europe PMC PDF response could not be fully stored")
                            if len(prefix) < len(PDF_SIGNATURE):
                                prefix.extend(chunk[: len(PDF_SIGNATURE) - len(prefix)])
                            digest.update(chunk)
                            total = next_total
                            owned_identity = FileIdentity(
                                device=opened.st_dev,
                                inode=opened.st_ino,
                                size=total,
                                sha256=digest.hexdigest(),
                            )
                        stream.flush()
                        os.fsync(stream.fileno())
                except BaseException:
                    if owned_identity is not None:
                        _unlink_owned(target, owned_identity)
                    raise
        except ResearchKBError:
            raise
        except (HTTPError, URLError, TimeoutError, OSError) as error:
            if owned_identity is not None:
                _unlink_owned(target, owned_identity)
            raise _connector_error("Europe PMC PDF response could not be stored") from error

        if bytes(prefix) != PDF_SIGNATURE:
            if owned_identity is not None:
                _unlink_owned(target, owned_identity)
            raise _output_error("Europe PMC PDF response does not have a PDF signature")
        value = digest.hexdigest()
        stored = target.stat()
        identity = FileIdentity(
            device=stored.st_dev,
            inode=stored.st_ino,
            size=total,
            sha256=value,
        )
        return DownloadedAsset(
            content_type="application/pdf",
            content_size_bytes=total,
            sha256=value,
            file_identity=identity,
        )


def _validate_asset_ref(asset_ref: ProviderAssetRef) -> None:
    if (
        not isinstance(asset_ref, ProviderAssetRef)
        or asset_ref.provider != "europe-pmc"
        or re.fullmatch(r"PMC[0-9]+", asset_ref.pmcid) is None
        or asset_ref.asset_kind != "pdf"
        or asset_ref.route != "europe-pmc-pdf-v1"
    ):
        raise _output_error("Europe PMC acquisition asset reference is invalid")


def _validate_content_length(value, maximum: int) -> None:
    if value is None:
        return
    try:
        length = int(value)
    except (TypeError, ValueError) as error:
        raise _output_error("Europe PMC PDF content length is invalid") from error
    if length < 0:
        raise _output_error("Europe PMC PDF content length is invalid")
    if length > maximum:
        raise _too_large()


def _unlink_owned(path: Path, identity: FileIdentity) -> None:
    try:
        current = os.lstat(path)
    except OSError:
        return
    if (
        current.st_dev != identity.device
        or current.st_ino != identity.inode
        or current.st_size != identity.size
    ):
        return
    try:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(EUROPE_PMC_PDF_CHUNK_SIZE), b""):
                digest.update(chunk)
        if digest.hexdigest() != identity.sha256:
            return
        path.unlink()
    except OSError:
        pass


def _connector_error(message: str) -> ResearchKBError:
    return ResearchKBError(
        Diagnostic(DISCOVERY_CONNECTOR_ERROR, "discovery-acquisition", None, "", message)
    )


def _output_error(message: str) -> ResearchKBError:
    return ResearchKBError(
        Diagnostic(DISCOVERY_OUTPUT_INVALID, "discovery-acquisition", None, "", message)
    )


def _write_conflict(message: str) -> ResearchKBError:
    return ResearchKBError(
        Diagnostic(WRITE_CONFLICT, "discovery-acquisition", None, "", message)
    )


def _too_large() -> ResearchKBError:
    return ResearchKBError(
        Diagnostic(
            INPUT_TOO_LARGE,
            "discovery-acquisition",
            None,
            "",
            "Europe PMC PDF response exceeded the 64 MiB limit",
        )
    )


__all__ = ["EUROPE_PMC_PDF_ENDPOINT", "EuropePmcPdfTransport"]
