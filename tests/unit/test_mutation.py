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


def test_question_mapping_request_loads_approved_origin_without_paper_context(tmp_path: Path) -> None:
    path = tmp_path / "question-request.json"
    path.write_text(json.dumps({
        "contract_version": "1.0",
        "operation": "append",
        "record_kind": "question-mapping",
        "target_record_id": None,
        "context": {"paper_id": None, "question_origin": "user_approved_candidate"},
        "payload": {
            "question_text": "Which fabricated response differs?",
            "scope": "Synthetic records only.",
            "mapping_status": "ai_draft",
            "paper_links": [],
        },
    }), encoding="utf-8")

    request = load_mutation_request(path)

    assert request.paper_id is None
    assert request.question_origin == "user_approved_candidate"


@pytest.mark.parametrize(
    ("record_kind", "operation", "paper_id", "question_origin"),
    [
        ("question-mapping", "append", None, "existing_question"),
        ("question-mapping", "replace", None, "user_supplied"),
        ("question-mapping", "append", "paper_a1111111-1111-4111-8111-111111111111", "user_supplied"),
        ("evidence", "append", "paper_a1111111-1111-4111-8111-111111111111", "user_supplied"),
    ],
)
def test_question_origin_context_is_operation_and_kind_scoped(
    tmp_path: Path,
    record_kind: str,
    operation: str,
    paper_id: str | None,
    question_origin: str,
) -> None:
    path = tmp_path / "invalid-origin.json"
    path.write_text(json.dumps({
        "contract_version": "1.0",
        "operation": operation,
        "record_kind": record_kind,
        "target_record_id": (
            "question_a1111111-1111-4111-8111-111111111111"
            if operation == "replace"
            else None
        ),
        "context": {"paper_id": paper_id, "question_origin": question_origin},
        "payload": {},
    }), encoding="utf-8")

    with pytest.raises(ResearchKBError) as caught:
        load_mutation_request(path)

    assert caught.value.diagnostic.code == "RKBC-002"
