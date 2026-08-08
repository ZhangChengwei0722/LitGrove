from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from research_kb.errors import (
    INPUT_TOO_LARGE,
    OPERATION_CANCELLED,
    PARSER_WORKER_FAILED,
    PROTECTED_INPUT_CHANGED,
    Diagnostic,
    ResearchKBError,
)


PARSER_BUDGET_PROFILE = "trusted-local-pdf-standard@1.0"


@dataclass(frozen=True, slots=True)
class ParserBudgetProfile:
    profile_id: str = PARSER_BUDGET_PROFILE
    max_source_bytes: int = 268_435_456
    max_pages: int = 2_000
    max_page_text_utf8_bytes: int = 2_097_152
    max_total_parsed_utf8_bytes: int = 268_435_456
    max_operation_temp_bytes: int = 536_870_912
    wall_timeout_seconds: float = 300
    cancel_grace_seconds: float = 5
    max_request_frame_bytes: int = 65_536
    max_status_frame_bytes: int = 1_048_576


@dataclass(frozen=True, slots=True)
class WorkerParseRequest:
    operation_id: str
    source_path: Path
    source_sha256: str
    paper_id: str
    adapter_name: str
    adapter_version: str
    parser_profile_id: str
    temp_root: Path
    budget: ParserBudgetProfile

    def to_wire(self) -> dict[str, Any]:
        return {
            "protocol": "research-kb-parser-worker@1.0",
            "operation_id": self.operation_id,
            "source_path": str(self.source_path),
            "source_sha256": self.source_sha256,
            "paper_id": self.paper_id,
            "adapter_name": self.adapter_name,
            "adapter_version": self.adapter_version,
            "parser_profile_id": self.parser_profile_id,
            "temp_root": str(self.temp_root),
            "budget": asdict(self.budget),
        }


@dataclass(frozen=True, slots=True)
class WorkerParseResult:
    pages: tuple[dict[str, Any], ...]
    source_sha256: str
    parser: dict[str, str]
    output_utf8_bytes: int


CancelCheck = Callable[[], bool]


def run_parser_worker(
    request: WorkerParseRequest,
    *,
    cancel_check: CancelCheck | None = None,
) -> WorkerParseResult:
    _validate_budget(request.budget)
    frame = json.dumps(request.to_wire(), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
    if len(frame) > request.budget.max_request_frame_bytes:
        raise _error(INPUT_TOO_LARGE, "/request", "parser worker request exceeds the frame budget")
    request.temp_root.mkdir(mode=0o700, parents=False)
    process = subprocess.Popen(
        [sys.executable, "-m", "research_kb.parse.worker"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        return _complete_started_worker(process, request, frame, cancel_check)
    except BaseException:
        _settle_started_worker(process, request.budget.cancel_grace_seconds)
        raise


def _complete_started_worker(
    process: Any,
    request: WorkerParseRequest,
    frame: bytes,
    cancel_check: CancelCheck | None,
) -> WorkerParseResult:
    assert process.stdin is not None
    process.stdin.write(frame)
    process.stdin.close()
    deadline = time.monotonic() + request.budget.wall_timeout_seconds
    cancelled = False
    timed_out = False
    while process.poll() is None:
        if cancel_check is not None and cancel_check():
            cancelled = True
            process.terminate()
            break
        if time.monotonic() >= deadline:
            timed_out = True
            process.terminate()
            break
        time.sleep(0.02)
    if cancelled or timed_out:
        try:
            process.wait(timeout=request.budget.cancel_grace_seconds)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        code = OPERATION_CANCELLED if cancelled else PARSER_WORKER_FAILED
        message = "parser worker was cancelled" if cancelled else "parser worker exceeded the wall-time budget"
        raise _error(code, "/worker", message)
    assert process.stdout is not None and process.stderr is not None
    stdout = process.stdout.read(request.budget.max_status_frame_bytes + 1)
    _ = process.stderr.read(request.budget.max_status_frame_bytes + 1)
    if len(stdout) > request.budget.max_status_frame_bytes:
        raise _error(INPUT_TOO_LARGE, "/worker/status", "parser worker status exceeds the frame budget")
    if process.returncode != 0:
        raise _error(PARSER_WORKER_FAILED, "/worker", "parser worker exited unsuccessfully")
    try:
        status = json.loads(stdout.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise _error(PARSER_WORKER_FAILED, "/worker/status", "parser worker returned malformed status") from error
    failure_keys = {"protocol", "status", "diagnostic_code", "diagnostic_path"}
    if isinstance(status, dict) and set(status) == failure_keys and status.get("status") == "failure":
        code = status.get("diagnostic_code")
        if code not in {INPUT_TOO_LARGE, PARSER_WORKER_FAILED, PROTECTED_INPUT_CHANGED}:
            raise _error(PARSER_WORKER_FAILED, "/worker/status", "parser worker failure code is not allowed")
        raise _error(code, str(status.get("diagnostic_path")), "parser worker refused the operation")
    expected_keys = {
        "protocol", "status", "output_name", "output_sha256", "output_bytes", "page_count",
        "output_utf8_bytes", "source_sha256", "parser",
    }
    if not isinstance(status, dict) or set(status) != expected_keys or status.get("status") != "success":
        raise _error(PARSER_WORKER_FAILED, "/worker/status", "parser worker status does not match the closed contract")
    if status.get("protocol") != "research-kb-parser-worker@1.0" or status.get("output_name") != "pages.json":
        raise _error(PARSER_WORKER_FAILED, "/worker/status", "parser worker protocol or output name is invalid")
    numeric_fields = ("output_bytes", "page_count", "output_utf8_bytes")
    if any(type(status.get(field)) is not int or status[field] < 0 for field in numeric_fields):
        raise _error(PARSER_WORKER_FAILED, "/worker/status", "parser worker numeric status fields are invalid")
    if not isinstance(status.get("output_sha256"), str) or len(status["output_sha256"]) != 64:
        raise _error(PARSER_WORKER_FAILED, "/worker/status", "parser worker output digest is invalid")
    output = request.temp_root / "pages.json"
    if output.is_symlink() or not output.is_file():
        raise _error(PARSER_WORKER_FAILED, "/worker/output", "parser worker output is not a regular operation-owned file")
    try:
        output_size = output.stat().st_size
        if output_size > request.budget.max_operation_temp_bytes or output_size != status["output_bytes"]:
            raise _error(INPUT_TOO_LARGE, "/worker/output", "parser worker output exceeds or mismatches the temp budget")
        with output.open("rb") as stream:
            content = stream.read(request.budget.max_operation_temp_bytes + 1)
    except OSError as error:
        raise _error(PARSER_WORKER_FAILED, "/worker/output", "parser worker output cannot be read") from error
    if len(content) > request.budget.max_operation_temp_bytes or len(content) != output_size:
        raise _error(INPUT_TOO_LARGE, "/worker/output", "parser worker output exceeds or mismatches the temp budget")
    if hashlib.sha256(content).hexdigest() != status.get("output_sha256"):
        raise _error(PROTECTED_INPUT_CHANGED, "/worker/output", "parser worker output digest does not match status")
    try:
        pages = json.loads(content.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise _error(PARSER_WORKER_FAILED, "/worker/output", "parser worker output is malformed") from error
    output_utf8_bytes = _validate_pages(pages, request.budget)
    if len(pages) != status.get("page_count") or status.get("source_sha256") != request.source_sha256:
        raise _error(PROTECTED_INPUT_CHANGED, "/worker/status", "parser worker source or page identity changed")
    if status["output_utf8_bytes"] != output_utf8_bytes:
        raise _error(PROTECTED_INPUT_CHANGED, "/worker/status", "parser worker text-size identity changed")
    parser = status.get("parser")
    if parser != {"adapter": request.adapter_name, "version": request.adapter_version}:
        raise _error(PROTECTED_INPUT_CHANGED, "/worker/parser", "parser worker identity changed")
    return WorkerParseResult(tuple(pages), request.source_sha256, parser, status["output_utf8_bytes"])


def _settle_started_worker(process: Any, grace_seconds: float) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def _validate_pages(pages: Any, budget: ParserBudgetProfile) -> int:
    if not isinstance(pages, list) or not pages or len(pages) > budget.max_pages:
        raise _error(INPUT_TOO_LARGE, "/worker/pages", "parser worker page count is outside the budget")
    total = 0
    expected_keys = {"pdf_page", "printed_page", "text", "locator"}
    for index, page in enumerate(pages, start=1):
        if not isinstance(page, dict) or set(page) != expected_keys or page.get("pdf_page") != index:
            raise _error(PARSER_WORKER_FAILED, "/worker/pages", "parser worker pages are not canonical")
        text = page.get("text")
        if not isinstance(text, str) or not isinstance(page.get("locator"), str):
            raise _error(PARSER_WORKER_FAILED, "/worker/pages", "parser worker page fields are invalid")
        size = len(text.encode("utf-8"))
        if size > budget.max_page_text_utf8_bytes:
            raise _error(INPUT_TOO_LARGE, "/worker/pages", "parser worker page text exceeds the budget")
        total += size
        if total > budget.max_total_parsed_utf8_bytes:
            raise _error(INPUT_TOO_LARGE, "/worker/pages", "parser worker total text exceeds the budget")
    return total


def _validate_budget(budget: ParserBudgetProfile) -> None:
    values = asdict(budget)
    for key, value in values.items():
        if key == "profile_id":
            continue
        if not isinstance(value, (int, float)) or value <= 0:
            raise _error(PARSER_WORKER_FAILED, f"/budget/{key}", "parser budget values must be positive")


def _error(code: str, path: str, message: str) -> ResearchKBError:
    return ResearchKBError(Diagnostic(code, "parser-worker", None, path, message))


__all__ = [
    "PARSER_BUDGET_PROFILE",
    "ParserBudgetProfile",
    "WorkerParseRequest",
    "WorkerParseResult",
    "run_parser_worker",
]
