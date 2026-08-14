import pytest

from research_kb import __version__
from research_kb.contracts.versions import ContractVersion, parse_version, require_supported
from research_kb.errors import ResearchKBError


def test_package_identity_matches_registered_release() -> None:
    assert __version__ == "0.1.1"
    assert __version__ != "0.1.0"


def test_parse_supported_version() -> None:
    assert parse_version("1.0") == ContractVersion(1, 0)
    assert str(require_supported("1.0")) == "1.0"


@pytest.mark.parametrize("value", [None, 1, "1", "1.0.0", "01.0", "2.0", "1.1"])
def test_unsupported_versions_fail_closed(value: object) -> None:
    with pytest.raises(ResearchKBError) as caught:
        require_supported(value)
    assert caught.value.diagnostic.code == "RKBC-001"
