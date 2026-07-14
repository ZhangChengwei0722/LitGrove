from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from filelock import FileLock, Timeout

from research_kb.errors import LOCK_TIMEOUT, Diagnostic, ResearchKBError
from research_kb.storage.json_io import ensure_private_directory


@contextmanager
def workspace_lock(path: Path, *, timeout: float = 30.0) -> Iterator[None]:
    ensure_private_directory(path.parent)
    lock = FileLock(path, timeout=timeout)
    try:
        with lock:
            yield
    except Timeout as error:
        raise ResearchKBError(
            Diagnostic(LOCK_TIMEOUT, "workspace-lock", None, "", "workspace lock acquisition timed out")
        ) from error
