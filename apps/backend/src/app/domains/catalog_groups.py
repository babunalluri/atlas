"""Classify agents, teams, and workflows into workspace domain groups.

Used by admin catalog grouping and by platform import so Stock Broker / Dental
packs keep their domain after copy into another tenant — even when the
destination slug becomes ``{slug}-copy``.
"""

from __future__ import annotations

import re
from typing import Any

from app.domains.templates import DOMAIN_TEMPLATES
from app.domains.types import WORKSPACE_DOMAINS, WorkspaceDomain

COPY_SUFFIX_RE = re.compile(r"-copy(?:-\d+)?$", re.IGNORECASE)

# Desk areas for the Stock Broker pack (agents, teams, and workflows).
STOCK_BROKER_DESKS: dict[str, str] = {
    "learning": "learning",
    "learning-guide": "learning",
    "paper-trading": "paper",
    "paper-trader": "paper",
    "paper-from-signal": "paper",
    "live-trading": "live",
    "live-trader": "live",
    "live-approval": "live",
    "research": "research",
    "researcher": "research",
}

# Slugs retired from the live pack that still exist in tenants provisioned
# earlier. Research is leader-only now, but pre-existing tenants kept their
# ``researcher`` agent — keep classifying it so pack copy does not dump it into
# General. A template reintroducing the slug wins over this map.
LEGACY_SLUG_DOMAINS: dict[str, WorkspaceDomain] = {
    "researcher": "stock_broker",
}


def canonical_catalog_slug(slug: str) -> str:
    """Strip clone/import ``-copy`` / ``-copy-N`` suffixes for grouping."""
    return COPY_SUFFIX_RE.sub("", (slug or "").strip().lower())


def coerce_workspace_domain(value: str | None) -> WorkspaceDomain:
    raw = (value or "generic").strip().lower().replace("-", "_")
    if raw == "general":
        raw = "generic"
    if raw in WORKSPACE_DOMAINS:
        return raw  # type: ignore[return-value]
    return "generic"


def slug_domain_map() -> dict[str, WorkspaceDomain]:
    mapping: dict[str, WorkspaceDomain] = dict(LEGACY_SLUG_DOMAINS)
    for domain, template in DOMAIN_TEMPLATES.items():
        for spec in (*template.agents, *template.teams, *template.workflows):
            mapping[spec.slug] = domain  # type: ignore[assignment]
    return mapping


_SLUG_DOMAIN: dict[str, WorkspaceDomain] | None = None


def _slug_domains() -> dict[str, WorkspaceDomain]:
    global _SLUG_DOMAIN
    if _SLUG_DOMAIN is None:
        _SLUG_DOMAIN = slug_domain_map()
    return _SLUG_DOMAIN


def classify_catalog_slug(slug: str) -> tuple[WorkspaceDomain, str | None]:
    """Return ``(domain, desk)`` from a catalog slug.

    ``desk`` is set only for Stock Broker learning / paper / live / research resources.
    """
    canonical = canonical_catalog_slug(slug)
    domain = _slug_domains().get(canonical, "generic")
    desk = STOCK_BROKER_DESKS.get(canonical) if domain == "stock_broker" else None
    return domain, desk


def resolve_catalog_domain(
    *,
    slug: str,
    stored_domain: str | None = None,
    tenant_domain: str | None = None,
) -> WorkspaceDomain:
    """Prefer pack slugs, then stored metadata, then the source tenant domain."""
    from_slug, _ = classify_catalog_slug(slug)
    if from_slug != "generic":
        return from_slug
    stored = coerce_workspace_domain(stored_domain)
    if stored != "generic":
        return stored
    return coerce_workspace_domain(tenant_domain)


async def stored_config_domain(
    session: Any,
    tenant_id: Any,
    domain: str | None,
) -> str:
    """Resolve domain for a new config: explicit value, else tenant domain."""
    from app.db.models import Tenant

    if domain is not None:
        return coerce_workspace_domain(domain)
    tenant = await session.get(Tenant, tenant_id)
    return coerce_workspace_domain(getattr(tenant, "domain", None) if tenant else None)
