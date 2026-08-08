from __future__ import annotations

import hashlib
import io
import json
import shutil
import uuid
from pathlib import Path

import pytest

from research_kb.errors import ResearchKBError
import research_kb.parse.worker_protocol as worker_protocol
from research_kb.parse.pdfplumber_adapter import PdfPlumberTextFlowAdapter
from research_kb.parse.worker_protocol import ParserBudgetProfile, WorkerParseRequest, run_parser_worker
from research_kb.storage.json_io import file_sha256
from tests.pdf_helpers import write_synthetic_pdf


PAPER_ID = "paper_11111111-1111-4111-8111-111111111111"


def _request(tmp_path: Path, **budget_overrides) -> WorkerParseRequest:
    source = tmp_path / "worker.pdf"
    write_synthetic_pdf(source, ["Synthetic worker page one.", "Synthetic worker page two."])
    adapter = PdfPlumberTextFlowAdapter()
    return WorkerParseRequest(
        operation_id=f"worker_{uuid.uuid4()}",
        source_path=source,
        source_sha256=file_sha256(source) or "",
        paper_id=PAPER_ID,
        adapter_name=adapter.name,
        adapter_version=adapter.version,
        parser_profile_id="trusted-local-pdf-standard@1.0",
        temp_root=tmp_path / "operation-temp",
        budget=ParserBudgetProfile(**budget_overrides),
    )


def test_parser_worker_round_trip_is_source_and_parser_bound(tmp_path: Path) -> None:
    request = _request(tmp_path)
    result = run_parser_worker(request)

    assert len(result.pages) == 2
    assert result.source_sha256 == request.source_sha256
    assert result.parser == {"adapter": request.adapter_name, "version": request.adapter_version}


def test_parser_worker_wall_timeout_is_enforced(tmp_path: Path) -> None:
    request = _request(tmp_path, wall_timeout_seconds=0.001)

    with pytest.raises(ResearchKBError) as caught:
        run_parser_worker(request)

    assert caught.value.diagnostic.code == "RKBC-037"


def test_parser_worker_cancellation_is_enforced(tmp_path: Path) -> None:
    request = _request(tmp_path)

    with pytest.raises(ResearchKBError) as caught:
        run_parser_worker(request, cancel_check=lambda: True)

    assert caught.value.diagnostic.code == "RKBC-038"


def test_parser_worker_page_text_budget_is_classified(tmp_path: Path) -> None:
    request = _request(tmp_path, max_page_text_utf8_bytes=4)

    with pytest.raises(ResearchKBError) as caught:
        run_parser_worker(request)

    assert caught.value.diagnostic.code == "RKBC-030"


def test_parser_worker_source_digest_drift_is_classified(tmp_path: Path) -> None:
    request = _request(tmp_path)
    request.source_path.write_bytes(request.source_path.read_bytes() + b"changed")

    with pytest.raises(ResearchKBError) as caught:
        run_parser_worker(request)

    assert caught.value.diagnostic.code == "RKBC-026"


class _FakeProcess:
    def __init__(self, stdout: bytes, *, returncode: int = 0) -> None:
        self.stdin = io.BytesIO()
        self.stdout = io.BytesIO(stdout)
        self.stderr = io.BytesIO()
        self.returncode = returncode

    def poll(self) -> int:
        return self.returncode


class _RunningFakeProcess(_FakeProcess):
    def __init__(self) -> None:
        super().__init__(b"")
        self.returncode = None
        self.terminated = False
        self.killed = False
        self.waited = False

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 1

    def kill(self) -> None:
        self.killed = True
        self.returncode = 1

    def wait(self, timeout=None) -> int:
        del timeout
        self.waited = True
        self.returncode = 1
        return 1


def test_parser_worker_crash_is_classified_without_parsing_status(tmp_path: Path, monkeypatch) -> None:
    request = _request(tmp_path)
    monkeypatch.setattr(
        worker_protocol.subprocess,
        "Popen",
        lambda *_args, **_kwargs: _FakeProcess(b"", returncode=9),
    )

    with pytest.raises(ResearchKBError) as caught:
        run_parser_worker(request)

    assert caught.value.diagnostic.code == "RKBC-037"
    assert caught.value.diagnostic.json_path == "/worker"


def test_parent_side_exception_settles_started_worker(tmp_path: Path, monkeypatch) -> None:
    request = _request(tmp_path)
    process = _RunningFakeProcess()
    monkeypatch.setattr(worker_protocol.subprocess, "Popen", lambda *_args, **_kwargs: process)

    def failing_cancel_check() -> bool:
        raise RuntimeError("synthetic parent-side failure")

    with pytest.raises(RuntimeError, match="parent-side"):
        run_parser_worker(request, cancel_check=failing_cancel_check)

    assert process.terminated is True
    assert process.waited is True
    assert process.poll() is not None


def test_parser_worker_malformed_status_is_rejected(tmp_path: Path, monkeypatch) -> None:
    request = _request(tmp_path)
    monkeypatch.setattr(
        worker_protocol.subprocess,
        "Popen",
        lambda *_args, **_kwargs: _FakeProcess(b"{not-json"),
    )

    with pytest.raises(ResearchKBError) as caught:
        run_parser_worker(request)

    assert caught.value.diagnostic.code == "RKBC-037"
    assert caught.value.diagnostic.json_path == "/worker/status"


def test_parser_worker_malformed_output_is_rejected(tmp_path: Path, monkeypatch) -> None:
    request = _request(tmp_path)
    content = b"not-json"
    status = {
        "protocol": "research-kb-parser-worker@1.0",
        "status": "success",
        "output_name": "pages.json",
        "output_sha256": hashlib.sha256(content).hexdigest(),
        "output_bytes": len(content),
        "page_count": 1,
        "output_utf8_bytes": 0,
        "source_sha256": request.source_sha256,
        "parser": {"adapter": request.adapter_name, "version": request.adapter_version},
    }

    def fake_popen(*_args, **_kwargs):
        (request.temp_root / "pages.json").write_bytes(content)
        return _FakeProcess(json.dumps(status).encode("utf-8"))

    monkeypatch.setattr(worker_protocol.subprocess, "Popen", fake_popen)

    with pytest.raises(ResearchKBError) as caught:
        run_parser_worker(request)

    assert caught.value.diagnostic.code == "RKBC-037"
    assert caught.value.diagnostic.json_path == "/worker/output"


@pytest.fixture(autouse=True)
def _cleanup_operation_temp(tmp_path: Path):
    yield
    target = tmp_path / "operation-temp"
    if target.is_dir():
        shutil.rmtree(target)
