from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from filelock import FileLock, Timeout

from research_kb.errors import LOCK_TIMEOUT, Diagnostic, ResearchKBError


@contextmanager
def workspace_lock(path: Path, *, timeout: float = 30.0) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = FileLock(path, timeout=timeout)
    try:
        with lock:
            yield
    except Timeout as error:
        raise ResearchKBError(
            Diagnostic(LOCK_TIMEOUT, "workspace-lock", None, "", "workspace lock acquisition timed out")
        ) from error
