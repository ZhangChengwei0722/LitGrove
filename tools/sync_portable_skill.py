from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any


TEXT_SUFFIXES = {".md", ".yaml", ".yml", ".json", ".txt"}


def snapshot(root: Path) -> dict[str, Any]:
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError(f"portable Skill root is not a directory: {root}")
    files: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        if path.is_symlink():
            raise ValueError(f"portable Skill tree contains a symlink: {path.relative_to(root).as_posix()}")
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        content = normalized_bytes(path)
        files.append(
            {
                "path": relative,
                "sha256": hashlib.sha256(content).hexdigest(),
                "bytes": len(content),
            }
        )
    tree = hashlib.sha256()
    for item in files:
        tree.update(item["path"].encode("utf-8"))
        tree.update(b"\0")
        tree.update(item["sha256"].encode("ascii"))
        tree.update(b"\0")
    return {"tree_sha256": tree.hexdigest(), "file_count": len(files), "files": files}


def normalized_bytes(path: Path) -> bytes:
    content = path.read_bytes()
    if path.suffix.lower() not in TEXT_SUFFIXES:
        return content
    text = content.decode("utf-8")
    return text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")


def sync_tree(source: Path, destination: Path) -> dict[str, Any]:
    source = source.resolve(strict=True)
    destination = destination.resolve(strict=False)
    source_snapshot = snapshot(source)
    if destination.exists() and destination.is_symlink():
        raise ValueError("portable Skill destination cannot be a symlink")
    destination.mkdir(parents=True, exist_ok=True)

    source_paths = {item["path"] for item in source_snapshot["files"]}
    for path in sorted(destination.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if path.is_symlink():
            raise ValueError(f"portable Skill destination contains a symlink: {path}")
        relative = path.relative_to(destination).as_posix()
        if path.is_file() and relative not in source_paths:
            path.unlink()
        elif path.is_dir() and not any(path.iterdir()):
            path.rmdir()

    for item in source_snapshot["files"]:
        source_path = source / Path(item["path"])
        destination_path = destination / Path(item["path"])
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        content = normalized_bytes(source_path)
        temporary = destination_path.with_name(f".{destination_path.name}.{os.getpid()}.tmp")
        temporary.write_bytes(content)
        os.replace(temporary, destination_path)

    destination_snapshot = snapshot(destination)
    if destination_snapshot["tree_sha256"] != source_snapshot["tree_sha256"]:
        raise RuntimeError("portable Skill destination does not match the normalized source snapshot")
    return destination_snapshot


def comparison(source: Path, destination: Path) -> dict[str, Any]:
    source_snapshot = snapshot(source)
    destination_snapshot = snapshot(destination) if destination.exists() else None
    return {
        "status": "current" if destination_snapshot == source_snapshot else "different",
        "source": source_snapshot,
        "destination": destination_snapshot,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check or refresh the repo-owned portable Skill snapshot.")
    parser.add_argument("--source", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)

    repository_root = Path(__file__).resolve().parents[1]
    destination = (repository_root / "skills" / "research-kb").resolve(strict=False)
    if args.apply:
        sync_tree(args.source, destination)
    report = comparison(args.source, destination)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0 if report["status"] == "current" else 1


if __name__ == "__main__":
    raise SystemExit(main())
