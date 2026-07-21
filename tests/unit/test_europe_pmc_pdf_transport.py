from __future__ import annotations

import hashlib
from io import BytesIO

import pytest

from research_kb.discovery.europe_pmc_pdf import (
    EUROPE_PMC_PDF_ENDPOINT,
    EuropePmcPdfTransport,
    _unlink_owned,
)
from research_kb.discovery.acquisition import FileIdentity
from research_kb.discovery.resolution import ProviderAssetRef
from research_kb.errors import ResearchKBError


PDF_BYTES = bytes((37, 80, 68, 70, 45)) + b"1.4\nsynthetic transport bytes\n%%EOF\n"


def asset_ref() -> ProviderAssetRef:
    return ProviderAssetRef(
        provider="europe-pmc",
        source="MED",
        record_id="SYNTH-DISCOVERY-1",
        pmcid="PMC1234567",
        asset_kind="pdf",
        route="europe-pmc-pdf-v1",
    )


class FakeResponse:
    def __init__(
        self,
        content=PDF_BYTES,
        *,
        status=200,
        content_type="application/pdf",
        url=f"{EUROPE_PMC_PDF_ENDPOINT}?pmcid=PMC1234567",
        content_length=None,
    ):
        self.stream = BytesIO(content)
        self.status = status
        self.headers = {
            "Content-Type": content_type,
            "Content-Length": str(len(content) if content_length is None else content_length),
        }
        self.url = url

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def geturl(self):
        return self.url

    def read(self, size=-1):
        return self.stream.read(size)


class FakeOpener:
    def __init__(self, response=None, error=None):
        self.response = response or FakeResponse()
        self.error = error
        self.calls = []

    def open(self, request, timeout):
        self.calls.append((request, timeout))
        if self.error is not None:
            raise self.error
        return self.response


def test_transport_uses_fixed_get_and_streams_one_exclusive_pdf(tmp_path) -> None:
    opener = FakeOpener()
    target = tmp_path / ".research-kb-acquire-event.part.pdf"

    downloaded = EuropePmcPdfTransport(opener=opener).download(asset_ref(), target)

    request, timeout = opener.calls[0]
    assert request.full_url == f"{EUROPE_PMC_PDF_ENDPOINT}?pmcid=PMC1234567"
    assert request.method == "GET"
    assert request.headers["Accept"] == "application/pdf"
    assert "Cookie" not in request.headers
    assert timeout == 30
    assert target.read_bytes() == PDF_BYTES
    assert downloaded.content_type == "application/pdf"
    assert downloaded.content_size_bytes == len(PDF_BYTES)
    assert len(downloaded.sha256) == 64
    assert downloaded.file_identity.size == len(PDF_BYTES)


@pytest.mark.parametrize(
    ("response", "expected_code"),
    [
        (FakeResponse(status=404), "RKBC-032"),
        (FakeResponse(content_type="text/html"), "RKBC-033"),
        (
            FakeResponse(
                url="https://example.invalid/api/getPdf?pmcid=PMC1234567"
            ),
            "RKBC-032",
        ),
        (FakeResponse(content=b"not a pdf"), "RKBC-033"),
        (FakeResponse(content_length=1000), "RKBC-030"),
    ],
)
def test_transport_rejects_invalid_response_and_removes_owned_partial(
    tmp_path,
    response,
    expected_code,
) -> None:
    target = tmp_path / ".research-kb-acquire-event.part.pdf"
    transport = EuropePmcPdfTransport(
        opener=FakeOpener(response=response),
        response_limit=128,
    )

    with pytest.raises(ResearchKBError) as error:
        transport.download(asset_ref(), target)

    assert error.value.diagnostic.code == expected_code
    assert not target.exists()


def test_transport_rejects_existing_target_without_opening_network(tmp_path) -> None:
    target = tmp_path / ".research-kb-acquire-event.part.pdf"
    target.write_bytes(b"pre-existing")
    opener = FakeOpener()

    with pytest.raises(ResearchKBError) as error:
        EuropePmcPdfTransport(opener=opener).download(asset_ref(), target)

    assert error.value.diagnostic.code == "RKBC-017"
    assert target.read_bytes() == b"pre-existing"
    assert opener.calls == []


@pytest.mark.parametrize(
    ("pmcid", "route"),
    [
        ("PMC1234567", "arbitrary-url"),
        ("../1234567", "europe-pmc-pdf-v1"),
    ],
)
def test_transport_rejects_invalid_asset_route_before_network(
    tmp_path,
    pmcid,
    route,
) -> None:
    invalid = ProviderAssetRef(
        provider="europe-pmc",
        source="MED",
        record_id="SYNTH-DISCOVERY-1",
        pmcid=pmcid,
        asset_kind="pdf",
        route=route,
    )
    opener = FakeOpener()

    with pytest.raises(ResearchKBError) as error:
        EuropePmcPdfTransport(opener=opener).download(
            invalid,
            tmp_path / "partial.pdf",
        )

    assert error.value.diagnostic.code == "RKBC-033"
    assert opener.calls == []


def test_transport_enforces_streamed_byte_limit_not_only_content_length(tmp_path) -> None:
    response = FakeResponse(content=PDF_BYTES + b"x" * 200, content_length=0)
    target = tmp_path / "partial.pdf"

    with pytest.raises(ResearchKBError) as error:
        EuropePmcPdfTransport(
            opener=FakeOpener(response=response),
            response_limit=64,
            chunk_size=16,
        ).download(asset_ref(), target)

    assert error.value.diagnostic.code == "RKBC-030"
    assert not target.exists()


def test_transport_cleanup_preserves_same_inode_when_content_changed(tmp_path) -> None:
    target = tmp_path / "partial.pdf"
    target.write_bytes(PDF_BYTES)
    current = target.stat()

    identity = FileIdentity(
        device=current.st_dev,
        inode=current.st_ino,
        size=len(PDF_BYTES),
        sha256=hashlib.sha256(PDF_BYTES).hexdigest(),
    )
    replacement = b"changed but same length".ljust(len(PDF_BYTES), b"!")
    target.write_bytes(replacement)

    _unlink_owned(target, identity)

    assert target.read_bytes() == replacement
