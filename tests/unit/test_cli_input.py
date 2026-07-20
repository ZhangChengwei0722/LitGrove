from io import BytesIO

import pytest

from research_kb.cli_input import read_bounded_json_object
from research_kb.errors import ResearchKBError


class RecordingStream(BytesIO):
    def __init__(self, value: bytes):
        super().__init__(value)
        self.requested_sizes: list[int] = []

    def read(self, size: int = -1) -> bytes:
        self.requested_sizes.append(size)
        return super().read(size)


def test_bounded_json_object_accepts_utf8_and_trailing_whitespace() -> None:
    value = read_bounded_json_object(
        BytesIO('{"label":"\u6d4b\u8bd5"}\n  '.encode("utf-8")),
        limit=64,
        record_kind="registry-metadata",
    )

    assert value == {"label": "\u6d4b\u8bd5"}


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"   \n",
        b"{",
        b"[]",
        b"1",
        b"key: value\n",
        b'{"a":1}{"b":2}',
        b"\xff",
    ],
)
def test_bounded_json_object_rejects_invalid_payload_without_echo(payload: bytes) -> None:
    with pytest.raises(ResearchKBError) as caught:
        read_bounded_json_object(BytesIO(payload), limit=64, record_kind="stdin-input")

    assert caught.value.diagnostic.code == "RKBC-002"
    assert caught.value.diagnostic.record_kind == "stdin-input"
    decoded = payload.decode("utf-8", errors="ignore")
    if decoded:
        assert decoded not in caught.value.diagnostic.message


def test_bounded_json_object_reads_at_most_limit_plus_one() -> None:
    stream = RecordingStream(b'{"value":"0123456789"}')

    with pytest.raises(ResearchKBError) as caught:
        read_bounded_json_object(stream, limit=8, record_kind="mutation-request")

    assert caught.value.diagnostic.code == "RKBC-030"
    assert stream.requested_sizes == [9]
    assert stream.tell() == 9


def test_bounded_json_object_accepts_payload_at_exact_limit() -> None:
    payload = b'{"a":1}'

    assert len(payload) == 7
    assert read_bounded_json_object(
        BytesIO(payload),
        limit=7,
        record_kind="registry-metadata",
    ) == {"a": 1}
