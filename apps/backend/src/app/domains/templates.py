"""Domain workspace starter templates (agents, teams, workflows)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

DEFAULT_MODEL = "openai:gpt-4.1-mini"


@dataclass(frozen=True)
class AgentTemplate:
    slug: str
    name: str
    description: str
    instructions: str


@dataclass(frozen=True)
class TeamTemplate:
    slug: str
    name: str
    description: str
    instructions: str
    mode: str
    member_slugs: list[str]


@dataclass(frozen=True)
class WorkflowStepTemplate:
    name: str
    target_slug: str
    target_type: str  # agent | team


@dataclass(frozen=True)
class WorkflowTemplate:
    slug: str
    name: str
    description: str
    mode: str
    steps: list[WorkflowStepTemplate]


@dataclass(frozen=True)
class DomainTemplate:
    agents: list[AgentTemplate] = field(default_factory=list)
    teams: list[TeamTemplate] = field(default_factory=list)
    workflows: list[WorkflowTemplate] = field(default_factory=list)


STOCK_BROKER = DomainTemplate(
    agents=[
        AgentTemplate(
            slug="learning-guide",
            name="Learning Guide",
            description="Concepts, generic market questions, no trading.",
            instructions=(
                "You are the Stock Broker Learning Guide. This window is Learning: Knowledge Base "
                "lessons and generic market questions (e.g. predict TCS for the next few hours). "
                "You cannot predict or guarantee prices — that is not investment advice. "
                "For ticker questions, use assigned read-only quote tools if bound: get_ltp, "
                "get_quote, get_ohlc (or the closest alias on Groww, Kite, or any other adapter). "
                "Never invent ticks. If no quote tool is bound, say so and point at the desk chart. "
                "Never call place_order, cancel_order, or place_paper_order. "
                "Hand analysis and payoffs to Research; paper practice to Paper trading; "
                "holdings and live orders to Live trading. "
                "If KB has no hit for a how-to question, say you do not have that article yet."
            ),
        ),
        AgentTemplate(
            slug="paper-trader",
            name="Paper Trader",
            description="Signal to paper fills with virtual capital.",
            instructions=(
                "You are the Stock Broker Paper Trader. This window is paper only. "
                "Use assigned platform tools: list_signals, get_signal, get_paper_hub, "
                "place_paper_order (HITL, reuse idempotency_key), list_positions. "
                "Optional read-only quotes if bound (get_ltp / get_quote / get_ohlc). "
                "Never call live place_order, cancel_order, or modify_order. "
                "Never invent fills or P&L. Paper is allowed when the cash market is closed. "
                "Hand concepts to Learning; analysis to Research; demat and live orders to Live trading."
            ),
        ),
        AgentTemplate(
            slug="live-trader",
            name="Live Trader",
            description="Assigned broker account, holdings, and live orders.",
            instructions=(
                "You are the Stock Broker Live Trader. This window is live/demat only. "
                "Discover tools bound on this agent or the Live trading team. Never assume "
                "Groww, Kite, or any vendor — call whatever names exist on the assigned toolkit. "
                "Typical aliases: get_account_health/get_profile, get_holdings, get_positions, "
                "get_user_margin/get_margins/get_user_margins/get_funds, list_orders/get_orders, "
                "get_ltp/get_quote, place_order/cancel_order (HITL, stable order_reference_id). "
                "If no broker tool is bound, say so. Do not invent holdings, fills, or tokens. "
                "Never echo OAuth or OTPs. You cannot approve live eligibility or trip a global "
                "kill switch. Hand paper practice to Paper trading; analysis to Research; "
                "concepts to Learning."
            ),
        ),
        AgentTemplate(
            slug="signal-operator",
            name="Signal Operator",
            description="Admin signal metrics, entry evaluation, and publish.",
            instructions=(
                "You are the Stock Broker Signal Operator on the admin Signals ops team. "
                "Use get_metric_config and get_signal_state to report metric pass/fail. "
                "When entry_ready and the operator confirms, call publish_entry_signal (HITL). "
                "Fetch live prints via assigned read-only broker tools — never invent quotes. "
                "Dow Jones uses the slow cache tier; do not refetch every second. "
                "This team is admin-only — never expose metrics on customer desk chats. "
                "Hand customer paper to Paper trading; analysis to Research; live to Live trading."
            ),
        ),
    ],
    teams=[
        TeamTemplate(
            slug="learning",
            name="Learning",
            description="Concepts and generic market questions. No trading.",
            instructions=(
                "This is the Learning workspace. Route to Learning Guide for KB lessons and "
                "generic ticker questions. Quotes are read-only from whatever broker toolkit is "
                "assigned — never predict prices. No paper or live orders. "
                "Hand analysis to Research; paper practice to Paper trading; "
                "holdings and live orders to Live trading."
            ),
            mode="route",
            member_slugs=["learning-guide"],
        ),
        TeamTemplate(
            slug="paper-trading",
            name="Paper trading",
            description="Practice signals with virtual capital.",
            instructions=(
                "This is the Paper trading workspace. Route to Paper Trader for signal → paper fill. "
                "Never place live broker orders from this team. Hand concepts to Learning; "
                "analysis to Research; demat and live orders to Live trading."
            ),
            mode="route",
            member_slugs=["paper-trader"],
        ),
        TeamTemplate(
            slug="live-trading",
            name="Live trading",
            description="Assigned broker: holdings, margin, live orders.",
            instructions=(
                "This is the Live trading workspace. Route to Live Trader. Use whatever broker "
                "toolkit is assigned (Groww, Kite, or any other) — do not hard-code a vendor. "
                "If none is bound, say so. Hand paper practice to Paper trading; analysis to Research; "
                "concepts to Learning."
            ),
            mode="route",
            member_slugs=["live-trader"],
        ),
        TeamTemplate(
            slug="research",
            name="Research",
            description="Tool-required stock and F&O analysis. No orders.",
            instructions=(
                "This is the Research workspace. You run leader-only — there is no member "
                "agent to route to, so answer directly with the toolkits bound on this team. "
                "This window is analysis only. "
                "You MUST call tools for any price, position, Greek, IV, payoff, or what-if. "
                "If a tool fails or returns no data, say so. Never invent quotes, option chains, "
                "candles, IV, Greeks, or P&L. "
                "Use research_stock_snapshot, research_compare_symbols, and research_option_payoff "
                "after fetching prints from assigned read-only quote tools (get_ltp / get_quote / "
                "get_ohlc) or taking user-supplied strikes/LTPs. "
                "Always try the bound quote tools first — ask the user for a price only after a "
                "tool call actually failed or no quote tool is bound. "
                "If no quote toolkit is bound to this team, say so plainly: analysis needs a "
                "quote-capable broker toolkit bound to the Research team, and an admin does that "
                "in Team Builder. Then offer to run the math on a price the user supplies. "
                "Defined structures only: long_call, long_put, covered_call, bull_call_spread, "
                "iron_condor. Groww/Kite quote tools have no live option chain — do not fake one. "
                "Never call place_order, modify_order, cancel_order, or place_paper_order. "
                "Research is for analysis; live orders stay on Live trading (HITL). "
                "Hand concepts to Learning; paper practice to Paper trading."
            ),
            # Leader-only: Agno Team runs with no members and calls Team.tools itself.
            mode="coordinate",
            member_slugs=[],
        ),
        TeamTemplate(
            slug="signals-ops",
            name="Signals ops",
            description="Admin-only signal metrics, entry rules, and publish.",
            instructions=(
                "This is the admin Signals ops workspace — not on the end-user desk. "
                "Route to Signal Operator. Use signal_engine toolkit for metric state and "
                "publish_entry_signal when entry_ready. "
                "Bind Kite Connect (read-only) on this team: get_quote, get_ltp, get_ohlc, "
                "get_historical_candles — Kite-only for signals; no Groww. "
                "Global indices use backend Yahoo Finance slow tier (~1 h); do not poll Yahoo from chat. "
                "Trade Desk Checklist metrics refresh fast (NIFTY/CE/PE/OI) via Kite; "
                "ADX/RSI/crude/VIX medium tier; globals slow tier. "
                "Never expose raw metrics to customer teams."
            ),
            mode="route",
            member_slugs=["signal-operator"],
        ),
    ],
    workflows=[
        WorkflowTemplate(
            slug="paper-from-signal",
            name="Paper from signal",
            description="Entitled signal → paper ticket → fill.",
            mode="sequential",
            steps=[
                WorkflowStepTemplate("Paper ticket", "paper-trading", "team"),
            ],
        ),
        WorkflowTemplate(
            slug="live-approval",
            name="Live status",
            description="Assigned broker health, arm status, live orders.",
            mode="sequential",
            steps=[
                WorkflowStepTemplate("Live trading", "live-trading", "team"),
            ],
        ),
    ],
)

DENTAL_CLINIC = DomainTemplate(
    agents=[
        AgentTemplate(
            slug="front-desk",
            name="Front Desk",
            description="Scheduling, recalls, and front-office patient requests.",
            instructions=(
                "You are the Dental Clinic Front Desk agent. Help staff book, reschedule, "
                "and cancel appointments; surface recall lists; and confirm patient identity."
            ),
        ),
        AgentTemplate(
            slug="patient-concierge",
            name="Patient Concierge",
            description="Patient FAQ, aftercare, and self-service chat.",
            instructions=(
                "You are the Dental Clinic Patient Concierge. Answer FAQs, aftercare questions, "
                "and route appointment changes to staff workflows. Never diagnose conditions."
            ),
        ),
        AgentTemplate(
            slug="clinician-copilot",
            name="Clinician Copilot",
            description="Staff copilot for chart summaries and treatment plan explanations.",
            instructions=(
                "You are the Dental Clinic Clinician Copilot. Summarize chart notes and explain "
                "treatment plans in plain language for staff. Do not replace clinical judgment."
            ),
        ),
    ],
    teams=[
        TeamTemplate(
            slug="front-desk-team",
            name="Front Desk Team",
            description="Front office scheduling and patient intake.",
            instructions="Coordinate front desk scheduling and intake via the Front Desk agent.",
            mode="route",
            member_slugs=["front-desk"],
        ),
        TeamTemplate(
            slug="patient-support",
            name="Patient Support",
            description="Patient-facing concierge for FAQ and appointment help.",
            instructions="Help patients with FAQs and route sensitive requests to staff.",
            mode="route",
            member_slugs=["patient-concierge"],
        ),
    ],
    workflows=[
        WorkflowTemplate(
            slug="book-appointment",
            name="Book appointment",
            description="Patient or staff guided booking flow.",
            mode="sequential",
            steps=[
                WorkflowStepTemplate("Front desk intake", "front-desk", "agent"),
            ],
        ),
        WorkflowTemplate(
            slug="recall-reminder",
            name="Recall reminder",
            description="Identify due recalls and draft outreach.",
            mode="sequential",
            steps=[
                WorkflowStepTemplate("Recall review", "front-desk", "agent"),
            ],
        ),
    ],
)

DOMAIN_TEMPLATES: dict[str, DomainTemplate] = {
    "stock_broker": STOCK_BROKER,
    "dental_clinic": DENTAL_CLINIC,
}
