from pathlib import Path

import pytest

from research_kb.errors import ResearchKBError
from research_kb.paths import (
    SourceRef,
    collision_key,
    make_source_ref,
    normalize_relative_path,
    resolve_source_ref,
    validate_config_relative_path,
)


def test_source_ref_serializes_as_posix() -> None:
    ref = make_source_ref("sources", "folder/\u6d4b\u8bd5.txt")
    assert ref.to_dict() == {"root_id": "sources", "relative_path": "folder/\u6d4b\u8bd5.txt"}


@pytest.mark.parametrize(
    ("value", "code"),
    [
        ("../escape.txt", "RKBC-007"),
        ("folder/../escape.txt", "RKBC-007"),
        ("./file.txt", "RKBC-007"),
        ("folder/./file.txt", "RKBC-007"),
        ("folder//file.txt", "RKBC-007"),
        ("folder/", "RKBC-007"),
        ("/absolute/file.txt", "RKBC-007"),
        ("C" + ":/private/file.txt", "RKBC-007"),
        ("folder\\file.txt", "RKBC-008"),
        ("~/private/file.txt", "RKBC-007"),
        ("", "RKBC-007"),
    ],
)
def test_invalid_persisted_paths_are_rejected(value: str, code: str) -> None:
    with pytest.raises(ResearchKBError) as caught:
        normalize_relative_path(value)
    assert caught.value.diagnostic.code == code


def test_resolved_path_remains_under_root(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    ref = make_source_ref("sources", "folder/file.txt")
    assert resolve_source_ref(root, ref) == root / "folder" / "file.txt"


def test_resolver_rechecks_confinement_for_preconstructed_ref(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    with pytest.raises(ResearchKBError) as caught:
        resolve_source_ref(root, SourceRef("sources", "../escape.txt"))
    assert caught.value.diagnostic.code == "RKBC-007"


def test_collision_key_is_host_independent() -> None:
    assert collision_key("Folder/File.txt") == collision_key("folder/file.TXT")


@pytest.mark.parametrize("value", ["./knowledge", "../knowledge", "profiles/domain.yaml"])
def test_config_relative_paths_are_accepted(value: str) -> None:
    assert validate_config_relative_path(value)


@pytest.mark.parametrize("value", ["/managed", "~/managed", "folder\\managed"])
def test_non_relative_or_non_posix_managed_paths_are_rejected(value: str) -> None:
    with pytest.raises(ResearchKBError):
        validate_config_relative_path(value)
