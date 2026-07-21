from __future__ import annotations

import pytest

from research_kb.acquisition_paths import acquisition_destination
from research_kb.errors import ResearchKBError
from tests.runtime_helpers import make_runtime_workspace


CANDIDATE_ID = "discovery_a1111111-1111-4111-8111-111111111111"


def test_acquisition_destination_is_exactly_addressable_and_deterministic(tmp_path) -> None:
    layout = make_runtime_workspace(
        tmp_path,
        local_inbox="./sources/inbox",
        create_local_inbox=True,
    )

    destination = acquisition_destination(layout, CANDIDATE_ID)

    assert destination.root_id == "alpha-sources"
    assert destination.inbox == layout.source_roots["alpha-sources"] / "inbox"
    assert destination.final_path == destination.inbox / f"{CANDIDATE_ID}.pdf"
    assert destination.source_ref.to_dict() == {
        "root_id": "alpha-sources",
        "relative_path": f"inbox/{CANDIDATE_ID}.pdf",
    }


@pytest.mark.parametrize(
    ("local_inbox", "create_local_inbox"),
    [
        ("./sources/missing-inbox", False),
        ("./outside-inbox", True),
    ],
)
def test_acquisition_destination_rejects_missing_or_unaddressable_inbox(
    tmp_path,
    local_inbox,
    create_local_inbox,
) -> None:
    layout = make_runtime_workspace(
        tmp_path,
        local_inbox=local_inbox,
        create_local_inbox=create_local_inbox,
    )

    with pytest.raises(ResearchKBError) as error:
        acquisition_destination(layout, CANDIDATE_ID)

    assert error.value.diagnostic.code in {"RKBC-007", "RKBC-021"}


def test_acquisition_destination_rejects_multiple_nested_source_owners(tmp_path) -> None:
    layout = make_runtime_workspace(
        tmp_path,
        local_inbox="./sources/nested/inbox",
        create_local_inbox=True,
        source_roots=[
            {"root_id": "outer", "path": "./sources", "read_only_assets": True},
            {"root_id": "inner", "path": "./sources/nested", "read_only_assets": True},
        ],
    )
    with pytest.raises(ResearchKBError) as error:
        acquisition_destination(layout, CANDIDATE_ID)

    assert error.value.diagnostic.code == "RKBC-021"


def test_acquisition_destination_rejects_inbox_file(tmp_path) -> None:
    layout = make_runtime_workspace(tmp_path)
    inbox = layout.config.path.parent / "inbox"
    inbox.write_text("not a directory", encoding="utf-8")

    with pytest.raises(ResearchKBError):
        acquisition_destination(layout, CANDIDATE_ID)
