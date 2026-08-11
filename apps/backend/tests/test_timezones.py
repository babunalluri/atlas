import pytest

from app.core.timezones import normalize_timezone


def test_normalize_timezone_accepts_canonical_utc() -> None:
    assert normalize_timezone("UTC") == "UTC"


def test_normalize_timezone_rejects_lowercase_utc() -> None:
    with pytest.raises(ValueError, match="Unknown IANA timezone: utc"):
        normalize_timezone("utc")


def test_normalize_timezone_defaults_to_utc() -> None:
    assert normalize_timezone(None) == "UTC"
    assert normalize_timezone("") == "UTC"
