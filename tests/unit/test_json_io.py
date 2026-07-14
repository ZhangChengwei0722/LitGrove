import os
import stat
from pathlib import Path

import pytest

from research_kb.errors import ResearchKBError
from research_kb.storage.json_io import atomic_write_bytes, read_jsonl, serialize_json, serialize_jsonl


def test_json_serialization_is_deterministic_utf8_lf() -> None:
    value = {"z": "\u6d4b\u8bd5", "a": 1}
    assert serialize_json(value) == b'{"a":1,"z":"\xe6\xb5\x8b\xe8\xaf\x95"}\n'
    assert serialize_jsonl([value, {"a": 2}]).endswith(b"\n")


def test_jsonl_round_trip_and_duplicate_id_check(tmp_path: Path) -> None:
    path = tmp_path / "records.jsonl"
    records = [{"record_id": "one", "value": 1}, {"record_id": "two", "value": 2}]
    path.write_bytes(serialize_jsonl(records))
    assert read_jsonl(path, id_field="record_id") == records

    path.write_bytes(serialize_jsonl([records[0], records[0]]))
    with pytest.raises(ResearchKBError) as caught:
        read_jsonl(path, id_field="record_id")
    assert caught.value.diagnostic.code == "RKBC-015"


@pytest.mark.parametrize(
    "content",
    [
        b"\xef\xbb\xbf{}\n",
        b"{}\r\n",
        b"{}\n\n",
        b"{}",
        b"[]\n",
        b"{\n",
        b"\xff\n",
    ],
)
def test_noncanonical_jsonl_fails_closed(tmp_path: Path, content: bytes) -> None:
    path = tmp_path / "invalid.jsonl"
    path.write_bytes(content)
    with pytest.raises(ResearchKBError) as caught:
        read_jsonl(path)
    assert caught.value.diagnostic.code == "RKBC-015"


def test_atomic_write_replaces_complete_bytes(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "records.jsonl"
    atomic_write_bytes(target, b'{"value":1}\n', "write-id")
    assert target.read_bytes() == b'{"value":1}\n'
    assert not list(target.parent.glob("*.tmp"))


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission contract")
def test_atomic_write_preserves_existing_private_file_mode(tmp_path: Path) -> None:
    target = tmp_path / "records.jsonl"
    target.write_bytes(b'{"old":true}\n')
    target.chmod(0o600)

    atomic_write_bytes(target, b'{"new":true}\n', "write-id")

    assert stat.S_IMODE(target.stat().st_mode) == 0o600


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission contract")
def test_atomic_write_creates_private_target_and_immediate_parent(tmp_path: Path) -> None:
    target = tmp_path / "private" / "records.jsonl"

    atomic_write_bytes(target, b'{"value":1}\n', "write-id")

    assert stat.S_IMODE(target.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
