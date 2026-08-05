from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = REPOSITORY_ROOT / "tools" / "test-shards.json"
L3_DIRECTORIES = ("tests/unit", "tests/contract", "tests/integration", "tests/privacy")
SCALE_DIRECTORIES = ("tests/benchmark",)
MARKER_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


class ManifestError(ValueError):
    pass


@dataclass(frozen=True)
class ManifestReport:
    l3_shards: tuple[str, ...]
    scale_shard: str
    l3_files: tuple[str, ...]
    scale_files: tuple[str, ...]
    shard_file_counts: dict[str, int]

    def as_dict(self) -> dict[str, Any]:
        return {
            "l3_shards": list(self.l3_shards),
            "scale_shard": self.scale_shard,
            "l3_file_count": len(self.l3_files),
            "scale_file_count": len(self.scale_files),
            "shard_file_counts": self.shard_file_counts,
        }


def load_manifest(path: Path = DEFAULT_MANIFEST) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != "test-shard-manifest@1.0":
        raise ManifestError("unsupported shard manifest schema")
    if not isinstance(data.get("l3_shards"), list) or not isinstance(data.get("shards"), dict):
        raise ManifestError("shard manifest structure is invalid")
    return data


def marker_for(shard: str, path: Path = DEFAULT_MANIFEST) -> str | None:
    manifest = load_manifest(path)
    marker = manifest.get("shard_markers", {}).get(shard)
    if marker is not None and (not isinstance(marker, str) or not MARKER_PATTERN.fullmatch(marker)):
        raise ManifestError(f"invalid shard marker: {shard}")
    return marker


def expand_selectors(selectors: list[str]) -> tuple[str, ...]:
    files: set[str] = set()
    for selector in selectors:
        relative = Path(selector)
        if relative.is_absolute() or ".." in relative.parts or not relative.parts or relative.parts[0] != "tests":
            raise ManifestError(f"unsafe test selector: {selector}")
        target = REPOSITORY_ROOT / relative
        if target.is_dir():
            selected = target.rglob("test_*.py")
        elif target.is_file() and target.name.startswith("test_") and target.suffix == ".py":
            selected = (target,)
        else:
            raise ManifestError(f"test selector does not resolve to tests: {selector}")
        files.update(path.relative_to(REPOSITORY_ROOT).as_posix() for path in selected)
    return tuple(sorted(files))


def _expected_files(directories: tuple[str, ...]) -> tuple[str, ...]:
    return expand_selectors(list(directories))


def verify_manifest(path: Path = DEFAULT_MANIFEST) -> ManifestReport:
    manifest = load_manifest(path)
    l3_shards = tuple(manifest["l3_shards"])
    scale_shard = manifest.get("scale_shard")
    shards = manifest["shards"]
    shard_markers = manifest.get("shard_markers", {})
    if len(l3_shards) != len(set(l3_shards)):
        raise ManifestError("L3 shard names are duplicated")
    if not isinstance(scale_shard, str) or scale_shard in l3_shards:
        raise ManifestError("scale shard is missing or overlaps L3")
    if set(shards) != set(l3_shards) | {scale_shard}:
        raise ManifestError("manifest contains a missing or undeclared shard")
    if not isinstance(shard_markers, dict) or not set(shard_markers).issubset(l3_shards):
        raise ManifestError("shard markers contain an undeclared or non-L3 shard")
    for shard_name, marker in shard_markers.items():
        if not isinstance(marker, str) or not MARKER_PATTERN.fullmatch(marker):
            raise ManifestError(f"invalid shard marker: {shard_name}")

    owners: dict[str, list[str]] = {}
    shard_file_counts: dict[str, int] = {}
    for shard_name, selectors in shards.items():
        if not isinstance(selectors, list) or not selectors:
            raise ManifestError(f"shard has no selectors: {shard_name}")
        files = expand_selectors(selectors)
        shard_file_counts[shard_name] = len(files)
        for file_name in files:
            owners.setdefault(file_name, []).append(shard_name)

    expected_l3 = set(_expected_files(L3_DIRECTORIES))
    actual_l3 = {name for name, assigned in owners.items() if any(shard in l3_shards for shard in assigned)}
    expected_scale = set(_expected_files(SCALE_DIRECTORIES))
    actual_scale = set(expand_selectors(shards[scale_shard]))
    duplicates = {name: assigned for name, assigned in owners.items() if len(assigned) > 1}
    for name, assigned in duplicates.items():
        markers = [shard_markers.get(shard) for shard in assigned]
        if any(marker is None for marker in markers) or len(set(markers)) != len(markers):
            raise ManifestError(f"test file has ambiguous shard owners: {name}: {assigned}")
    if actual_l3 != expected_l3:
        raise ManifestError(
            f"L3 coverage mismatch; missing={sorted(expected_l3 - actual_l3)}, "
            f"unexpected={sorted(actual_l3 - expected_l3)}"
        )
    if actual_scale != expected_scale:
        raise ManifestError(
            f"scale coverage mismatch; missing={sorted(expected_scale - actual_scale)}, "
            f"unexpected={sorted(actual_scale - expected_scale)}"
        )
    if actual_l3.intersection(actual_scale):
        raise ManifestError("L3 and scale test files overlap")

    return ManifestReport(
        l3_shards=l3_shards,
        scale_shard=scale_shard,
        l3_files=tuple(sorted(actual_l3)),
        scale_files=tuple(sorted(actual_scale)),
        shard_file_counts=shard_file_counts,
    )


def selectors_for(shard: str, path: Path = DEFAULT_MANIFEST) -> list[str]:
    manifest = load_manifest(path)
    if shard not in manifest["shards"]:
        raise ManifestError(f"unknown shard: {shard}")
    return list(manifest["shards"][shard])


def collect_nodeids(selectors: list[str], *, marker: str | None = None) -> tuple[str, ...]:
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-p",
        "no:cacheprovider",
        "--collect-only",
        "-q",
        *([] if marker is None else ["-m", marker]),
        *selectors,
    ]
    result = subprocess.run(
        command,
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        raise ManifestError(f"pytest collection failed for {selectors}: {result.stdout}\n{result.stderr}")
    nodeids = tuple(
        sorted(
            line.strip()
            for line in result.stdout.splitlines()
            if line.strip().startswith("tests/") and "::" in line
        )
    )
    if not nodeids:
        raise ManifestError(f"pytest collection returned no node IDs for {selectors}")
    return nodeids


def nodeid_digest(nodeids: tuple[str, ...]) -> str:
    payload = "\n".join(nodeids).encode("utf-8") + b"\n"
    return hashlib.sha256(payload).hexdigest()


def verify_nodeid_coverage(path: Path = DEFAULT_MANIFEST) -> dict[str, Any]:
    report = verify_manifest(path)
    manifest = load_manifest(path)
    expected_l3 = set(collect_nodeids(list(L3_DIRECTORIES)))
    assigned_l3: dict[str, str] = {}
    shard_counts: dict[str, int] = {}
    for shard in report.l3_shards:
        nodeids = collect_nodeids(
            list(manifest["shards"][shard]),
            marker=marker_for(shard, path),
        )
        shard_counts[shard] = len(nodeids)
        for nodeid in nodeids:
            if nodeid in assigned_l3:
                raise ManifestError(f"node ID appears in two shards: {nodeid}")
            assigned_l3[nodeid] = shard
    actual_l3 = set(assigned_l3)
    if actual_l3 != expected_l3:
        raise ManifestError(
            f"L3 node coverage mismatch; missing={sorted(expected_l3 - actual_l3)}, "
            f"unexpected={sorted(actual_l3 - expected_l3)}"
        )

    scale_nodeids = collect_nodeids(list(manifest["shards"][report.scale_shard]))
    expected_scale = collect_nodeids(list(SCALE_DIRECTORIES))
    if scale_nodeids != expected_scale:
        raise ManifestError("scale node coverage does not match the benchmark collection")
    return {
        **report.as_dict(),
        "l3_node_count": len(actual_l3),
        "l3_nodeid_sha256": nodeid_digest(tuple(sorted(actual_l3))),
        "scale_node_count": len(scale_nodeids),
        "scale_nodeid_sha256": nodeid_digest(scale_nodeids),
        "shard_node_counts": shard_counts | {report.scale_shard: len(scale_nodeids)},
    }
