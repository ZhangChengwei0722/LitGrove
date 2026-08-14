"""Deterministic, fail-closed CI risk classification for G1 shadow mode."""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


CLASSIFICATION_SCHEMA_VERSION = "ci-risk-classification@1"
RULE_SCHEMA_VERSION = "ci-risk-rules@1"
DEFAULT_RULES_PATH = Path(__file__).with_name("ci-risk-rules.json")
FAIL_CLOSED_LEVELS = ("L3", "L4")
VALID_LEVELS = frozenset({"L0", "L2", "L3", "L4"})
SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class ClassifierError(RuntimeError):
    """Raised when a deterministic classification cannot be completed."""


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _with_digest(payload: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    result["digest"] = hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
    return result


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_canonical_json(value) + "\n", encoding="utf-8", newline="\n")


def _read_json(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ClassifierError(f"rules_unreadable:{type(error).__name__}") from error
    if not isinstance(value, Mapping):
        raise ClassifierError("rules_not_an_object")
    return value


def _string_list(value: Any, name: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, list) or (not allow_empty and not value):
        raise ClassifierError(f"rules_invalid_{name}")
    if any(not isinstance(item, str) or not item for item in value):
        raise ClassifierError(f"rules_invalid_{name}")
    return tuple(value)


def load_rules(path: Path = DEFAULT_RULES_PATH) -> dict[str, Any]:
    """Load and validate the versioned rule data without applying defaults."""

    raw = _read_json(path)
    if raw.get("schema_version") != RULE_SCHEMA_VERSION:
        raise ClassifierError("rules_schema_drift")
    rule_version = raw.get("rule_version")
    if not isinstance(rule_version, str) or not rule_version:
        raise ClassifierError("rules_invalid_version")

    docs_only_allowlist = _string_list(raw.get("docs_only_allowlist"), "docs_only_allowlist")
    ordinary_code_globs = _string_list(raw.get("ordinary_code_globs"), "ordinary_code_globs")
    ordinary_affected_tests = _string_list(
        raw.get("ordinary_affected_tests"), "ordinary_affected_tests", allow_empty=True
    )
    event_full_l3 = _string_list(raw.get("event_full_l3"), "event_full_l3", allow_empty=True)
    raw_risk_classes = raw.get("risk_classes")
    if not isinstance(raw_risk_classes, list) or not raw_risk_classes:
        raise ClassifierError("rules_invalid_risk_classes")

    risk_classes: list[dict[str, Any]] = []
    for item in raw_risk_classes:
        if not isinstance(item, Mapping):
            raise ClassifierError("rules_invalid_risk_class")
        code = item.get("code")
        if not isinstance(code, str) or not code:
            raise ClassifierError("rules_invalid_risk_code")
        levels = _string_list(item.get("levels"), f"risk_levels:{code}")
        if any(level not in VALID_LEVELS or level == "L0" for level in levels):
            raise ClassifierError(f"rules_invalid_risk_levels:{code}")
        globs = _string_list(item.get("globs"), f"risk_globs:{code}")
        priority = item.get("priority", 100)
        if not isinstance(priority, int) or priority < 0:
            raise ClassifierError(f"rules_invalid_risk_priority:{code}")
        risk_classes.append(
            {
                "code": code,
                "levels": tuple(levels),
                "globs": tuple(globs),
                "priority": priority,
            }
        )

    risk_classes.sort(key=lambda item: (item["priority"], item["code"]))
    return {
        "schema_version": RULE_SCHEMA_VERSION,
        "rule_version": rule_version,
        "docs_only_allowlist": docs_only_allowlist,
        "ordinary_code_globs": ordinary_code_globs,
        "ordinary_affected_tests": ordinary_affected_tests,
        "event_full_l3": event_full_l3,
        "risk_classes": tuple(risk_classes),
    }


def _matches(path: str, patterns: Iterable[str]) -> bool:
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)


def _validate_git_path(path: str) -> str:
    if not isinstance(path, str) or not path or "\\" in path or path.startswith("/"):
        raise ClassifierError("git_path_invalid")
    parts = path.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ClassifierError("git_path_invalid")
    return path


def _classify_path(path: str, rules: Mapping[str, Any]) -> dict[str, Any]:
    risk_matches = [
        {"code": item["code"], "levels": tuple(item["levels"])}
        for item in rules["risk_classes"]
        if _matches(path, item["globs"])
    ]
    if risk_matches:
        return {"category": "risk", "matches": tuple(risk_matches)}
    if _matches(path, rules["docs_only_allowlist"]):
        return {"category": "docs", "matches": ()}
    if _matches(path, rules["ordinary_code_globs"]):
        return {"category": "ordinary", "matches": ()}
    return {"category": "unknown", "matches": ()}


def _resolve_commit(repo: Path, ref: str) -> str:
    if not isinstance(ref, str) or not ref:
        raise ClassifierError("commit_ref_missing")
    result = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--verify", f"{ref}^{{commit}}"],
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        raise ClassifierError("commit_ref_unresolved")
    try:
        resolved = result.stdout.decode("ascii").strip()
    except UnicodeDecodeError as error:
        raise ClassifierError("commit_ref_not_ascii") from error
    if not SHA_RE.fullmatch(resolved):
        raise ClassifierError("commit_ref_not_full_sha")
    return resolved


def _git_diff(repo: Path, base: str, head: str) -> bytes:
    result = subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "diff",
            "--name-status",
            "--find-renames=50%",
            "-z",
            "--no-ext-diff",
            "--no-textconv",
            base,
            head,
            "--",
        ],
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        raise ClassifierError("git_diff_failed")
    return result.stdout


def _parse_diff(raw: bytes) -> list[dict[str, Any]]:
    try:
        tokens = raw.decode("utf-8").split("\0")
    except UnicodeDecodeError as error:
        raise ClassifierError("git_path_not_utf8") from error
    if tokens and tokens[-1] == "":
        tokens.pop()
    changes: list[dict[str, Any]] = []
    index = 0
    while index < len(tokens):
        status = tokens[index]
        index += 1
        if "\t" in status:
            status, inline_path = status.split("\t", 1)
        else:
            inline_path = None
        if not status:
            raise ClassifierError("git_diff_status_missing")
        status_code = status[0]
        if status_code in {"R", "C"}:
            if index + 1 >= len(tokens):
                raise ClassifierError("git_diff_rename_pair_missing")
            old_path = _validate_git_path(tokens[index])
            new_path = _validate_git_path(tokens[index + 1])
            index += 2
            change = "renamed" if status_code == "R" else "copied"
        else:
            if inline_path is not None:
                path = inline_path
            elif index < len(tokens):
                path = tokens[index]
                index += 1
            else:
                raise ClassifierError("git_diff_path_missing")
            path = _validate_git_path(path)
            old_path = path if status_code == "D" else None
            new_path = path if status_code != "D" else None
            change = {
                "A": "added",
                "D": "deleted",
                "M": "modified",
                "T": "type_changed",
                "U": "unmerged",
            }.get(status_code, "unknown")
        changes.append(
            {
                "change": change,
                "old_path": old_path,
                "new_path": new_path,
            }
        )
    changes.sort(key=lambda item: (item["old_path"] or "", item["new_path"] or "", item["change"]))
    return changes


def _add_reason(
    reasons: dict[str, dict[str, Any]],
    code: str,
    paths: Iterable[str],
    levels: Iterable[str],
) -> None:
    item = reasons.setdefault(code, {"code": code, "paths": set(), "levels": set()})
    item["paths"].update(path for path in paths if path)
    item["levels"].update(level for level in levels if level in VALID_LEVELS)


def _reason_list(reasons: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "code": code,
            "levels": sorted(item["levels"], key=lambda level: (len(level), level)),
            "paths": sorted(item["paths"]),
        }
        for code, item in sorted(reasons.items())
    ]


def _collapse_levels(levels: Iterable[str]) -> list[str]:
    selected = set(levels)
    if "L4" in selected:
        return ["L3", "L4"]
    if "L3" in selected:
        return ["L3"]
    if "L2" in selected:
        return ["L2"]
    return ["L0"]


def _normalise_event(event: str | None) -> str:
    if not event:
        return "pull_request"
    return event.strip().lower().replace("_", "-")


def _event_requires_full_l3(event: str, branch: str | None, rules: Mapping[str, Any]) -> bool:
    configured = {_normalise_event(value) for value in rules["event_full_l3"]}
    if event in configured:
        return True
    return event in {"push", "pull-request"} and branch == "main"


def _event_is_known(event: str) -> bool:
    return event in {
        "pull-request",
        "push",
        "schedule",
        "workflow-dispatch",
        "main",
        "release",
        "release-candidate",
    }


def _failure_result(rule_version: str, event: str | None, error_code: str) -> dict[str, Any]:
    payload = {
        "schema_version": CLASSIFICATION_SCHEMA_VERSION,
        "status": "fail_closed",
        "rule_version": rule_version,
        "base": None,
        "head": None,
        "event": _normalise_event(event),
        "branch": None,
        "changed_paths": [],
        "classification_reasons": [
            {"code": "classifier_failure", "levels": list(FAIL_CLOSED_LEVELS), "paths": []}
        ],
        "selected_validation_levels": list(FAIL_CLOSED_LEVELS),
        "affected_tests": [],
        "error_code": error_code,
    }
    return _with_digest(payload)


def classify(
    repo: Path,
    base: str,
    head: str,
    *,
    event: str = "pull_request",
    branch: str | None = None,
    rules_path: Path = DEFAULT_RULES_PATH,
) -> dict[str, Any]:
    """Classify the tree diff between two commits into deterministic levels."""

    repo = Path(repo).resolve()
    if not repo.is_dir():
        raise ClassifierError("repository_missing")
    rules = load_rules(Path(rules_path).resolve())
    resolved_base = _resolve_commit(repo, base)
    resolved_head = _resolve_commit(repo, head)
    changes = _parse_diff(_git_diff(repo, resolved_base, resolved_head))
    normalised_event = _normalise_event(event)
    normalised_branch = branch.strip() if isinstance(branch, str) and branch.strip() else None
    reasons: dict[str, dict[str, Any]] = {}
    levels: set[str] = set()

    for change in changes:
        old_path = change["old_path"]
        new_path = change["new_path"]
        if change["change"] in {"renamed", "copied"}:
            old_class = _classify_path(old_path, rules)
            new_class = _classify_path(new_path, rules)
            for path, path_class in ((old_path, old_class), (new_path, new_class)):
                if path_class["category"] == "risk":
                    for match in path_class["matches"]:
                        _add_reason(reasons, match["code"], [path], match["levels"])
                        levels.update(match["levels"])
                elif path_class["category"] == "docs":
                    _add_reason(reasons, "docs_only_allowlist", [path], ["L0"])
                    levels.add("L0")
                elif path_class["category"] == "ordinary":
                    _add_reason(reasons, "ordinary_code_change", [path], ["L2"])
                    levels.add("L2")
                else:
                    _add_reason(reasons, "unknown_path", [path], ["L3"])
                    levels.add("L3")

            categories = {old_class["category"], new_class["category"]}
            if "unknown" in categories:
                _add_reason(reasons, "unknown_rename", [old_path, new_path], ["L3"])
                levels.add("L3")
            elif categories == {"docs"}:
                _add_reason(reasons, "safe_rename", [old_path, new_path], ["L0"])
            elif categories == {"ordinary"}:
                _add_reason(reasons, "safe_rename", [old_path, new_path], ["L2"])
            else:
                unsafe_levels = {"L3"}
                for path_class in (old_class, new_class):
                    for match in path_class["matches"]:
                        unsafe_levels.update(match["levels"])
                _add_reason(reasons, "unsafe_rename", [old_path, new_path], unsafe_levels)
                levels.update(unsafe_levels)
        else:
            path = new_path or old_path
            path_class = _classify_path(path, rules)
            if path_class["category"] == "risk":
                for match in path_class["matches"]:
                    _add_reason(reasons, match["code"], [path], match["levels"])
                    levels.update(match["levels"])
            elif path_class["category"] == "docs":
                _add_reason(reasons, "docs_only_allowlist", [path], ["L0"])
                levels.add("L0")
            elif path_class["category"] == "ordinary":
                _add_reason(reasons, "ordinary_code_change", [path], ["L2"])
                levels.add("L2")
            else:
                _add_reason(reasons, "unknown_path", [path], ["L3"])
                levels.add("L3")

    if not changes:
        _add_reason(reasons, "no_changed_paths", [], ["L0"])
        levels.add("L0")
    if _event_requires_full_l3(normalised_event, normalised_branch, rules):
        _add_reason(reasons, "full_l3_event", [], ["L3"])
        levels.add("L3")
    if not _event_is_known(normalised_event):
        _add_reason(reasons, "unknown_event", [], FAIL_CLOSED_LEVELS)
        levels.update(FAIL_CLOSED_LEVELS)

    changed_paths = [
        {
            "change": change["change"],
            "old_path": change["old_path"],
            "new_path": change["new_path"],
        }
        for change in changes
    ]
    reason_list = _reason_list(reasons)
    changed_test_paths = sorted(
        {
            path
            for change in changes
            for path in (change["old_path"], change["new_path"])
            if path and (path == "tests" or path.startswith("tests/"))
        }
    )
    if not changed_test_paths and any(reason["code"] == "ordinary_code_change" for reason in reason_list):
        changed_test_paths = list(rules["ordinary_affected_tests"])

    payload = {
        "schema_version": CLASSIFICATION_SCHEMA_VERSION,
        "status": "classified",
        "rule_version": rules["rule_version"],
        "base": resolved_base,
        "head": resolved_head,
        "event": normalised_event,
        "branch": normalised_branch,
        "changed_paths": changed_paths,
        "classification_reasons": reason_list,
        "selected_validation_levels": _collapse_levels(levels),
        "affected_tests": changed_test_paths,
    }
    return _with_digest(payload)


def _rule_version_for_failure(path: Path) -> str:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return "unavailable"
    version = value.get("rule_version") if isinstance(value, Mapping) else None
    return version if isinstance(version, str) and version else "unavailable"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--base", required=True, help="merge-base or base commit/ref")
    parser.add_argument("--head", required=True, help="head commit/ref")
    parser.add_argument("--event", default="pull_request")
    parser.add_argument("--branch")
    parser.add_argument("--rules", type=Path, default=DEFAULT_RULES_PATH)
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = classify(
            args.repo,
            args.base,
            args.head,
            event=args.event,
            branch=args.branch,
            rules_path=args.rules,
        )
    except ClassifierError as error:
        result = _failure_result(
            _rule_version_for_failure(args.rules),
            args.event,
            str(error),
        )
        print(f"ci-risk-classifier: {error}", file=sys.stderr)
        return_code = 2
    except (OSError, ValueError, TypeError) as error:
        result = _failure_result(
            _rule_version_for_failure(args.rules),
            args.event,
            type(error).__name__,
        )
        print(f"ci-risk-classifier: {type(error).__name__}", file=sys.stderr)
        return_code = 2
    else:
        return_code = 0

    try:
        if args.output:
            _write_json(args.output, result)
        else:
            print(_canonical_json(result))
    except OSError as error:
        print(f"ci-risk-classifier: output_write_failed:{type(error).__name__}", file=sys.stderr)
        return 2
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
