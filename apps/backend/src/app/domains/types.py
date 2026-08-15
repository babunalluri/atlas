"""Workspace domain identifiers and defaults."""

from __future__ import annotations

from typing import Any, Literal

WorkspaceDomain = Literal["generic", "stock_broker", "dental_clinic"]

WORKSPACE_DOMAINS: tuple[WorkspaceDomain, ...] = (
    "generic",
    "stock_broker",
    "dental_clinic",
)

DOMAIN_LABELS: dict[WorkspaceDomain, str] = {
    "generic": "General",
    "stock_broker": "Stock Broker",
    "dental_clinic": "Dental Clinic",
}

STOCK_BROKER_DESK_TEAMS: tuple[str, ...] = (
    "learning",
    "paper-trading",
    "live-trading",
    "research",
)

DOMAIN_DEFAULT_TEAM_SLUGS: dict[WorkspaceDomain, tuple[str, ...]] = {
    "stock_broker": STOCK_BROKER_DESK_TEAMS,
}


def normalize_domain(value: str | None) -> WorkspaceDomain:
    normalized = (value or "generic").strip().lower().replace("-", "_")
    if normalized not in WORKSPACE_DOMAINS:
        raise ValueError(
            f"domain must be one of: {', '.join(WORKSPACE_DOMAINS)}"
        )
    return normalized  # type: ignore[return-value]


def default_branding(domain: WorkspaceDomain) -> dict[str, Any]:
    if domain == "stock_broker":
        return {
            "primaryColor": "#0c4a6e",
            "accentColor": "#38bdf8",
            "tagline": "Research, paper trading, and live ops for your brokerage desk",
            "teamWelcomeMessage": "Welcome to your Stock Broker workspace. How can we help?",
            "workflowWelcomeMessage": "Start a guided trading workflow.",
        }
    if domain == "dental_clinic":
        return {
            "primaryColor": "#0f766e",
            "accentColor": "#5eead4",
            "tagline": "Patient engagement and clinic operations powered by AI",
            "teamWelcomeMessage": "Welcome to Smile Dental. How can we assist you today?",
            "workflowWelcomeMessage": "Let's walk through your appointment or care request.",
        }
    return {
        "primaryColor": "#0f766e",
        "accentColor": "#5eead4",
        "tagline": "AI agents for your customers",
    }
