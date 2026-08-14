from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from tools.ci_risk_classifier import classify


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPOSITORY_ROOT / "tools" / "ci_risk_classifier.py"
RULES_PATH = REPOSITORY_ROOT / "tools" / "ci-risk-rules.json"
CASES_PATH = REPOSITORY_ROOT / "tests" / "fixtures" / "release-governance" / "ci-risk-cases.json"


def _load_cases() -> dict[str, Any]:
    return json.loads(CASES_PATH.read_text(encoding="utf-8"))


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _write_tree(repo: Path, tree: dict[str, str]) -> None:
    for relative, content in tree.items():
        path = repo / Path(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")


def _materialise_case(case: dict[str, Any], root: Path) -> tuple[Path, str, str]:
    repo = root / case["id"]
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.name", "Synthetic Fixture")
    _git(repo, "config", "user.email", "synthetic@example.invalid")

    base_tree = case["base"]
    head_tree = case["head"]
    _write_tree(repo, base_tree)
    _git(repo, "add", "--all")
    _git(repo, "commit", "--allow-empty", "-m", "synthetic base")
    base_sha = _git(repo, "rev-parse", "HEAD")

    for relative in set(base_tree) - set(head_tree):
        (repo / Path(relative)).unlink()
    _write_tree(repo, head_tree)
    _git(repo, "add", "--all")
    _git(repo, "commit", "--allow-empty", "-m", "synthetic head")
    head_sha = _git(repo, "rev-parse", "HEAD")
    return repo, base_sha, head_sha


def _classify_case(case: dict[str, Any], root: Path) -> dict[str, Any]:
    repo, base_sha, head_sha = _materialise_case(case, root)
    return classify(
        repo,
        base_sha,
        head_sha,
        event=case.get("event", "pull_request"),
        branch=case.get("branch"),
        rules_path=RULES_PATH,
    )


def _reason_codes(result: dict[str, Any]) -> set[str]:
    return {reason["code"] for reason in result["classification_reasons"]}


def _digest_for(result: dict[str, Any]) -> str:
    payload = dict(result)
    payload.pop("digest")
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def test_fixture_is_versioned_and_synthetic() -> None:
    fixture = _load_cases()
    assert fixture["schema_version"] == "ci-risk-cases@1"
    assert fixture["fixture_version"] == 1
    assert fixture["fixture_origin"] == "synthetic_from_scratch"
    assert fixture["rule_version"] == "g1-risk-v1"
    cases = fixture["cases"]
    assert len(cases) >= 20
    assert len({case["id"] for case in cases}) == len(cases)
    assert all(case["case_version"] == 1 for case in cases)
    assert all("synthetic_from_scratch" in json.dumps(case) for case in cases if case["base"] or case["head"])


def test_named_governance_release_and_validation_paths_are_explicitly_l3(tmp_path: Path) -> None:
    fixture = _load_cases()
    cases = {case["id"]: case for case in fixture["cases"]}
    named = {
        "AGENTS.md": ("agents_governance_modify", "governance_change"),
        "CHANGELOG.md": ("changelog_release_modify", "release_change"),
        "docs/test-validation.md": ("validation_policy_modify", "validation_governance_change"),
        "tools/run_validation.py": ("validation_runner_modify", "validation_governance_change"),
        "tools/test-shards.json": ("validation_shards_modify", "validation_governance_change"),
        "CONTRIBUTING.md": ("contributing_governance_modify", "governance_change"),
        "docs/contributor-guide.md": ("contributor_guide_governance_modify", "governance_change"),
    }
    for relative_path, (case_id, reason_code) in named.items():
        case = cases[case_id]
        assert case["head"] == {relative_path: next(iter(case["head"].values()))}
        result = _classify_case(case, tmp_path)
        assert result["selected_validation_levels"] == ["L3"], relative_path
        assert reason_code in _reason_codes(result), relative_path


def test_readme_and_ordinary_non_policy_docs_remain_l0(tmp_path: Path) -> None:
    cases = _load_cases()["cases"]
    for case_id in ("readme_modify", "ordinary_docs_modify"):
        case = next(case for case in cases if case["id"] == case_id)
        result = _classify_case(case, tmp_path)
        assert result["selected_validation_levels"] == ["L0"], case_id
        assert _reason_codes(result) == {"docs_only_allowlist"}


def test_all_synthetic_cases_match_expected_levels_and_reasons(tmp_path: Path) -> None:
    fixture = _load_cases()
    for case in fixture["cases"]:
        result = _classify_case(case, tmp_path)
        expected = case["expected"]
        assert result["status"] == "classified", case["id"]
        assert result["rule_version"] == fixture["rule_version"], case["id"]
        assert result["selected_validation_levels"] == expected["levels"], case["id"]
        assert _reason_codes(result) == set(expected["reason_codes"]), case["id"]


def test_rename_output_contains_both_tree_sides(tmp_path: Path) -> None:
    case = next(case for case in _load_cases()["cases"] if case["id"] == "safe_docs_rename")
    result = _classify_case(case, tmp_path)
    assert result["changed_paths"] == [
        {
            "change": "renamed",
            "old_path": "docs/old-guide.md",
            "new_path": "docs/new-guide.md",
        }
    ]


def test_unknown_rename_and_high_risk_never_fall_below_l3(tmp_path: Path) -> None:
    cases = _load_cases()["cases"]
    guarded = [case for case in cases if "L3" in case["expected"]["levels"]]
    for case in guarded:
        result = _classify_case(case, tmp_path)
        assert "L3" in result["selected_validation_levels"], case["id"]


def test_scale_and_benchmark_always_include_l4(tmp_path: Path) -> None:
    cases = _load_cases()["cases"]
    scale_cases = [case for case in cases if "L4" in case["expected"]["levels"]]
    assert scale_cases
    for case in scale_cases:
        result = _classify_case(case, tmp_path)
        assert result["selected_validation_levels"] == ["L3", "L4"], case["id"]
        assert "scale_or_benchmark" in _reason_codes(result), case["id"]


def test_digest_is_deterministic_and_binds_canonical_payload(tmp_path: Path) -> None:
    case = next(case for case in _load_cases()["cases"] if case["id"] == "ordinary_code_add")
    repo, base_sha, head_sha = _materialise_case(case, tmp_path)
    first = classify(repo, base_sha, head_sha, event=case["event"], rules_path=RULES_PATH)
    second = classify(repo, base_sha, head_sha, event=case["event"], rules_path=RULES_PATH)
    assert first == second
    assert first["digest"] == _digest_for(first)


def test_event_scope_can_raise_an_ordinary_change_to_full_l3(tmp_path: Path) -> None:
    case = next(case for case in _load_cases()["cases"] if case["id"] == "main_event")
    result = _classify_case(case, tmp_path)
    assert result["selected_validation_levels"] == ["L3"]
    assert "full_l3_event" in _reason_codes(result)


def test_cli_success_emits_parseable_canonical_json(tmp_path: Path) -> None:
    case = next(case for case in _load_cases()["cases"] if case["id"] == "workflow_add")
    repo, base_sha, head_sha = _materialise_case(case, tmp_path)
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--repo",
            str(repo),
            "--base",
            base_sha,
            "--head",
            head_sha,
            "--event",
            case["event"],
        ],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr
    parsed = json.loads(result.stdout)
    assert parsed["selected_validation_levels"] == ["L3"]
    assert parsed["digest"] == _digest_for(parsed)


def test_cli_failure_writes_fail_closed_evidence(tmp_path: Path) -> None:
    case = next(case for case in _load_cases()["cases"] if case["id"] == "ordinary_code_add")
    repo, _, head_sha = _materialise_case(case, tmp_path)
    output = tmp_path / "failure.json"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--repo",
            str(repo),
            "--base",
            "missing-base-ref",
            "--head",
            head_sha,
            "--output",
            str(output),
        ],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 2
    parsed = json.loads(output.read_text(encoding="utf-8"))
    assert parsed["status"] == "fail_closed"
    assert parsed["selected_validation_levels"] == ["L3", "L4"]
    assert "classifier_failure" in _reason_codes(parsed)
    assert parsed["digest"] == _digest_for(parsed)


def test_cli_rules_schema_drift_fails_closed(tmp_path: Path) -> None:
    case = next(case for case in _load_cases()["cases"] if case["id"] == "ordinary_code_add")
    repo, base_sha, head_sha = _materialise_case(case, tmp_path)
    bad_rules = tmp_path / "drifted-rules.json"
    bad_rules.write_text('{"schema_version":"ci-risk-rules@unexpected"}\n', encoding="utf-8", newline="\n")
    output = tmp_path / "drift.json"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--repo",
            str(repo),
            "--base",
            base_sha,
            "--head",
            head_sha,
            "--rules",
            str(bad_rules),
            "--output",
            str(output),
        ],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 2
    parsed = json.loads(output.read_text(encoding="utf-8"))
    assert parsed["status"] == "fail_closed"
    assert parsed["selected_validation_levels"] == ["L3", "L4"]
    assert parsed["digest"] == _digest_for(parsed)
