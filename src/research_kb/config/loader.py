from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from research_kb.contracts.registry import SchemaRegistry
from research_kb.contracts.validator import validate_record
from research_kb.errors import SCHEMA_VALIDATION_FAILED, Diagnostic, ResearchKBError


@dataclass(frozen=True, slots=True)
class ConfigDocument:
    path: Path
    data: dict[str, Any]

    @property
    def base_dir(self) -> Path:
        return self.path.parent


def load_config(path: Path, kind: str, registry: SchemaRegistry | None = None) -> ConfigDocument:
    resolved = path.resolve()
    text = resolved.read_text(encoding="utf-8")
    if resolved.suffix.lower() == ".json":
        loaded = json.loads(text)
    else:
        loaded = yaml.safe_load(text)
    if not isinstance(loaded, dict):
        raise ResearchKBError(
            Diagnostic(SCHEMA_VALIDATION_FAILED, kind, None, "", "config root must be a mapping")
        )
    diagnostics = validate_record(kind, loaded, registry=registry)
    if diagnostics:
        raise ResearchKBError(diagnostics[0])
    return ConfigDocument(resolved, loaded)


def resolve_config_path(document: ConfigDocument, value: str) -> Path:
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = document.base_dir / candidate
    return candidate.resolve()
