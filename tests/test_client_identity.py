import pytest

from src.client_identity import client_commit, client_version


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, None),
        (12, None),
        ("١.٢.٣", None),
        ("1.2.3\n", None),
        ("1.2", None),
        ("01.002.3", "01.002.3"),
        ("1.2.3", "1.2.3"),
    ],
)
def test_version_boundary(value, expected):
    assert client_version(value) == expected


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, None),
        (12, None),
        ("A" * 40, "a" * 40),
        ("g" * 40, None),
        ("a" * 39, None),
        ("a" * 40 + "\n", None),
    ],
)
def test_commit_boundary(value, expected):
    assert client_commit(value) == expected
