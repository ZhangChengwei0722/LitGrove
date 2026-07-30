from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

from research_kb.catalog.models import canonical_digest
from research_kb.errors import DUPLICATE_ID, INCOMPLETE_TRANSACTION, Diagnostic, ResearchKBError


ROOT_REASONS = frozenset({"reference_registered", "copied_into_local_inbox"})
UNAVAILABLE_REASONS = {
    "source_missing": "missing",
    "source_inaccessible": "inaccessible",
    "source_relink_required": "relink_required",
}


def source_asset_chain_diagnostics(states: Iterable[dict[str, Any]]) -> list[Diagnostic]:
    state_list = list(states)
    diagnostics: list[Diagnostic] = []
    seen_state_ids: set[str] = set()
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for state in state_list:
        state_id = state.get("source_asset_state_id")
        if isinstance(state_id, str):
            if state_id in seen_state_ids:
                diagnostics.append(_diagnostic(state, "/source_asset_state_id", "duplicate source asset state ID"))
            seen_state_ids.add(state_id)
        asset_id = state.get("source_asset_id")
        if isinstance(asset_id, str):
            grouped[asset_id].append(state)

    for history in grouped.values():
        ordered = sorted(history, key=lambda item: item.get("revision", 0))
        if not ordered or ordered[0].get("revision") != 1:
            diagnostics.append(_diagnostic(ordered[0], "/revision", "source asset chain must begin at revision one"))
            continue
        root = ordered[0]
        if root.get("predecessor") is not None:
            diagnostics.append(_diagnostic(root, "/predecessor", "source asset root must not have a predecessor"))
        if root.get("reason") not in ROOT_REASONS:
            diagnostics.append(_diagnostic(root, "/reason", "source asset root has an invalid intake reason"))
        if root.get("manifestation_status") != "active" or root.get("availability") != "available":
            diagnostics.append(_diagnostic(root, "/manifestation_status", "source asset root must be an available active manifestation"))
        for index, state in enumerate(ordered):
            fingerprint = state.get("source_fingerprint", {}).get("value")
            if isinstance(fingerprint, str) and state.get("manifestation_id") != f"sha256:{fingerprint}":
                diagnostics.append(_diagnostic(state, "/manifestation_id", "manifestation identity must match source fingerprint"))
            if state.get("manifestation_status") == "change_candidate" and state.get("availability") != "available":
                diagnostics.append(_diagnostic(state, "/availability", "changed manifestation candidate must be available"))
            if index == 0:
                continue
            previous = ordered[index - 1]
            if state.get("revision") != previous.get("revision", 0) + 1:
                diagnostics.append(_diagnostic(state, "/revision", "source asset revision sequence is not contiguous"))
            expected_predecessor = {
                "state_id": previous.get("source_asset_state_id"),
                "state_digest": canonical_digest(previous),
            }
            if state.get("predecessor") != expected_predecessor:
                diagnostics.append(_diagnostic(state, "/predecessor", "source asset predecessor does not match prior revision"))
            for field in ("source_asset_id", "workspace_id", "asset_role", "created_at"):
                if state.get(field) != root.get(field):
                    diagnostics.append(_diagnostic(state, f"/{field}", f"source asset {field} changed across revisions"))
            previous_paper = previous.get("paper_id")
            current_paper = state.get("paper_id")
            if previous_paper is not None and current_paper != previous_paper:
                diagnostics.append(_diagnostic(state, "/paper_id", "source asset paper association cannot be rewritten"))
            diagnostics.extend(
                _transition_diagnostics(
                    state,
                    previous,
                    active_fingerprint=_active_fingerprint_before(ordered, index),
                )
            )
            if _parse_timestamp(state.get("updated_at")) < _parse_timestamp(previous.get("updated_at")):
                diagnostics.append(_diagnostic(state, "/updated_at", "source asset timestamp moved backwards"))

    if diagnostics:
        return _deduplicate(diagnostics)
    projections = source_asset_projection(state_list, validate=False)
    active_main: dict[str, str] = {}
    by_state_id = {
        state["source_asset_state_id"]: state
        for state in state_list
        if isinstance(state.get("source_asset_state_id"), str)
    }
    for projection in projections:
        paper_id = projection.get("paper_id")
        if paper_id is None or projection.get("asset_role") != "main_pdf":
            continue
        active_state_id = projection.get("active_state_id")
        if active_state_id is None:
            continue
        existing = active_main.get(paper_id)
        if existing is not None and existing != projection["source_asset_id"]:
            state = by_state_id[projection["observed_state_id"]]
            diagnostics.append(
                Diagnostic(
                    DUPLICATE_ID,
                    "source-asset-state",
                    state["source_asset_state_id"],
                    "/paper_id",
                    "paper has more than one active main PDF source asset",
                )
            )
        active_main[paper_id] = projection["source_asset_id"]
    return _deduplicate(diagnostics)


def current_source_asset_heads(states: Iterable[dict[str, Any]]) -> tuple[dict[str, Any], ...]:
    state_list = list(states)
    diagnostics = source_asset_chain_diagnostics(state_list)
    if diagnostics:
        raise ResearchKBError(diagnostics[0])
    heads: dict[str, dict[str, Any]] = {}
    for state in state_list:
        existing = heads.get(state["source_asset_id"])
        if existing is None or state["revision"] > existing["revision"]:
            heads[state["source_asset_id"]] = state
    return tuple(sorted(heads.values(), key=lambda item: item["source_asset_id"]))


def source_asset_projection(
    states: Iterable[dict[str, Any]],
    *,
    validate: bool = True,
) -> tuple[dict[str, Any], ...]:
    state_list = list(states)
    if validate:
        diagnostics = source_asset_chain_diagnostics(state_list)
        if diagnostics:
            raise ResearchKBError(diagnostics[0])
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for state in state_list:
        grouped[state["source_asset_id"]].append(state)
    result: list[dict[str, Any]] = []
    for asset_id, history in grouped.items():
        ordered = sorted(history, key=lambda item: item["revision"])
        observed = ordered[-1]
        active = next(
            (item for item in reversed(ordered) if item["manifestation_status"] == "active"),
            None,
        )
        if observed["availability"] != "available":
            currentness = "unavailable"
        elif observed["manifestation_status"] == "change_candidate":
            currentness = "stale_source"
        else:
            currentness = "current"
        result.append(
            {
                "source_asset_id": asset_id,
                "paper_id": observed["paper_id"],
                "asset_role": observed["asset_role"],
                "observed_state_id": observed["source_asset_state_id"],
                "active_state_id": None if active is None else active["source_asset_state_id"],
                "source_availability": observed["availability"],
                "source_currentness": currentness,
                "manifestation_status": observed["manifestation_status"],
            }
        )
    return tuple(sorted(result, key=lambda item: item["source_asset_id"]))


def _diagnostic(state: Mapping[str, Any], path: str, message: str) -> Diagnostic:
    return Diagnostic(
        INCOMPLETE_TRANSACTION,
        "source-asset-state",
        state.get("source_asset_state_id") if isinstance(state.get("source_asset_state_id"), str) else None,
        path,
        message,
    )


def _transition_diagnostics(
    state: Mapping[str, Any],
    previous: Mapping[str, Any],
    *,
    active_fingerprint: str | None,
) -> list[Diagnostic]:
    reason = state.get("reason")
    failures: list[Diagnostic] = []

    def require(condition: bool, path: str, message: str) -> None:
        if not condition:
            failures.append(_diagnostic(state, path, message))

    same_source_ref = state.get("source_ref") == previous.get("source_ref")
    same_fingerprint = state.get("source_fingerprint") == previous.get("source_fingerprint")
    same_manifestation = state.get("manifestation_id") == previous.get("manifestation_id")
    same_observation = (
        same_source_ref
        and same_fingerprint
        and same_manifestation
        and state.get("manifestation_status") == previous.get("manifestation_status")
        and state.get("availability") == previous.get("availability")
    )
    current_fingerprint = state.get("source_fingerprint", {}).get("value")

    if reason == "paper_associated":
        require(previous.get("paper_id") is None and state.get("paper_id") is not None, "/paper_id", "paper association must bind one previously unassociated asset")
        require(previous.get("manifestation_status") == "active" and previous.get("availability") == "available", "/manifestation_status", "paper association requires a current available manifestation")
        require(same_observation, "/source_fingerprint", "paper association cannot change source manifestation or availability")
    else:
        require(state.get("paper_id") == previous.get("paper_id"), "/paper_id", "only paper_associated may add a paper association")

    if reason == "same_digest_relink":
        require(not same_source_ref, "/source_ref", "same-digest relink must change the portable source reference")
        require(current_fingerprint == active_fingerprint, "/source_fingerprint", "same-digest relink must retain the active manifestation digest")
        require(state.get("manifestation_status") == "active" and state.get("availability") == "available", "/manifestation_status", "same-digest relink must restore an available active manifestation")
    elif reason == "changed_bytes_observed":
        require(same_source_ref, "/source_ref", "changed-byte observation cannot change the portable source reference")
        require(current_fingerprint != active_fingerprint, "/source_fingerprint", "changed-byte observation must differ from the active manifestation digest")
        require(state.get("manifestation_status") == "change_candidate" and state.get("availability") == "available", "/manifestation_status", "changed-byte observation must remain an available change candidate")
    elif reason == "source_available":
        require(same_source_ref, "/source_ref", "source availability observation cannot change the portable source reference")
        require(current_fingerprint == active_fingerprint, "/source_fingerprint", "source availability must restore the active manifestation digest")
        require(state.get("manifestation_status") == "active" and state.get("availability") == "available", "/availability", "source_available must restore an available active manifestation")
    elif reason in UNAVAILABLE_REASONS:
        require(same_source_ref, "/source_ref", "source unavailability observation cannot change the portable source reference")
        require(current_fingerprint == active_fingerprint, "/source_fingerprint", "source unavailability must retain the active manifestation digest")
        require(state.get("manifestation_status") == "active" and state.get("availability") == UNAVAILABLE_REASONS[reason], "/availability", "source unavailability reason does not match its projected state")
    elif reason != "paper_associated":
        require(False, "/reason", "source asset successor has an invalid transition reason")
    return failures


def _active_fingerprint_before(
    ordered: list[dict[str, Any]],
    index: int,
) -> str | None:
    active = next(
        (
            item
            for item in reversed(ordered[:index])
            if item.get("manifestation_status") == "active"
        ),
        None,
    )
    if active is None:
        return None
    value = active.get("source_fingerprint", {}).get("value")
    return value if isinstance(value, str) else None


def _parse_timestamp(value: object) -> datetime:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=UTC)


def _deduplicate(diagnostics: list[Diagnostic]) -> list[Diagnostic]:
    seen: set[tuple[str, str | None, str, str]] = set()
    result: list[Diagnostic] = []
    for item in diagnostics:
        key = (item.code, item.record_id, item.json_path, item.message)
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


__all__ = [
    "current_source_asset_heads",
    "source_asset_chain_diagnostics",
    "source_asset_projection",
]
