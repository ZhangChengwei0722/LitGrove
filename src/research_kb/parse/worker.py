from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

from research_kb.parse.pdfplumber_adapter import PdfPlumberAdapter, PdfPlumberTextFlowAdapter
from research_kb.parse.worker_protocol import ParserBudgetProfile
from research_kb.errors import INPUT_TOO_LARGE, PARSER_WORKER_FAILED, PROTECTED_INPUT_CHANGED


def main() -> int:
    raw = sys.stdin.buffer.readline(65_537)
    if not raw or len(raw) > 65_536 or not raw.endswith(b"\n"):
        return 2
    try:
        request = json.loads(raw.decode("utf-8"))
    except Exception:
        return 3
    try:
        result = _execute(request)
    except WorkerRefusal as refusal:
        result = {
            "protocol": "research-kb-parser-worker@1.0",
            "status": "failure",
            "diagnostic_code": refusal.code,
            "diagnostic_path": refusal.path,
        }
    except Exception:
        result = {
            "protocol": "research-kb-parser-worker@1.0",
            "status": "failure",
            "diagnostic_code": PARSER_WORKER_FAILED,
            "diagnostic_path": "/worker",
        }
    encoded = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
    if len(encoded) > request["budget"]["max_status_frame_bytes"]:
        return 4
    sys.stdout.buffer.write(encoded)
    sys.stdout.buffer.flush()
    return 0


def _execute(request: dict[str, Any]) -> dict[str, Any]:
    expected = {
        "protocol", "operation_id", "source_path", "source_sha256", "paper_id", "adapter_name",
        "adapter_version", "parser_profile_id", "temp_root", "budget",
    }
    if not isinstance(request, dict) or set(request) != expected or request.get("protocol") != "research-kb-parser-worker@1.0":
        raise WorkerRefusal(PARSER_WORKER_FAILED, "/request")
    budget = ParserBudgetProfile(**request["budget"])
    source = Path(request["source_path"])
    temp_root = Path(request["temp_root"])
    if not temp_root.is_dir() or temp_root.is_symlink() or source.is_symlink() or not source.is_file():
        raise WorkerRefusal(PARSER_WORKER_FAILED, "/paths")
    before = _sha256(source)
    if before != request["source_sha256"] or source.stat().st_size > budget.max_source_bytes:
        if before != request["source_sha256"]:
            raise WorkerRefusal(PROTECTED_INPUT_CHANGED, "/source_sha256")
        raise WorkerRefusal(INPUT_TOO_LARGE, "/source_path")
    adapters = {item.name: item for item in (PdfPlumberAdapter(), PdfPlumberTextFlowAdapter())}
    adapter = adapters.get(request["adapter_name"])
    if adapter is None or adapter.version != request["adapter_version"]:
        raise WorkerRefusal(PROTECTED_INPUT_CHANGED, "/adapter")
    pages = list(adapter.parse(source, paper_id=request["paper_id"], parse_run_id=request["operation_id"]))
    if not pages or len(pages) > budget.max_pages:
        raise WorkerRefusal(INPUT_TOO_LARGE, "/pages")
    total_text = 0
    for page in pages:
        page_bytes = len(page["text"].encode("utf-8"))
        if page_bytes > budget.max_page_text_utf8_bytes:
            raise WorkerRefusal(INPUT_TOO_LARGE, "/pages/text")
        total_text += page_bytes
        if total_text > budget.max_total_parsed_utf8_bytes:
            raise WorkerRefusal(INPUT_TOO_LARGE, "/pages")
    if _sha256(source) != before:
        raise WorkerRefusal(PROTECTED_INPUT_CHANGED, "/source_sha256")
    content = json.dumps(pages, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(content) > budget.max_operation_temp_bytes:
        raise WorkerRefusal(INPUT_TOO_LARGE, "/output")
    output = temp_root / "pages.json"
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    return {
        "protocol": "research-kb-parser-worker@1.0",
        "status": "success",
        "output_name": "pages.json",
        "output_sha256": hashlib.sha256(content).hexdigest(),
        "output_bytes": len(content),
        "page_count": len(pages),
        "output_utf8_bytes": total_text,
        "source_sha256": before,
        "parser": {"adapter": adapter.name, "version": adapter.version},
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


class WorkerRefusal(Exception):
    def __init__(self, code: str, path: str):
        self.code = code
        self.path = path
        super().__init__(code)


if __name__ == "__main__":
    raise SystemExit(main())
