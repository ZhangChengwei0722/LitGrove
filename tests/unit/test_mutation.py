import json
from pathlib import Path

import pytest

from research_kb.errors import ResearchKBError
from research_kb.mutation import load_mutation_request


def test_mutation_request_loads_valid_candidate(tmp_path: Path) -> None:
    path = tmp_path / "request.json"
    path.write_text(json.dumps({
        "contract_version": "1.0",
        "operation": "append",
        "record_kind": "evidence",
        "target_record_id": None,
        "context": {"paper_id": "paper_a1111111-1111-4111-8111-111111111111"},
        "payload": {"claim": "Synthetic claim."},
    }), encoding="utf-8")
    request = load_mutation_request(path)
    assert request.record_kind == "evidence"
    assert request.paper_id.startswith("paper_")


def test_replace_mutation_requires_target_id(tmp_path: Path) -> None:
    path = tmp_path / "request.json"
    path.write_text(json.dumps({
        "contract_version": "1.0",
        "operation": "replace",
        "record_kind": "review-queue",
        "target_record_id": None,
        "context": {"paper_id": None},
        "payload": {},
    }), encoding="utf-8")
    with pytest.raises(ResearchKBError) as caught:
        load_mutation_request(path)
    assert caught.value.diagnostic.code == "RKBC-002"
