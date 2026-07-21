from __future__ import annotations

import json
from io import BytesIO, TextIOWrapper
from pathlib import Path

from research_kb.cli import main
from research_kb.storage.json_io import read_jsonl
from tests.discovery_candidate_helpers import discovery_report, discovery_result, selection_request
from tests.runtime_helpers import make_runtime_workspace


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_cli_select_list_show_and_guardian_preserve_unselected_metadata_and_sources(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    layout = make_runtime_workspace(tmp_path)
    first = discovery_result()
    second = discovery_result(
        result_key="doi:10.0000/synthetic.second",
        doi="10.0000/synthetic.second",
        record_id="SYNTH-DISCOVERY-2",
        title="Targeted degradation delivery in a second invented system",
    )
    request = selection_request(discovery_report(first, second), result_keys=[first["result_key"]])
    source_before = _tree_bytes(layout.source_roots["alpha-sources"])
    stream = TextIOWrapper(BytesIO(json.dumps(request).encode("utf-8")), encoding="utf-8")
    monkeypatch.setattr("sys.stdin", stream)

    assert main(
        [
            "discovery",
            "select",
            "--workspace",
            str(layout.config.path),
            "--request",
            "-",
            "--actor",
            "user",
        ]
    ) == 0
    selected = json.loads(capsys.readouterr().out)
    candidate_id = selected["selected_candidate_ids"][0]
    assert selected["persistent_writes"] == 1

    assert main(["discovery", "list", "--workspace", str(layout.config.path)]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed["candidate_count"] == 1
    assert listed["candidates"][0]["candidate_id"] == candidate_id

    assert main(
        [
            "discovery",
            "show",
            "--workspace",
            str(layout.config.path),
            "--candidate-id",
            candidate_id,
        ]
    ) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["candidate"]["result_key"] == first["result_key"]
    assert second["result_key"] not in layout.discovery_candidates_path.read_text(encoding="utf-8")

    changed = discovery_result(
        title="Targeted degradation delivery in a changed invented system"
    )
    conflict_path = tmp_path / "conflict.json"
    conflict_path.write_text(
        json.dumps(selection_request(discovery_report(changed))),
        encoding="utf-8",
        newline="\n",
    )
    candidate_bytes = layout.discovery_candidates_path.read_bytes()
    assert main(
        [
            "discovery",
            "select",
            "--workspace",
            str(layout.config.path),
            "--request",
            str(conflict_path),
            "--actor",
            "user",
        ]
    ) == 4
    conflict = capsys.readouterr()
    assert conflict.out == ""
    assert json.loads(conflict.err)["diagnostic"]["code"] == "RKBC-034"
    assert layout.discovery_candidates_path.read_bytes() == candidate_bytes

    assert main(["guardian", "check", "--workspace", str(layout.config.path)]) == 0
    guardian = json.loads(capsys.readouterr().out)
    assert guardian["status"] == "success"
    assert _tree_bytes(layout.source_roots["alpha-sources"]) == source_before
    assert not (layout.knowledge_root / "registry" / "papers.jsonl").exists()


def test_cli_non_user_selection_has_empty_stdout_and_no_candidate_store(
    tmp_path: Path,
    capsys,
) -> None:
    layout = make_runtime_workspace(tmp_path)
    request_path = tmp_path / "selection.json"
    request_path.write_text(json.dumps(selection_request()), encoding="utf-8", newline="\n")

    assert main(
        [
            "discovery",
            "select",
            "--workspace",
            str(layout.config.path),
            "--request",
            str(request_path),
            "--actor",
            "agent",
        ]
    ) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err)["diagnostic"]["code"] == "RKBC-006"
    assert read_jsonl(layout.discovery_candidates_path, record_kind="discovery-candidate") == []
