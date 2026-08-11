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
            slug="signal-publisher",
            name="Signal Publisher",
            description="Publish and suppress trading signal packs for ops.",
            instructions=(
                "You are the Stock Broker Signal Publisher. You help operators review "
                "draft signals, publish approved packs, and suppress stale or bad signals. "
                "Confirm every mutating action with the operator before running tools."
            ),
        ),
        AgentTemplate(
            slug="param-editor",
            name="Param Editor",
            description="Maintain strategy parameter schemas and drafts.",
            instructions=(
                "You are the Stock Broker Param Editor. You validate parameter schemas, "
                "prepare drafts, and explain diffs to operators. Never publish live params "
                "without explicit confirmation."
            ),
        ),
        AgentTemplate(
            slug="feed-monitor",
            name="Feed Monitor",
            description="Watch signal feed health, lag, and stale counts.",
            instructions=(
                "You are the Stock Broker Feed Monitor. Report lag, stale signal counts, "
                "and feed errors with concrete metrics. Escalate publish issues to Signal Publisher."
            ),
        ),
        AgentTemplate(
            slug="compliance-officer",
            name="Compliance Officer",
            description="Live approval queue, risk caps, and kill switch governance.",
            instructions=(
                "You are the Stock Broker Compliance Officer. You own live approval, deny/approve "
                "decisions, and kill-switch actions. Operators cannot self-approve live trading."
            ),
        ),
        AgentTemplate(
            slug="learning-guide",
            name="Learning Guide",
            description="Customer education from the knowledge base.",
            instructions=(
                "You are the Stock Broker Learning Guide. Teach customers using curated knowledge "
                "base content. Do not give personalized investment advice."
            ),
        ),
        AgentTemplate(
            slug="customer-concierge",
            name="Customer Concierge",
            description="Customer-facing signals, paper trading, and account questions.",
            instructions=(
                "You are the Stock Broker Customer Concierge. Help customers understand signals, "
                "paper trading status, and account questions. Route live trading requests to compliance."
            ),
        ),
    ],
    teams=[
        TeamTemplate(
            slug="ops-desk",
            name="Ops Desk",
            description="Internal operators: publish, params, feed, compliance routing.",
            instructions=(
                "Lead the Stock Broker Ops Desk. Route publish work to Signal Publisher, schema "
                "work to Param Editor, feed health to Feed Monitor, and live governance to Compliance."
            ),
            mode="route",
            member_slugs=[
                "signal-publisher",
                "param-editor",
                "feed-monitor",
                "compliance-officer",
            ],
        ),
        TeamTemplate(
            slug="customer-support",
            name="Customer Support",
            description="Customer-facing concierge for signals and paper trading.",
            instructions=(
                "Lead customer support for Stock Broker. Use Customer Concierge for end-user questions."
            ),
            mode="route",
            member_slugs=["customer-concierge"],
        ),
        TeamTemplate(
            slug="learning",
            name="Learning",
            description="Education team powered by the knowledge base.",
            instructions="Help customers learn platform concepts via the Learning Guide.",
            mode="route",
            member_slugs=["learning-guide"],
        ),
    ],
    workflows=[
        WorkflowTemplate(
            slug="publish-signal",
            name="Publish signal",
            description="Ops workflow: review draft → publish or suppress.",
            mode="sequential",
            steps=[
                WorkflowStepTemplate("Review draft", "signal-publisher", "agent"),
                WorkflowStepTemplate("Compliance check", "compliance-officer", "agent"),
            ],
        ),
        WorkflowTemplate(
            slug="live-approval",
            name="Live approval",
            description="Compliance review before arming live trading.",
            mode="sequential",
            steps=[
                WorkflowStepTemplate("Compliance review", "compliance-officer", "agent"),
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
