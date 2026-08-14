"""Provision domain starter packs into a new tenant workspace."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories import AgentRepository, TeamRepository, WorkflowRepository
from app.domains.templates import DEFAULT_MODEL, DOMAIN_TEMPLATES, DomainTemplate
from app.domains.types import WorkspaceDomain, normalize_domain
from app.tenancy.context import TenantContext


async def provision_domain_workspace(
    session: AsyncSession,
    *,
    context: TenantContext,
    domain: str | WorkspaceDomain,
) -> dict[str, Any]:
    """Create draft-and-publish agents, teams, and workflows for a domain template."""
    normalized = normalize_domain(domain)
    template = DOMAIN_TEMPLATES.get(normalized)
    if template is None:
        return {"domain": normalized, "provisioned": False}

    agent_repo = AgentRepository(session, context)
    team_repo = TeamRepository(session, context)
    workflow_repo = WorkflowRepository(session, context)

    agent_ids: dict[str, uuid.UUID] = {}
    team_ids: dict[str, uuid.UUID] = {}

    for spec in template.agents:
        config = await agent_repo.create_config(
            slug=spec.slug,
            name=spec.name,
            description=spec.description,
            domain=normalized,
        )
        draft = await agent_repo.create_draft(
            config_id=config.id,
            instructions=spec.instructions,
            model_id=DEFAULT_MODEL,
            temperature=0.2,
        )
        await agent_repo.publish(draft.id)
        agent_ids[spec.slug] = config.id

    for spec in template.teams:
        members = [agent_ids[slug] for slug in spec.member_slugs]
        config = await team_repo.create_config(
            slug=spec.slug,
            name=spec.name,
            description=spec.description,
            domain=normalized,
        )
        draft = await team_repo.create_draft(
            config_id=config.id,
            instructions=spec.instructions,
            mode=spec.mode,
            model_id=DEFAULT_MODEL,
            temperature=0.2,
            member_config_ids=members,
        )
        await team_repo.publish(draft.id)
        team_ids[spec.slug] = config.id

    workflow_ids: dict[str, uuid.UUID] = {}
    for spec in template.workflows:
        steps: list[dict[str, Any]] = []
        for step in spec.steps:
            if step.target_type == "agent":
                config_id = agent_ids.get(step.target_slug)
                if config_id is None:
                    continue
                steps.append(
                    {
                        "name": step.name,
                        "target_type": "agent",
                        "target_config_id": config_id,
                    }
                )
            elif step.target_type == "team":
                config_id = team_ids.get(step.target_slug)
                if config_id is None:
                    continue
                steps.append(
                    {
                        "name": step.name,
                        "target_type": "team",
                        "target_config_id": config_id,
                    }
                )
        if not steps:
            continue
        config = await workflow_repo.create_config(
            slug=spec.slug,
            name=spec.name,
            description=spec.description,
            domain=normalized,
        )
        draft = await workflow_repo.create_draft(
            config_id=config.id,
            mode=spec.mode,
            steps=steps,
        )
        await workflow_repo.publish(draft.id)
        workflow_ids[spec.slug] = config.id

    from app.domains.access import assign_domain_default_teams

    assigned = await assign_domain_default_teams(session, context, context.user_id)

    return {
        "domain": normalized,
        "provisioned": True,
        "agents": {slug: str(agent_id) for slug, agent_id in agent_ids.items()},
        "teams": {slug: str(team_id) for slug, team_id in team_ids.items()},
        "workflows": {
            slug: str(workflow_id) for slug, workflow_id in workflow_ids.items()
        },
        "assigned_teams": assigned,
    }
