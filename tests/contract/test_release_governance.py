from __future__ import annotations

import base64
import copy
import csv
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

from tools.release_governance import (
    ARTIFACT_SCHEMA_VERSION,
    GovernanceInputError,
    INSTALLED_SCHEMA_VERSION,
    MAX_SAFE_INTEGER,
    OPERATION_SCHEMA_VERSION,
    PUBLICATION_SCHEMA_VERSION,
    build_operation_manifest,
    build_artifact_manifest,
    build_installed_manifest,
    build_publication_activation,
    build_publication_manifests,
    canonical_digest,
    canonical_json,
    canonical_json_bytes,
    collect_reachable_history,
    main,
    verify_artifact_manifest,
    verify_canonical_value,
    verify_installed_manifest,
    verify_publication_authority,
    verify_operation_manifest,
    verify_publication_activation,
    verify_reachable_history,
    verify_release_contract,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = REPOSITORY_ROOT / "tests" / "fixtures" / "release-governance" / "release-policy-cases.json"
SOURCE_COMMIT = "1111111111111111111111111111111111111111"
VERSION = "0.1.1"


def _fixture() -> dict:
    value = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    assert value["fixture_origin"] == "synthetic_from_scratch"
    return value


def _write_archives(root: Path) -> tuple[Path, Path]:
    wheel = root / f"synthetic-{VERSION}-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in (
            ("research_kb/__init__.py", b"__version__ = 'synthetic'\n"),
            (f"synthetic_core-{VERSION}.dist-info/METADATA", f"Metadata-Version: 2.1\nName: synthetic-core\nVersion: {VERSION}\n".encode()),
            (f"synthetic_core-{VERSION}.dist-info/WHEEL", b"Wheel-Version: 1.0\nGenerator: synthetic\nRoot-Is-Purelib: true\nTag: py3-none-any\n"),
        ):
            archive.writestr(name, content)

    sdist = root / f"synthetic-{VERSION}.tar.gz"
    with tarfile.open(sdist, "w:gz") as archive:
        content = b"synthetic source archive\n"
        info = tarfile.TarInfo(f"synthetic-{VERSION}/README.txt")
        info.size = len(content)
        info.mtime = 0
        archive.addfile(info, io.BytesIO(content))
    return wheel, sdist


def _write_installed(root: Path, capability: Path, artifact: Path) -> dict:
    site_packages = root / "Lib" / "site-packages"
    package = site_packages / "research_kb"
    dist_info = site_packages / f"synthetic_core-{VERSION}.dist-info"
    script = root / "Scripts" / "research-kb.exe"
    package.mkdir(parents=True)
    dist_info.mkdir(parents=True)
    script.parent.mkdir(parents=True)
    (package / "__init__.py").write_bytes(b"__version__ = 'synthetic'\n")
    script.write_bytes(b"synthetic launcher\n")
    (dist_info / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: synthetic-core\nVersion: {VERSION}\n",
        encoding="utf-8",
        newline="\n",
    )
    (dist_info / "WHEEL").write_text(
        "Wheel-Version: 1.0\nGenerator: synthetic\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        encoding="utf-8",
        newline="\n",
    )
    rows = []
    for path in (script, package / "__init__.py", dist_info / "METADATA", dist_info / "WHEEL"):
        relative = Path(os.path.relpath(path, site_packages)).as_posix()
        encoded = base64.urlsafe_b64encode(__import__("hashlib").sha256(path.read_bytes()).digest()).decode().rstrip("=")
        rows.append((relative, f"sha256={encoded}", str(path.stat().st_size)))
    record_path = dist_info / "RECORD"
    rows.append((record_path.relative_to(site_packages).as_posix(), "", ""))
    record_path.write_text("", encoding="utf-8", newline="\n")
    with record_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerows(rows[:-1])
        writer.writerow(rows[-1])
    capability.write_text(
        json.dumps(
            {
                "interface_version": "1.0",
                "core": {"version": VERSION, "contract_versions": ["1.0"], "layout_versions": ["1.0"]},
                "features": {"synthetic": True},
            },
            indent=2,
            sort_keys=False,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return {"artifact": artifact}


def _materialize(tmp_path: Path) -> tuple[dict, dict, Path, Path, dict, dict]:
    fixture = _fixture()
    dist = tmp_path / "dist"
    dist.mkdir()
    wheel, sdist = _write_archives(dist)
    artifact = build_artifact_manifest(
        dist,
        repository=fixture["candidate"]["repository"],
        source_commit=fixture["candidate"]["source_commit"],
        workflow_run_id=fixture["candidate"]["workflow_run_id"],
        workflow_run_attempt=fixture["candidate"]["workflow_run_attempt"],
        version=fixture["candidate"]["version"],
        artifact_name=fixture["candidate"]["artifact_name"],
    )
    install = tmp_path / "installed"
    capability = tmp_path / "capability.json"
    _write_installed(install, capability, wheel)
    installed = build_installed_manifest(
        install,
        candidate=artifact["candidate"],
        capability_json=capability,
        artifact=wheel,
    )
    authority = copy.deepcopy(fixture["publication_authority"])
    accepted_digests = {
        item["filename"]: item["sha256"] for item in artifact["artifacts"]
    }
    authority["publication"]["accepted_artifact_digests"] = accepted_digests
    activation = copy.deepcopy(fixture["publication_activation"])
    activation["activation"]["accepted_artifact_digests"] = accepted_digests
    activation["activation"]["authority_manifest_sha256"] = canonical_digest(authority)
    return artifact, installed, install, capability, activation, authority


def _case(value: dict, case_id: str) -> dict:
    return next(case for case in value["cases"] if case["id"] == case_id)


def test_canonical_json_is_stable_and_digest_is_order_independent() -> None:
    assert canonical_json({"b": 2, "a": [True, "x"]}) == '{"a":[true,"x"],"b":2}'
    assert canonical_digest({"a": 1, "b": 2}) == canonical_digest({"b": 2, "a": 1})


def test_canonical_json_uses_utf16_order_raw_unicode_and_no_digest_newline() -> None:
    value = {"𝄞": 1, "€": 2, "דּ": 3, "text": "café/\n\"\\\u0000"}
    assert canonical_json(value) == '{"text":"café/\\n\\\"\\\\\\u0000","€":2,"𝄞":1,"דּ":3}'
    encoded = canonical_json_bytes(value)
    assert b"\xc3\xa9" in encoded
    assert not encoded.endswith(b"\n")
    assert canonical_json({"min": -MAX_SAFE_INTEGER, "max": MAX_SAFE_INTEGER}) == (
        '{"max":9007199254740991,"min":-9007199254740991}'
    )


@pytest.mark.parametrize(
    "value",
    [
        {"value": 1.5},
        {"value": float("nan")},
        {"value": MAX_SAFE_INTEGER + 1},
        {"value": -MAX_SAFE_INTEGER - 1},
        {"value": "\ud800"},
        {"\ud800": "value"},
        {1: "value"},
    ],
)
def test_canonical_json_rejects_out_of_domain_values(value: object) -> None:
    assert not verify_canonical_value(value).ok


def test_manifest_rejects_noncanonical_input(tmp_path: Path) -> None:
    artifact, _, _, _, _, _ = _materialize(tmp_path)
    mutated = copy.deepcopy(artifact)
    mutated["noncanonical_float"] = 1.0
    result = verify_artifact_manifest(mutated, expected=artifact)
    assert not result.ok
    assert "invalid_canonical_value" in result.codes


@pytest.mark.parametrize(
    "raw",
    (
        '{"schema_version":"first","schema_version":"second"}',
        '{"schema_version":NaN}',
    ),
)
def test_cli_rejects_ambiguous_json_input(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], raw: str
) -> None:
    manifest_path = tmp_path / "ambiguous.json"
    manifest_path.write_text(raw, encoding="utf-8", newline="\n")

    assert main(["verify-artifact", "--manifest", str(manifest_path)]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is False
    assert {item["code"] for item in result["errors"]} == {"invalid_input"}


def test_positive_synthetic_release_contract(tmp_path: Path) -> None:
    artifact, installed, install, capability, activation, authority = _materialize(tmp_path)
    result = verify_release_contract(
        artifact,
        installed,
        expected=authority,
        publication_activation=activation,
        downloaded_artifact_dir=tmp_path / "dist",
    )
    assert result.ok, result.to_dict()
    assert artifact["schema_version"] == ARTIFACT_SCHEMA_VERSION
    assert installed["schema_version"] == INSTALLED_SCHEMA_VERSION
    assert activation["schema_version"] == PUBLICATION_SCHEMA_VERSION
    assert verify_artifact_manifest(artifact, expected=artifact, artifact_paths=tmp_path / "dist").ok
    assert verify_installed_manifest(installed, expected=artifact, installed_root=install, capability_output=capability).ok
    assert any(item["path"] == "Scripts/research-kb.exe" for item in installed["distribution"]["record_entries"])


def test_installed_record_path_that_escapes_prefix_fails_closed(tmp_path: Path) -> None:
    artifact, _, install, capability, _, _ = _materialize(tmp_path)
    record_path = next(install.rglob("RECORD"))
    outside = tmp_path / "outside.exe"
    outside.write_bytes(b"outside prefix\n")
    encoded = base64.urlsafe_b64encode(__import__("hashlib").sha256(outside.read_bytes()).digest()).decode().rstrip("=")
    with record_path.open("a", encoding="utf-8", newline="") as stream:
        csv.writer(stream, lineterminator="\n").writerow(("../../../outside.exe", f"sha256={encoded}", str(outside.stat().st_size)))

    with pytest.raises(GovernanceInputError, match="RECORD path escapes installed root"):
        build_installed_manifest(
            install,
            candidate=artifact["candidate"],
            capability_json=capability,
        )


def test_installed_record_alias_duplicate_fails_closed(tmp_path: Path) -> None:
    artifact, _, install, capability, _, _ = _materialize(tmp_path)
    package_file = install / "Lib" / "site-packages" / "research_kb" / "__init__.py"
    record_path = next(install.rglob("RECORD"))
    encoded = base64.urlsafe_b64encode(
        __import__("hashlib").sha256(package_file.read_bytes()).digest()
    ).decode().rstrip("=")
    with record_path.open("a", encoding="utf-8", newline="") as stream:
        csv.writer(stream, lineterminator="\n").writerow(
            ("synthetic/../research_kb/__init__.py", f"sha256={encoded}", str(package_file.stat().st_size))
        )

    with pytest.raises(GovernanceInputError, match="duplicate normalized RECORD path"):
        build_installed_manifest(
            install,
            candidate=artifact["candidate"],
            capability_json=capability,
        )


def test_fixture_roster_executes_every_positive_and_negative_case(tmp_path: Path) -> None:
    artifact, installed, install, capability, activation, authority = _materialize(tmp_path)
    for case in _fixture()["cases"]:
        case_id = case["id"]
        if case["target"] == "release":
            result = verify_release_contract(
                artifact,
                installed,
                expected=authority,
                publication_activation=activation,
                downloaded_artifact_dir=tmp_path / "dist",
            )
        elif case["target"] == "artifact":
            mutated = copy.deepcopy(artifact)
            if case["mutation"] == "replace_first_artifact_digest":
                mutated["artifacts"][0]["sha256"] = "f" * 64
            elif case["mutation"] == "replace_candidate_commit":
                mutated["candidate"]["source_commit"] = "2" * 40
            elif case["mutation"] == "replace_candidate_run":
                mutated["candidate"]["workflow_run_id"] = "71002"
            elif case["mutation"] == "replace_candidate_attempt":
                mutated["candidate"]["workflow_run_attempt"] = "2"
            elif case["mutation"] == "increment_build_attempts":
                mutated["build_once"]["attempts"] = 2
            elif case["mutation"] == "replace_cache_candidate_key":
                mutated["cache_identity"]["candidate_key"] = "synthetic.example/LitGrove:other-candidate"
            elif case["mutation"] == "replace_cache_candidate_attempt":
                mutated["cache_identity"]["workflow_run_attempt"] = "2"
            else:
                raise AssertionError(f"unhandled fixture mutation: {case_id}")
            result = verify_artifact_manifest(mutated, expected=artifact)
        elif case["target"] == "installed":
            mutated = copy.deepcopy(installed)
            if case["mutation"] == "add_unrecorded_file":
                (install / "fixture-unexpected.py").write_text("synthetic\n", encoding="utf-8", newline="\n")
                result = verify_installed_manifest(mutated, installed_root=install, capability_output=capability)
            elif case["mutation"] == "mark_source_tree_import":
                mutated["runtime"]["source_tree_import"] = True
                result = verify_installed_manifest(mutated)
            else:
                raise AssertionError(f"unhandled fixture mutation: {case_id}")
        elif case["target"] == "publication":
            mutated = copy.deepcopy(activation)
            if case["mutation"] == "disable_publication":
                mutated["activation"]["enabled"] = False
            elif case["mutation"] == "replace_accepted_run":
                mutated["activation"]["accepted_run_id"] = "71002"
            elif case["mutation"] == "replace_accepted_attempt":
                mutated["activation"]["accepted_run_attempt"] = "2"
            elif case["mutation"] == "replace_accepted_digest":
                first_name = next(iter(mutated["activation"]["accepted_artifact_digests"]))
                mutated["activation"]["accepted_artifact_digests"][first_name] = "e" * 64
            else:
                raise AssertionError(f"unhandled fixture mutation: {case_id}")
            result = verify_publication_activation(
                mutated,
                expected=authority,
                downloaded_manifest=artifact,
                downloaded_artifact_dir=tmp_path / "dist",
            )
        elif case["target"] == "canonical":
            canonical_values = {
                "unicode_utf16_key_order": {"𝄞": 1, "€": 2, "דּ": 3},
                "float_value": {"value": 1.5},
                "out_of_range_integer": {"value": MAX_SAFE_INTEGER + 1},
                "unpaired_surrogate": {"value": "\ud800"},
                "non_string_key": {1: "value"},
            }
            result = verify_canonical_value(canonical_values[case["mutation"]])
            if case["mutation"] == "unicode_utf16_key_order":
                assert canonical_json(canonical_values[case["mutation"]]) == '{"€":2,"𝄞":1,"דּ":3}'
        else:
            raise AssertionError(f"unhandled fixture target: {case_id}")
        assert result.ok is (case["expected"] == "ok"), (case_id, result.to_dict())


@pytest.mark.parametrize("case_id,code", [("wrong_digest", "wrong_artifact_digest"), ("same_version_substituted_bytes", "same_version_substituted_bytes")])
def test_artifact_digest_and_same_version_substitution_fail_closed(tmp_path: Path, case_id: str, code: str) -> None:
    artifact, _, _, _, _, _ = _materialize(tmp_path)
    mutated = copy.deepcopy(artifact)
    mutated["artifacts"][0]["sha256"] = "f" * 64
    result = verify_artifact_manifest(mutated, expected=artifact)
    assert not result.ok
    assert code in result.codes
    assert _case(_fixture(), case_id)["expected"] == "fail"


def test_wrong_commit_run_and_rebuild_attempt_fail_closed(tmp_path: Path) -> None:
    artifact, _, _, _, _, _ = _materialize(tmp_path)
    wrong_commit = copy.deepcopy(artifact)
    wrong_commit["candidate"]["source_commit"] = "2" * 40
    assert "wrong_commit" in verify_artifact_manifest(wrong_commit, expected=artifact).codes

    wrong_run = copy.deepcopy(artifact)
    wrong_run["candidate"]["workflow_run_id"] = "71002"
    assert "wrong_run" in verify_artifact_manifest(wrong_run, expected=artifact).codes

    wrong_attempt = copy.deepcopy(artifact)
    wrong_attempt["candidate"]["workflow_run_attempt"] = "2"
    assert "wrong_run_attempt" in verify_artifact_manifest(wrong_attempt, expected=artifact).codes

    rebuild = copy.deepcopy(artifact)
    rebuild["build_once"]["attempts"] = 2
    assert "rebuild_attempt" in verify_artifact_manifest(rebuild, expected=artifact).codes
    assert _case(_fixture(), "rebuild_attempt")["mutation"] == "increment_build_attempts"


def test_unexpected_installed_file_and_source_import_fail_closed(tmp_path: Path) -> None:
    _, installed, install, capability, _, _ = _materialize(tmp_path)
    source_module = tmp_path / "src" / "research_kb" / "__init__.py"
    source_module.parent.mkdir(parents=True)
    source_module.write_text("synthetic\n", encoding="utf-8", newline="\n")
    source_manifest = build_installed_manifest(
        install,
        candidate=installed["candidate"],
        capability_json=capability,
        module_path=source_module,
    )
    assert source_manifest["runtime"]["source_tree_import"] is True
    assert "source_tree_import" in verify_installed_manifest(source_manifest).codes

    (install / "unexpected.py").write_text("synthetic\n", encoding="utf-8", newline="\n")
    result = verify_installed_manifest(installed, installed_root=install, capability_output=capability)
    assert not result.ok
    assert "unexpected_installed_file" in result.codes

    source_import = copy.deepcopy(installed)
    source_import["runtime"]["source_tree_import"] = True
    result = verify_installed_manifest(source_import)
    assert not result.ok
    assert "source_tree_import" in result.codes


def test_cross_candidate_cache_fails_closed(tmp_path: Path) -> None:
    artifact, _, _, _, _, _ = _materialize(tmp_path)
    mutated = copy.deepcopy(artifact)
    mutated["cache_identity"]["candidate_key"] = "synthetic.example/LitGrove:other-candidate"
    result = verify_artifact_manifest(mutated, expected=artifact)
    assert not result.ok
    assert "cross_candidate_cache" in result.codes


def test_publication_activation_requires_exact_future_tuple(tmp_path: Path) -> None:
    artifact, _, _, _, activation, authority = _materialize(tmp_path)
    assert verify_publication_activation(
        activation,
        expected=authority,
        downloaded_manifest=artifact,
        downloaded_artifact_dir=tmp_path / "dist",
    ).ok

    assert verify_publication_activation(
        activation,
        downloaded_manifest=artifact,
        downloaded_artifact_dir=tmp_path / "dist",
    ).codes == ("missing_expected_authority",)

    disabled = copy.deepcopy(activation)
    disabled["activation"]["enabled"] = False
    result = verify_publication_activation(
        disabled,
        expected=authority,
        downloaded_manifest=artifact,
        downloaded_artifact_dir=tmp_path / "dist",
    )
    assert not result.ok
    assert "publication_not_authorized" in result.codes

    wrong_run = copy.deepcopy(activation)
    wrong_run["activation"]["accepted_run_id"] = "71002"
    result = verify_publication_activation(
        wrong_run,
        expected=authority,
        downloaded_manifest=artifact,
        downloaded_artifact_dir=tmp_path / "dist",
    )
    assert not result.ok
    assert "wrong_run" in result.codes

    wrong_digest = copy.deepcopy(activation)
    first_name = next(iter(wrong_digest["activation"]["accepted_artifact_digests"]))
    wrong_digest["activation"]["accepted_artifact_digests"][first_name] = "e" * 64
    result = verify_publication_activation(
        wrong_digest,
        expected=authority,
        downloaded_manifest=artifact,
        downloaded_artifact_dir=tmp_path / "dist",
    )
    assert not result.ok
    assert "wrong_artifact_digest" in result.codes


def test_publication_manifests_bind_actor_service_digest_and_exact_tag(tmp_path: Path) -> None:
    artifact, _, _, _, _, _ = _materialize(tmp_path)
    authority, activation = build_publication_manifests(
        artifact,
        artifact_dir=tmp_path / "dist",
        actor_id="237524179",
        authorized_actor_id="237524179",
        artifact_id="99001",
        artifact_service_digest="5" * 64,
        tag="v0.1.1",
        environment="pypi",
        trusted_owner="synthetic.example",
        trusted_repository="LitGrove",
        trusted_workflow="publish-accepted-release.yml",
        trusted_environment="pypi",
    )
    result = verify_publication_activation(
        activation,
        expected=authority,
        downloaded_manifest=artifact,
        downloaded_artifact_dir=tmp_path / "dist",
    )
    assert result.ok, result.to_dict()
    assert authority["publication"]["authorized_actor_id"] == "237524179"
    assert authority["publication"]["accepted_artifact_id"] == "99001"
    assert authority["publication"]["accepted_artifact_service_digest"] == "5" * 64
    assert authority["publication"]["workflow_ref"] == "refs/tags/v0.1.1"

    with pytest.raises(GovernanceInputError, match="authenticated actor"):
        build_publication_manifests(
            artifact,
            artifact_dir=tmp_path / "dist",
            actor_id="1",
            authorized_actor_id="237524179",
            artifact_id="99001",
            artifact_service_digest="5" * 64,
            tag="v0.1.1",
            environment="pypi",
            trusted_owner="synthetic.example",
            trusted_repository="LitGrove",
            trusted_workflow="publish-accepted-release.yml",
            trusted_environment="pypi",
        )


def test_publication_manifests_cli_writes_canonical_verified_outputs(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    artifact, _, _, _, _, _ = _materialize(tmp_path)
    candidate_path = tmp_path / "candidate.json"
    authority_path = tmp_path / "authority.json"
    activation_path = tmp_path / "activation.json"
    candidate_path.write_text(canonical_json(artifact), encoding="utf-8", newline="\n")

    assert main(
        [
            "publication-manifests",
            "--candidate-manifest",
            str(candidate_path),
            "--artifact-dir",
            str(tmp_path / "dist"),
            "--actor-id",
            "237524179",
            "--authorized-actor-id",
            "237524179",
            "--artifact-id",
            "99001",
            "--artifact-service-digest",
            "5" * 64,
            "--tag",
            "v0.1.1",
            "--environment",
            "pypi",
            "--trusted-owner",
            "synthetic.example",
            "--trusted-repository",
            "LitGrove",
            "--trusted-workflow",
            "publish-accepted-release.yml",
            "--trusted-environment",
            "pypi",
            "--authority-output",
            str(authority_path),
            "--activation-output",
            str(activation_path),
        ]
    ) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    authority = json.loads(authority_path.read_text(encoding="utf-8"))
    activation = json.loads(activation_path.read_text(encoding="utf-8"))
    assert authority_path.read_text(encoding="utf-8") == canonical_json(authority)
    assert activation_path.read_text(encoding="utf-8") == canonical_json(activation)

    context = authority["publication"]
    result = verify_publication_authority(
        authority,
        repository=authority["candidate"]["repository"],
        actor_id=context["authorized_actor_id"],
        accepted_run_id=context["accepted_run_id"],
        accepted_run_attempt=context["accepted_run_attempt"],
        accepted_commit=context["accepted_commit"],
        accepted_artifact_name=context["accepted_artifact_name"],
        accepted_artifact_id=context["accepted_artifact_id"],
        accepted_artifact_service_digest=context["accepted_artifact_service_digest"],
        tag=context["tag"],
        workflow_ref=context["workflow_ref"],
        environment=context["environment"],
        trusted_owner=context["trusted_publisher"]["owner"],
        trusted_repository=context["trusted_publisher"]["repository"],
        trusted_workflow=context["trusted_publisher"]["workflow"],
        trusted_environment=context["trusted_publisher"]["environment"],
    )
    assert result.ok, result.to_dict()

    activation_from_external = build_publication_activation(
        authority,
        artifact_manifest=artifact,
        artifact_dir=tmp_path / "dist",
    )
    assert activation_from_external == activation


def test_publication_authority_rejects_context_drift_and_unsafe_asset_names(tmp_path: Path) -> None:
    artifact, _, _, _, _, authority = _materialize(tmp_path)
    context = authority["publication"]
    result = verify_publication_authority(
        authority,
        repository=authority["candidate"]["repository"],
        actor_id=context["authorized_actor_id"],
        accepted_run_id=context["accepted_run_id"],
        accepted_run_attempt=context["accepted_run_attempt"],
        accepted_commit=context["accepted_commit"],
        accepted_artifact_name=context["accepted_artifact_name"],
        accepted_artifact_id="99999",
        accepted_artifact_service_digest=context["accepted_artifact_service_digest"],
        tag=context["tag"],
        workflow_ref=context["workflow_ref"],
        environment=context["environment"],
        trusted_owner=context["trusted_publisher"]["owner"],
        trusted_repository=context["trusted_publisher"]["repository"],
        trusted_workflow=context["trusted_publisher"]["workflow"],
        trusted_environment=context["trusted_publisher"]["environment"],
    )
    assert not result.ok
    assert "authority_context_mismatch" in result.codes

    unsafe = copy.deepcopy(authority)
    digest = next(iter(unsafe["publication"]["accepted_artifact_digests"].values()))
    unsafe["publication"]["accepted_artifact_digests"] = {
        "bad\n--clobber.whl": digest,
        "synthetic-0.1.1.tar.gz": "4" * 64,
    }
    with pytest.raises(GovernanceInputError, match="external publication authority is invalid"):
        build_publication_activation(
            unsafe,
            artifact_manifest=artifact,
            artifact_dir=tmp_path / "dist",
        )


def test_publication_authority_and_activation_reject_unknown_fields(tmp_path: Path) -> None:
    artifact, _, _, _, activation, authority = _materialize(tmp_path)
    authority_with_unknown = copy.deepcopy(authority)
    authority_with_unknown["unexpected"] = "not allowed"
    result = verify_publication_activation(
        activation,
        expected=authority_with_unknown,
        downloaded_manifest=artifact,
        downloaded_artifact_dir=tmp_path / "dist",
    )
    assert not result.ok
    assert "noncanonical_fields" in result.codes

    activation_with_unknown = copy.deepcopy(activation)
    activation_with_unknown["activation"]["unexpected"] = "not allowed"
    result = verify_publication_activation(
        activation_with_unknown,
        expected=authority,
        downloaded_manifest=artifact,
        downloaded_artifact_dir=tmp_path / "dist",
    )
    assert not result.ok
    assert "noncanonical_fields" in result.codes


def test_publication_rejects_malformed_external_authority_before_digest_check(tmp_path: Path) -> None:
    artifact, _, _, _, activation, authority = _materialize(tmp_path)

    malformed_digest = copy.deepcopy(authority)
    first_name = next(iter(malformed_digest["publication"]["accepted_artifact_digests"]))
    malformed_digest["publication"]["accepted_artifact_digests"][first_name] = 1.5
    result = verify_publication_activation(
        activation,
        expected=malformed_digest,
        downloaded_manifest=artifact,
        downloaded_artifact_dir=tmp_path / "dist",
    )
    assert not result.ok
    assert "invalid_canonical_value" in result.codes
    assert result.evidence.get("write_authority_checked") is not True

    malformed_trusted_publisher = copy.deepcopy(authority)
    malformed_trusted_publisher["publication"]["trusted_publisher"]["extra"] = "unexpected"
    result = verify_publication_activation(
        activation,
        expected=malformed_trusted_publisher,
        downloaded_manifest=artifact,
        downloaded_artifact_dir=tmp_path / "dist",
    )
    assert not result.ok
    assert "invalid_expected_authority" in result.codes
    assert result.evidence.get("write_authority_checked") is not True


def test_publication_checks_actual_artifact_directory_and_runtime_identity(tmp_path: Path) -> None:
    artifact, installed, install, capability, activation, authority = _materialize(tmp_path)
    (tmp_path / "dist" / "unexpected.txt").write_text("unexpected\n", encoding="utf-8", newline="\n")
    result = verify_publication_activation(
        activation,
        expected=authority,
        downloaded_manifest=artifact,
        downloaded_artifact_dir=tmp_path / "dist",
    )
    assert not result.ok
    assert "unexpected_artifact_file" in result.codes

    runtime = copy.deepcopy(installed["runtime"])
    runtime["site_packages_class"] = "user-site"
    assert "unsafe_install_location" in verify_installed_manifest(installed | {"runtime": runtime}).codes

    runtime = copy.deepcopy(installed["runtime"])
    runtime["cpython_version"] = "9.9.9"
    result = verify_installed_manifest(installed, runtime_identity=runtime)
    assert not result.ok
    assert "runtime_identity_mismatch" in result.codes

    runtime = copy.deepcopy(installed["runtime"])
    runtime["requires_dist_sha256"] = "0" * 64
    result = verify_installed_manifest(installed, installed_root=install, capability_output=capability)
    mutated = copy.deepcopy(installed)
    mutated["runtime"] = runtime
    result = verify_installed_manifest(mutated, installed_root=install, capability_output=capability)
    assert not result.ok
    assert "requires_dist_digest_mismatch" in result.codes


def test_operation_manifest_binds_sbom_and_provenance_digests(tmp_path: Path) -> None:
    artifact, installed, _, _, _, _ = _materialize(tmp_path)
    artifact_path = tmp_path / "artifact-manifest.json"
    installed_path = tmp_path / "installed-manifest.json"
    sbom_path = tmp_path / "sbom.cdx.json"
    provenance_path = tmp_path / "provenance-inputs.json"
    artifact_path.write_text(canonical_json(artifact), encoding="utf-8", newline="\n")
    installed_path.write_text(canonical_json(installed), encoding="utf-8", newline="\n")
    sbom_path.write_text('{"bomFormat":"CycloneDX","specVersion":"1.5"}', encoding="utf-8", newline="\n")
    operation = build_operation_manifest(
        artifact_path,
        installed_path,
        sbom_path,
        provenance_path,
        audit_lock="requirements/locks/linux_x86_64/py312/audit.txt",
    )
    result = verify_operation_manifest(
        operation,
        artifact_manifest=artifact,
        installed_manifest=installed,
        sbom_path=sbom_path,
        provenance_inputs_path=provenance_path,
    )
    assert result.ok, result.to_dict()
    assert operation["schema_version"] == OPERATION_SCHEMA_VERSION
    assert operation["immutable"] is True
    assert operation["sbom_sha256"]
    assert operation["provenance_inputs_sha256"]


def _git(repo: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def test_reachable_history_expected_boundary_is_read_only_and_content_free(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = tmp_path / "history"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "synthetic@example.invalid")
    _git(repo, "config", "user.name", "Synthetic")
    private_question = "Q" + "001"
    for index in range(14):
        (repo / f"boundary-{index:02d}.txt").write_text(
            f"{private_question} access was absent in this public boundary statement.\n",
            encoding="utf-8",
        )
    pdf_signature = chr(37) + "PDF-"
    (repo / "format-check.txt").write_text(
        f"Synthetic literal {pdf_signature} format check.\n", encoding="utf-8"
    )
    (repo / "safe.txt").write_text("public synthetic content\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "synthetic history")
    _git(repo, "branch", "-M", "main")
    before_head = _git(repo, "rev-parse", "HEAD")
    before_status = _git(repo, "status", "--porcelain")

    snapshot = collect_reachable_history(repo)
    expected = {
        "schema_version": snapshot["schema_version"],
        "refs": snapshot["refs"],
        "commits": snapshot["commits"],
        "findings": snapshot["findings"],
    }
    result = verify_reachable_history(repo, expected)
    assert result.ok, result.to_dict()
    assert all(set(item) == {"path", "type", "blob"} for item in snapshot["findings"])
    assert len(snapshot["findings"]) == 15
    assert sum(item["type"] == "historical_boundary" for item in snapshot["findings"]) == 14
    assert sum(item["type"] == "pdf" for item in snapshot["findings"]) == 1
    assert all("content" not in item for item in result.evidence["findings"])
    assert _git(repo, "rev-parse", "HEAD") == before_head
    assert _git(repo, "status", "--porcelain") == before_status

    expected_path = tmp_path / "history-expectations.json"
    expected_path.write_text(canonical_json(expected), encoding="utf-8", newline="\n")
    output_path = tmp_path / "history-result.json"
    assert main(["scan-history", "--repo", str(repo), "--expected", str(expected_path), "--output", str(output_path)]) == 0
    output = capsys.readouterr().out
    assert f"{private_question} access was absent" not in output
    assert json.loads(output)["ok"] is True
    assert json.loads(output_path.read_text(encoding="utf-8"))["ok"] is True


def test_reachable_history_fails_on_ref_drift_and_unexpected_findings(tmp_path: Path) -> None:
    repo = tmp_path / "history"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "synthetic@example.invalid")
    _git(repo, "config", "user.name", "Synthetic")
    (repo / "safe.txt").write_text("public synthetic content\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "safe history")
    _git(repo, "branch", "-M", "main")
    snapshot = collect_reachable_history(repo)
    expected = {
        "schema_version": snapshot["schema_version"],
        "refs": snapshot["refs"],
        "commits": snapshot["commits"],
        "findings": snapshot["findings"],
    }
    drifted = copy.deepcopy(expected)
    drifted["refs"][0]["target"] = "0" * 40
    assert "history_ref_drift" in verify_reachable_history(repo, drifted).codes

    secret_field = "pass" + "word"
    (repo / "unexpected.txt").write_text(
        f"{secret_field} = 'unexpected-secret-value'\n", encoding="utf-8"
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "unexpected history")
    current = collect_reachable_history(repo)
    expected_with_current_ref = copy.deepcopy(expected)
    expected_with_current_ref["refs"] = current["refs"]
    expected_with_current_ref["commits"] = current["commits"]
    result = verify_reachable_history(repo, expected_with_current_ref)
    assert not result.ok
    assert "history_unexpected_finding" in result.codes
    assert all("unexpected-secret-value" not in finding.message for finding in result.errors)

    missing = copy.deepcopy(expected)
    assert missing["findings"] == []
    assert "invalid_history_expectations" not in verify_reachable_history(repo, missing).codes
    missing["findings"] = [{"path": "safe.txt", "type": "historical_boundary", "blob": "1" * 40}]
    assert "history_expected_finding_missing" in verify_reachable_history(repo, missing).codes
    assert "invalid_history_expectations" in verify_reachable_history(repo, []).codes


def test_cli_verifier_emits_canonical_result(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    artifact, _, _, _, _, _ = _materialize(tmp_path)
    manifest_path = tmp_path / "artifact.json"
    manifest_path.write_text(canonical_json(artifact) + "\n", encoding="utf-8", newline="\n")
    assert main(["verify-artifact", "--manifest", str(manifest_path), "--expected", str(manifest_path)]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["ok"] is True
    assert output["errors"] == []


def _uses_references(text: str) -> list[str]:
    return re.findall(r"^\s*uses:\s*([^\s#]+)", text, flags=re.MULTILINE)


def test_workflows_are_pinned_and_release_candidate_artifacts_are_separated() -> None:
    candidate = (REPOSITORY_ROOT / ".github" / "workflows" / "release-candidate.yml").read_text(encoding="utf-8")
    publication = (REPOSITORY_ROOT / ".github" / "workflows" / "publish-accepted-release.yml").read_text(encoding="utf-8")
    full_sha = re.compile(r"^[^@]+@[0-9a-f]{40}$")
    assert _uses_references(candidate)
    assert all(full_sha.fullmatch(reference) for reference in _uses_references(candidate))
    assert all(full_sha.fullmatch(reference) for reference in _uses_references(publication))
    assert "python -m build --no-isolation --outdir" in candidate
    assert "python -m build --outdir" not in candidate
    assert "--require-hashes" in candidate
    assert "--no-deps" in candidate
    assert "--only-binary=:all:" in candidate
    assert '--prefix "$GOVERNANCE_ROOT/install-root"' in candidate
    assert '--target "$GOVERNANCE_ROOT/install-root"' not in candidate
    assert "site-packages-path.txt" in candidate
    assert "tools/release-lock-bootstrap.txt" in candidate
    assert candidate.index("tools/release-lock-bootstrap.txt") < candidate.index("requirements/locks/linux_x86_64/py312/build.txt")
    assert "--run-attempt" in candidate
    assert "accepted-release-candidate-${{ github.run_id }}-${{ github.run_attempt }}-${{ github.sha }}" in candidate
    assert "release-candidate-diagnostics-${{ github.run_id }}-${{ github.run_attempt }}-${{ github.sha }}" in candidate
    assert "Upload accepted release-candidate bytes and manifests\n        if: success()" in candidate
    assert "Stage only accepted bytes and durable manifests" in candidate
    assert 'rm -rf "$GOVERNANCE_ROOT/venv" "$GOVERNANCE_ROOT/install-root" "$GOVERNANCE_ROOT/smoke"' in candidate
    assert '"accepted "' in candidate
    assert "path: ${{ runner.temp }}/release-candidate" in candidate
    assert "Upload failure diagnostics without package bytes\n        if: failure()" in candidate
    assert "path: ${{ runner.temp }}/release-candidate/diagnostics" in candidate
    assert "if: always()" not in candidate
    assert "pip_audit" in candidate
    assert "requirements/locks/linux_x86_64/py312/audit.txt" in candidate
    assert "operation-manifest" in candidate
    assert "provenance-inputs.json" in candidate
    assert "json.dumps" not in candidate
    assert "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a" in candidate
    assert "GITHUB_SHA" in candidate
    assert "git rev-parse HEAD" in candidate
    assert "git status --porcelain=v1 --untracked-files=all" in candidate
    assert 'test -z "$status"' in candidate
    assert "pypa/gh-action-pypi-publish" not in candidate
    assert "gh release" not in candidate.lower()
    assert "id-token: write" not in candidate
    assert "contents: write" not in candidate

    assert "actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c" in publication
    assert "pypa/gh-action-pypi-publish@dc37677b2e1c63e2034f94d8a5b11f265b73ba33" in publication
    assert "if: ${{ false" not in publication
    assert "python -m build" not in publication
    assert "twine" not in publication.lower()
    assert publication.count("id-token: write") == 1
    assert publication.count("contents: write") == 1
    assert "G1_PUBLICATION_ENABLED" not in publication
    assert "github.actor_id == '237524179'" in publication
    assert "github.ref_type == 'tag'" in publication
    assert "authority_manifest_b64" in publication
    assert "authority_manifest_sha256" in publication
    assert "verify-publication-authority" in publication
    assert publication.index("verify-publication-authority") < publication.index("Download the exact accepted candidate artifact")
    assert "publication-activation" in publication
    assert "Generate canonical authority and activation from authenticated context" not in publication
    assert "--accepted-artifact-service-digest" in publication
    assert "accepted_artifact_id" in publication
    assert "artifact-ids:" in publication
    assert "digest-mismatch: error" in publication
    assert "steps.payload.outputs.artifact-id" in publication
    assert "steps.payload.outputs.artifact-digest" in publication
    assert "needs.verify-accepted-bytes.outputs.source_commit" in publication
    assert '/git/tags/{tag_target}' in publication
    assert "tag_does_not_resolve_to_commit" in publication
    assert "REQUIRED_CHECKS_JSON" in publication
    assert "/branches/main/protection/required_status_checks" in publication
    assert "/check-runs?filter=latest&per_page=100" in publication
    assert publication.count("/commits/${RELEASE_TAG}") == 2
    assert "accepted_run_attempt" in publication
    assert '--expected "$publication_root/authority-manifest.json"' in publication
    assert '--downloaded-artifact-dir "$publication_root/accepted/dist"' in publication
    assert "packages-dir: ${{ runner.temp }}/publication/accepted/dist" in publication
    assert "gh release create" in publication
    assert "gh release upload" in publication
    assert "--clobber" not in publication
    assert re.search(r"\+\s+--", publication) is None
    assert "partial_publication_tag_only" in publication
    assert "partial_publication_release_only" in publication
    assert "partial_publication_pypi_only" in publication
    assert "publication_complete_pending_public_route" in publication
    assert "unknown_fail_closed" in publication
    assert "if: always()" in publication
    assert 'test "$PUBLICATION_STATE" = "publication_complete_pending_public_route"' in publication
    assert "existing PyPI version contains yanked files" in publication
    assert '"observation_errors": observation_errors' in publication
