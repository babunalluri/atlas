"""Domain-specific workspace dashboard payloads."""

from __future__ import annotations

import uuid
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
from app.domains.types import DOMAIN_LABELS, WorkspaceDomain, normalize_domain
from app.metrics.service import MetricsService
from app.tenancy.context import TenantContext


class DomainDashboardService:
    def __init__(self, session: AsyncSession, context: TenantContext) -> None:
        self.session = session
        self.context = context

    async def dashboard(self, *, days: int = 30) -> dict[str, Any]:
        tenant = await self.session.scalar(
            select(Tenant).where(Tenant.id == self.context.tenant_id)
        )
        tenant_domain = normalize_domain(getattr(tenant, "domain", None) if tenant else None)
        metrics = await MetricsService(self.session, self.context).dashboard(days=days)
        catalog = await self._catalog_counts()

        widgets = self._widgets_for_domain(tenant_domain, metrics, catalog)
        quick_links = self._quick_links(tenant_domain, catalog)

        return {
            "domain": tenant_domain,
            "domain_label": DOMAIN_LABELS[tenant_domain],
            "range_days": days,
            "widgets": widgets,
            "quick_links": quick_links,
            "metrics": metrics,
            "catalog": catalog,
        }

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
        }

    def _widgets_for_domain(
        self,
        domain: WorkspaceDomain,
        metrics: dict[str, Any],
        catalog: dict[str, Any],
    ) -> list[dict[str, Any]]:
        kpis = metrics.get("kpis", {})
        if domain == "stock_broker":
            return [
                {
                    "id": "ops_runs",
                    "label": "Ops desk runs",
                    "value": str(kpis.get("runs", 0)),
                    "hint": "Agent and team runs in range",
                },
                {
                    "id": "approval_queue",
                    "label": "Pending approvals",
                    "value": str(catalog.get("pending_approvals", 0)),
                    "hint": "Live and mutating actions awaiting review",
                },
                {
                    "id": "success_rate",
                    "label": "Run success rate",
                    "value": f"{(kpis.get('success_rate', 0) * 100):.1f}%",
                    "hint": "Successful runs / total runs",
                },
                {
                    "id": "published_signals",
                    "label": "Published workflows",
                    "value": str(catalog.get("published_workflows", 0)),
                    "hint": "Publish signal and live approval flows",
                },
                {
                    "id": "customer_teams",
                    "label": "Customer teams",
                    "value": str(
                        sum(
                            1
                            for slug in catalog.get("team_slugs", [])
                            if slug in {"customer-support", "learning"}
                        )
                    ),
                    "hint": "Concierge and learning surfaces",
                },
                {
                    "id": "latency_p95",
                    "label": "P95 latency",
                    "value": self._format_ms(kpis.get("latency_p95_ms")),
                    "hint": "End-to-end run latency",
                },
            ]
        if domain == "dental_clinic":
            return [
                {
                    "id": "patient_sessions",
                    "label": "Patient sessions",
                    "value": str(kpis.get("unique_sessions", 0)),
                    "hint": "Unique chat sessions in range",
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
                },
                {
                    "id": "pending_approvals",
                    "label": "Staff approvals",
                    "value": str(catalog.get("pending_approvals", 0)),
                    "hint": "Sensitive actions waiting on staff",
                },
                {
                    "id": "front_desk_team",
                    "label": "Front desk team",
                    "value": "Ready"
                    if "front-desk-team" in catalog.get("team_slugs", [])
                    else "Setup",
                    "hint": "Scheduling and intake team",
                },
                {
                    "id": "patient_support",
                    "label": "Patient support",
                    "value": "Ready"
                    if "patient-support" in catalog.get("team_slugs", [])
                    else "Setup",
                    "hint": "Patient-facing concierge",
                },
                {
                    "id": "error_rate",
                    "label": "Error rate",
                    "value": f"{(kpis.get('error_rate', 0) * 100):.1f}%",
                    "hint": "Failed runs in range",
                },
            ]
        return [
            {
                "id": "runs",
                "label": "Runs",
                "value": str(kpis.get("runs", 0)),
                "hint": "Total runs in range",
            },
            {
                "id": "success_rate",
                "label": "Success rate",
                "value": f"{(kpis.get('success_rate', 0) * 100):.1f}%",
                "hint": "Successful runs / total runs",
            },
        ]

    def _quick_links(
        self, domain: WorkspaceDomain, catalog: dict[str, Any]
    ) -> list[dict[str, str]]:
        if domain == "stock_broker":
            links = [
                {"label": "Ops Desk team", "href": "/admin/teams"},
                {"label": "Workflows", "href": "/admin/workflows"},
                {"label": "Approvals", "href": "/admin/approvals"},
                {"label": "Metrics", "href": "/admin/metrics"},
            ]
            if "publish-signal" in catalog.get("workflow_slugs", []):
                links.insert(1, {"label": "Publish signal flow", "href": "/admin/workflows"})
            return links
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
