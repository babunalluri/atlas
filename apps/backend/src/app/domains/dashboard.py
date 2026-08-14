"""Domain-specific workspace dashboard payloads."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AgentConfig,
    ApprovalBinding,
    ApprovalStatus,
    TeamConfig,
    Tenant,
    WorkflowConfig,
)
from app.domains.desk_snapshot import DeskSnapshotService
from app.domains.types import (
    DOMAIN_LABELS,
    STOCK_BROKER_DESK_TEAMS,
    WorkspaceDomain,
    normalize_domain,
)
from app.metrics.service import MetricsService
from app.tenancy.context import TenantContext

STOCK_BROKER_CHAT_ORDER = STOCK_BROKER_DESK_TEAMS


def order_desk_chat_targets(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Stable desk-tab order: default pack slugs first, then remaining by name.

    Unpublished (draft) rows never become tabs. Callers should pass assigned
    published teams; this still drops drafts if a catalog dump is supplied.
    """
    by_slug: dict[str, dict[str, Any]] = {}
    extras: list[dict[str, Any]] = []
    for row in rows:
        if not row.get("published"):
            continue
        slug = str(row.get("slug") or "")
        if slug in STOCK_BROKER_DESK_TEAMS and slug not in by_slug:
            by_slug[slug] = row
        else:
            extras.append(row)
    ordered = [by_slug[slug] for slug in STOCK_BROKER_CHAT_ORDER if slug in by_slug]
    extras.sort(key=lambda row: str(row.get("name") or "").lower())
    return ordered + extras


def _team_chat_target(row: Any) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "slug": row.slug,
        "name": row.name,
        "published": bool(row.published_version_id),
    }


class DomainDashboardService:
    def __init__(self, session: AsyncSession, context: TenantContext) -> None:
        self.session = session
        self.context = context

    async def dashboard(self, *, days: int = 30, desk_snapshot: bool = False) -> dict[str, Any]:
        tenant = await self.session.scalar(
            select(Tenant).where(Tenant.id == self.context.tenant_id)
        )
        tenant_domain = normalize_domain(getattr(tenant, "domain", None) if tenant else None)
        metrics = await MetricsService(self.session, self.context).dashboard(days=days)
        catalog = await self._catalog_counts()
        desk = DeskSnapshotService(self.session, self.context)
        broker_tools = await desk.assigned_tools() if tenant_domain == "stock_broker" else []
        snapshot = (
            await desk.snapshot()
            if tenant_domain == "stock_broker" and desk_snapshot
            else None
        )

        widgets = self._widgets_for_domain(tenant_domain, metrics, catalog, broker_tools)
        if snapshot and snapshot.get("widgets"):
            widgets = [
                widget for widget in widgets if widget.get("group") != "brokers"
            ] + list(snapshot["widgets"])
        quick_links = self._quick_links(tenant_domain, catalog)
        chat_targets = await self._chat_targets(tenant_domain, catalog)

        return {
            "domain": tenant_domain,
            "domain_label": DOMAIN_LABELS[tenant_domain],
            "range_days": days,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "widgets": widgets,
            "quick_links": quick_links,
            "chat_targets": chat_targets,
            "broker_tools": broker_tools,
            "desk_snapshot": snapshot,
            "metrics": metrics,
            "catalog": catalog,
        }

    async def customer_desk(self, *, desk_snapshot: bool = False) -> dict[str, Any]:
        """End-user desk: assigned team chats plus broker widgets.

        Tabs come from assigned published teams (same rule as the admin
        workspace), not a hardcoded Learning / Paper / Live set and not every
        catalog team. Workflows stay off this payload: desk chat streams teams,
        and the desk API has never exposed workflow sessions. Default desk teams
        are assigned on create/provision, not on every desk load.
        """
        payload = await self.dashboard(days=30, desk_snapshot=desk_snapshot)
        if payload["domain"] != "stock_broker":
            return {
                **payload,
                "chat_targets": [],
                "widgets": [],
                "quick_links": [],
                "broker_tools": [],
                "desk_snapshot": None,
            }
        keep_ids = {"learning", "paper_flow", "live_approval", "broker_tools"}
        payload["widgets"] = [
            widget
            for widget in payload["widgets"]
            if widget.get("id") in keep_ids or widget.get("group") == "brokers"
        ]
        payload["quick_links"] = []
        return payload

    async def _catalog_counts(self) -> dict[str, Any]:
        agents = list(
            (
                await self.session.scalars(
                    select(AgentConfig).where(
                        AgentConfig.tenant_id == self.context.tenant_id
                    )
                )
            ).all()
        )
        teams = list(
            (
                await self.session.scalars(
                    select(TeamConfig).where(
                        TeamConfig.tenant_id == self.context.tenant_id
                    )
                )
            ).all()
        )
        workflows = list(
            (
                await self.session.scalars(
                    select(WorkflowConfig).where(
                        WorkflowConfig.tenant_id == self.context.tenant_id
                    )
                )
            ).all()
        )
        pending_approvals = int(
            await self.session.scalar(
                select(func.count())
                .select_from(ApprovalBinding)
                .where(
                    ApprovalBinding.tenant_id == self.context.tenant_id,
                    ApprovalBinding.status == ApprovalStatus.pending,
                )
            )
            or 0
        )
        return {
            "agents": len(agents),
            "teams": len(teams),
            "workflows": len(workflows),
            "published_agents": sum(1 for row in agents if row.published_version_id),
            "published_teams": sum(1 for row in teams if row.published_version_id),
            "published_workflows": sum(
                1 for row in workflows if row.published_version_id
            ),
            "pending_approvals": pending_approvals,
            "team_slugs": [row.slug for row in teams],
            "workflow_slugs": [row.slug for row in workflows],
            "teams_detail": [
                {
                    "id": str(row.id),
                    "slug": row.slug,
                    "name": row.name,
                    "published": bool(row.published_version_id),
                }
                for row in teams
            ],
        }

    def _widgets_for_domain(
        self,
        domain: WorkspaceDomain,
        metrics: dict[str, Any],
        catalog: dict[str, Any],
        broker_tools: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        kpis = metrics.get("kpis", {})
        if domain == "stock_broker":
            team_slugs = set(catalog.get("team_slugs", []))
            brokers = list(broker_tools or [])
            connected = [row["name"] for row in brokers if row.get("active")]
            widgets = [
                {
                    "id": "desk_runs",
                    "label": "Desk runs",
                    "value": str(kpis.get("runs", 0)),
                    "hint": "Learning, paper, and live chats in range",
                    "group": "ops",
                },
                {
                    "id": "success_rate",
                    "label": "Run success rate",
                    "value": f"{(kpis.get('success_rate', 0) * 100):.1f}%",
                    "hint": "Successful runs / total runs",
                    "group": "ops",
                },
                {
                    "id": "latency_p95",
                    "label": "P95 latency",
                    "value": self._format_ms(kpis.get("latency_p95_ms")),
                    "hint": "End-to-end run latency",
                    "group": "ops",
                },
                {
                    "id": "approval_queue",
                    "label": "Pending approvals",
                    "value": str(catalog.get("pending_approvals", 0)),
                    "hint": "Mutating paper and live actions awaiting HITL",
                    "group": "risk",
                },
                {
                    "id": "live_approval",
                    "label": "Live trading",
                    "value": "Ready" if "live-trading" in team_slugs else "Setup",
                    "hint": "Assigned broker + live status window",
                    "group": "risk",
                },
                {
                    "id": "paper_flow",
                    "label": "Paper trading",
                    "value": "Ready" if "paper-trading" in team_slugs else "Setup",
                    "hint": "Signal → virtual fill window",
                    "group": "signals",
                },
                {
                    "id": "learning",
                    "label": "Learning",
                    "value": "Ready" if "learning" in team_slugs else "Setup",
                    "hint": "Knowledge-base teaching window",
                    "group": "signals",
                },
                {
                    "id": "customer_sessions",
                    "label": "Chat sessions",
                    "value": str(kpis.get("unique_sessions", 0)),
                    "hint": "Unique desk sessions in range",
                    "group": "signals",
                },
                {
                    "id": "customer_teams",
                    "label": "Workspace chats",
                    "value": str(
                        sum(
                            1
                            for slug in team_slugs
                            if slug in {"learning", "paper-trading", "live-trading"}
                        )
                    ),
                    "hint": "Learning, Paper trading, Live trading",
                    "group": "signals",
                },
                {
                    "id": "broker_tools",
                    "label": "Broker tools",
                    "value": ", ".join(connected) if connected else "None",
                    "hint": "Assigned on Live trading — refresh loads via that team",
                    "group": "brokers",
                },
            ]
            for row in brokers:
                status = "Published" if row.get("published") else "Draft"
                if not row.get("active"):
                    status = "Inactive"
                widgets.append(
                    {
                        "id": f"broker_{row['slug']}",
                        "label": row["name"],
                        "value": status,
                        "hint": row.get("connection_status") or row["slug"],
                        "group": "brokers",
                    }
                )
            return widgets
        if domain == "dental_clinic":
            return [
                {
                    "id": "patient_sessions",
                    "label": "Patient sessions",
                    "value": str(kpis.get("unique_sessions", 0)),
                    "hint": "Unique chat sessions in range",
                    "group": "clinic",
                },
                {
                    "id": "appointments_flows",
                    "label": "Booking workflows",
                    "value": str(
                        sum(
                            1
                            for slug in catalog.get("workflow_slugs", [])
                            if slug in {"book-appointment", "recall-reminder"}
                        )
                    ),
                    "hint": "Published appointment and recall flows",
                    "group": "clinic",
                },
                {
                    "id": "pending_approvals",
                    "label": "Staff approvals",
                    "value": str(catalog.get("pending_approvals", 0)),
                    "hint": "Sensitive actions waiting on staff",
                    "group": "clinic",
                },
                {
                    "id": "front_desk_team",
                    "label": "Front desk team",
                    "value": "Ready"
                    if "front-desk-team" in catalog.get("team_slugs", [])
                    else "Setup",
                    "hint": "Scheduling and intake team",
                    "group": "clinic",
                },
                {
                    "id": "patient_support",
                    "label": "Patient support",
                    "value": "Ready"
                    if "patient-support" in catalog.get("team_slugs", [])
                    else "Setup",
                    "hint": "Patient-facing concierge",
                    "group": "clinic",
                },
                {
                    "id": "error_rate",
                    "label": "Error rate",
                    "value": f"{(kpis.get('error_rate', 0) * 100):.1f}%",
                    "hint": "Failed runs in range",
                    "group": "clinic",
                },
            ]
        return [
            {
                "id": "runs",
                "label": "Runs",
                "value": str(kpis.get("runs", 0)),
                "hint": "Total runs in range",
                "group": "ops",
            },
            {
                "id": "success_rate",
                "label": "Success rate",
                "value": f"{(kpis.get('success_rate', 0) * 100):.1f}%",
                "hint": "Successful runs / total runs",
                "group": "ops",
            },
        ]

    async def _chat_targets(
        self, domain: WorkspaceDomain, catalog: dict[str, Any]
    ) -> list[dict[str, Any]]:
        detail = list(catalog.get("teams_detail") or [])
        if domain != "stock_broker":
            return detail
        from app.db.repositories import TeamRepository

        assigned = await TeamRepository(
            self.session, self.context
        ).list_available_for_user(self.context.user_id)
        rows = [_team_chat_target(row) for row in assigned]
        if not rows and self.context.can_administer():
            rows = [row for row in detail if row.get("published")]
        return order_desk_chat_targets(rows)

    def _quick_links(
        self, domain: WorkspaceDomain, catalog: dict[str, Any]
    ) -> list[dict[str, str]]:
        if domain == "stock_broker":
            return [
                {"label": "Learning / Paper / Live", "href": "/admin/teams"},
                {"label": "Broker tools", "href": "/admin/tools"},
                {"label": "Workflows", "href": "/admin/workflows"},
                {"label": "Approvals", "href": "/admin/approvals"},
            ]
        if domain == "dental_clinic":
            return [
                {"label": "Front desk team", "href": "/admin/teams"},
                {"label": "Patient workflows", "href": "/admin/workflows"},
                {"label": "Schedules", "href": "/admin/schedules"},
                {"label": "Metrics", "href": "/admin/metrics"},
            ]
        return [{"label": "Metrics", "href": "/admin/metrics"}]

    @staticmethod
    def _format_ms(value: Any) -> str:
        if value is None:
            return "—"
        ms = int(value)
        return f"{ms} ms" if ms < 1000 else f"{ms / 1000:.2f} s"
