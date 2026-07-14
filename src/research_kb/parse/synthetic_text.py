from __future__ import annotations

from pathlib import Path
from typing import Any


class SyntheticTextAdapter:
    name = "synthetic-text"
    version = "1.0"

    def parse(
        self,
        source: Path,
        *,
        paper_id: str,
        parse_run_id: str,
    ) -> list[dict[str, Any]]:
        if source.suffix.lower() != ".txt":
            raise ValueError("SyntheticTextAdapter accepts only .txt fixture sources")
        text = source.read_text(encoding="utf-8", errors="strict")
        if not text:
            raise ValueError("synthetic source is empty")
        return [
            {
                "pdf_page": index,
                "printed_page": None,
                "text": page.strip(),
                "locator": f"page:{index}:block:1",
            }
            for index, page in enumerate(text.split("\f"), start=1)
        ]
