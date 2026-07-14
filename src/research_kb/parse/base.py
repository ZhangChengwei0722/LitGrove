from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any, Protocol


class ParseAdapter(Protocol):
    name: str
    version: str

    def parse(
        self,
        source: Path,
        *,
        paper_id: str,
        parse_run_id: str,
    ) -> Iterable[dict[str, Any]]: ...
