"""Shared IANA timezone helpers for Atlas APIs."""

from __future__ import annotations

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Curated common zones shown in admin pickers (still accept any valid IANA id).
COMMON_TIMEZONES: tuple[str, ...] = (
    "UTC",
    "Asia/Kolkata",
    "Asia/Dubai",
    "Asia/Singapore",
    "Asia/Tokyo",
    "Asia/Shanghai",
    "Europe/London",
    "Europe/Paris",
    "Europe/Berlin",
    "America/New_York",
    "America/Chicago",
    "America/Denver",
    "America/Los_Angeles",
    "America/Sao_Paulo",
    "America/Argentina/Buenos_Aires",
    "Africa/Cairo",
    "Africa/Johannesburg",
    "Australia/Sydney",
    "Pacific/Auckland",
)


def normalize_timezone(value: str | None, *, default: str = "UTC") -> str:
    cleaned = (value or "").strip() or default
    try:
        ZoneInfo(cleaned)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Unknown IANA timezone: {cleaned}") from exc
    return cleaned
