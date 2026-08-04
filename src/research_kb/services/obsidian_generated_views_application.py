from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Callable, Iterable

from research_kb.application import APPLICATION_SERVICE_INTERFACE_VERSION
from research_kb.errors import SCHEMA_VALIDATION_FAILED, Diagnostic, ResearchKBError
from research_kb.obsidian_views import OPTIONAL_TABLES, RENDERER_VERSION
from research_kb.process_events import utc_now
from research_kb.services.obsidian_generated_views import (
    MAX_PREVIEW_PATHS,
    ObsidianGeneratedViewsService,
)
from research_kb.services.workspace_session import WorkspaceSession


MAX_STATUS_PAGE_SIZE = 100


class ObsidianGeneratedViewsApplicationService:
    def __init__(self, *, clock: Callable[[], object] = utc_now):
        self.clock = clock

    def limits(self, session: WorkspaceSession) -> dict[str, Any]:
        _layout(session)
        return _read_response(
            {
                "renderer_version": RENDERER_VERSION,
                "optional_tables": list(OPTIONAL_TABLES),
                "max_preview_paths": MAX_PREVIEW_PATHS,
                "max_status_page_size": MAX_STATUS_PAGE_SIZE,
                "reverse_sync_supported": False,
                "browser_paths_accepted": False,
            }
        )

    def status(
        self,
        session: WorkspaceSession,
        *,
        page_size: int = 20,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        result = ObsidianGeneratedViewsService(_layout(session), clock=self.clock).status()
        entries, next_cursor = _page_entries(result["entries"], page_size, cursor)
        return _read_response({**result, "entries": entries, "next_cursor": next_cursor})

    def preview_render(
        self,
        session: WorkspaceSession,
        *,
        optional_tables: Iterable[str] = (),
    ) -> dict[str, Any]:
        result = ObsidianGeneratedViewsService(_layout(session), clock=self.clock).preview_render(
            optional_tables=optional_tables
        )
        return _read_response(result)

    def stream_snapshot(
        self,
        session: WorkspaceSession,
        *,
        expected_manifest_digest: str,
        sink: Callable[[str, str, bytes], None],
    ) -> dict[str, Any]:
        result = ObsidianGeneratedViewsService(_layout(session), clock=self.clock).stream_snapshot(
            expected_manifest_digest=expected_manifest_digest,
            sink=sink,
        )
        return _read_response(result)

    def render(
        self,
        session: WorkspaceSession,
        request: Mapping[str, Any],
        *,
        actor: str,
    ) -> dict[str, Any]:
        result = ObsidianGeneratedViewsService(_layout(session), clock=self.clock).render(
            request,
            actor=actor,
        )
        return {
            "status": "success",
            "interface_version": "1.0",
            "application_service_interface_version": APPLICATION_SERVICE_INTERFACE_VERSION,
            **result,
            "persistent_writes": 1 if result["result"] == "committed" else 0,
            "canonical_scientific_write": False,
        }


def _layout(session: WorkspaceSession):
    if not isinstance(session, WorkspaceSession):
        raise ResearchKBError(
            Diagnostic(
                SCHEMA_VALIDATION_FAILED,
                "obsidian-generated-view-application-request",
                None,
                "/session",
                "a Core-owned WorkspaceSession is required",
            )
        )
    return session._layout


def _read_response(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "success",
        "interface_version": "1.0",
        "application_service_interface_version": APPLICATION_SERVICE_INTERFACE_VERSION,
        **payload,
        "persistent_writes": 0,
        "canonical_scientific_write": False,
    }


def _page_entries(
    entries: list[dict[str, Any]],
    page_size: int,
    cursor: str | None,
) -> tuple[list[dict[str, Any]], str | None]:
    if isinstance(page_size, bool) or not isinstance(page_size, int) or not 1 <= page_size <= MAX_STATUS_PAGE_SIZE:
        raise ResearchKBError(
            Diagnostic(
                SCHEMA_VALIDATION_FAILED,
                "obsidian-generated-view-application-request",
                None,
                "/page_size",
                f"page_size must be between 1 and {MAX_STATUS_PAGE_SIZE}",
            )
        )
    ordered = sorted(entries, key=lambda item: item["logical_path"])
    if cursor is None:
        start = 0
    elif not isinstance(cursor, str) or not cursor:
        raise _cursor_error()
    else:
        matches = [index for index, item in enumerate(ordered) if item["logical_path"] == cursor]
        if len(matches) != 1:
            raise _cursor_error()
        start = matches[0] + 1
    page = ordered[start : start + page_size]
    next_cursor = page[-1]["logical_path"] if start + page_size < len(ordered) else None
    return page, next_cursor


def _cursor_error() -> ResearchKBError:
    return ResearchKBError(
        Diagnostic(
            SCHEMA_VALIDATION_FAILED,
            "obsidian-generated-view-application-request",
            None,
            "/cursor",
            "cursor is not a current generated-view logical path",
        )
    )


__all__ = ["MAX_STATUS_PAGE_SIZE", "ObsidianGeneratedViewsApplicationService"]
