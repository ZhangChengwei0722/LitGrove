import uuid

import pytest

from research_kb.errors import ResearchKBError
from research_kb.identifiers import Namespace, allocate_id, ensure_unique, validate_id


def test_allocate_and_validate_namespaced_uuid4() -> None:
    fixed = uuid.UUID("550e8400-e29b-41d4-a716-446655440000")
    value = allocate_id(Namespace.PAPER, lambda: fixed)
    assert value == "paper_550e8400-e29b-41d4-a716-446655440000"
    assert validate_id(value, Namespace.PAPER) == value


def test_identifier_has_no_domain_or_order_semantics() -> None:
    value = "evidence_3d813cbb-47fb-4b64-8e6f-c6c8f0d1c624"
    assert validate_id(value, Namespace.EVIDENCE) == value


@pytest.mark.parametrize(
    "value",
    [
        "paper_550e8400-e29b-11d4-a716-446655440000",
        "paper_550E8400-E29B-41D4-A716-446655440000",
        "unknown_550e8400-e29b-41d4-a716-446655440000",
        "paper-domain-0001",
    ],
)
def test_invalid_identifier_is_rejected(value: str) -> None:
    with pytest.raises(ResearchKBError):
        validate_id(value)


def test_namespace_mismatch_is_rejected() -> None:
    with pytest.raises(ResearchKBError):
        validate_id("paper_550e8400-e29b-41d4-a716-446655440000", Namespace.EVIDENCE)


def test_duplicate_id_is_rejected() -> None:
    with pytest.raises(ResearchKBError) as caught:
        ensure_unique(["value", "value"])
    assert caught.value.diagnostic.code == "RKBC-004"
