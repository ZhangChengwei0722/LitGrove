from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

from research_kb.errors import NON_POSIX_PATH, PATH_ESCAPE, Diagnostic, ResearchKBError


ROOT_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


@dataclass(frozen=True, slots=True)
class SourceRef:
    root_id: str
    relative_path: str

    def to_dict(self) -> dict[str, str]:
        return {"root_id": self.root_id, "relative_path": self.relative_path}


def validate_root_id(root_id: str) -> str:
    if ROOT_ID_PATTERN.fullmatch(root_id) is None:
        raise ResearchKBError(
            Diagnostic(PATH_ESCAPE, "source-ref", None, "/root_id", "invalid root_id")
        )
    return root_id


def normalize_relative_path(value: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ResearchKBError(
            Diagnostic(PATH_ESCAPE, "source-ref", None, "/relative_path", "relative_path must be non-empty")
        )
    if "\\" in value:
        raise ResearchKBError(
            Diagnostic(NON_POSIX_PATH, "source-ref", None, "/relative_path", "persisted paths must use POSIX separators")
        )
    if value == "~" or value.startswith("~/"):
        raise ResearchKBError(
            Diagnostic(PATH_ESCAPE, "source-ref", None, "/relative_path", "home-expanded paths are not valid source references")
        )
    if PurePosixPath(value).is_absolute() or PureWindowsPath(value).is_absolute() or PureWindowsPath(value).drive:
        raise ResearchKBError(
            Diagnostic(PATH_ESCAPE, "source-ref", None, "/relative_path", "absolute paths are not valid source references")
        )
    if any(part in {"", ".", ".."} for part in value.split("/")):
        raise ResearchKBError(
            Diagnostic(PATH_ESCAPE, "source-ref", None, "/relative_path", "path escape or ambiguous path segment")
        )
    path = PurePosixPath(value)
    return path.as_posix()


def validate_config_relative_path(value: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ResearchKBError(
            Diagnostic(PATH_ESCAPE, "workspace", None, "", "managed workspace path must be non-empty")
        )
    if "\\" in value:
        raise ResearchKBError(
            Diagnostic(NON_POSIX_PATH, "workspace", None, "", "managed workspace paths must use POSIX separators")
        )
    if value == "~" or value.startswith("~/"):
        raise ResearchKBError(
            Diagnostic(PATH_ESCAPE, "workspace", None, "", "managed workspace paths cannot use home expansion")
        )
    if PurePosixPath(value).is_absolute() or PureWindowsPath(value).is_absolute() or PureWindowsPath(value).drive:
        raise ResearchKBError(
            Diagnostic(PATH_ESCAPE, "workspace", None, "", "managed workspace paths must be config-relative")
        )
    return PurePosixPath(value).as_posix()


def make_source_ref(root_id: str, relative_path: str) -> SourceRef:
    return SourceRef(validate_root_id(root_id), normalize_relative_path(relative_path))


def resolve_source_ref(root: Path, source_ref: SourceRef) -> Path:
    root_resolved = root.resolve()
    candidate = (root_resolved / Path(*PurePosixPath(source_ref.relative_path).parts)).resolve()
    if not candidate.is_relative_to(root_resolved):
        raise ResearchKBError(
            Diagnostic(PATH_ESCAPE, "source-ref", None, "/relative_path", "resolved path escapes the declared source root")
        )
    return candidate


def collision_key(relative_path: str) -> str:
    normalized = normalize_relative_path(relative_path)
    return unicodedata.normalize("NFC", normalized).casefold()
