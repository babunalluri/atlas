"""Tenant knowledge and memory helpers."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import Settings, get_settings
from app.knowledge.rerank import build_reranker
from app.knowledge.store import TenantKnowledgeStore
from app.tenancy.context import TenantContext


def build_tenant_knowledge_store(
    session: AsyncSession,
    context: TenantContext,
    *,
    settings: Settings | None = None,
) -> TenantKnowledgeStore:
    """Construct the Atlas-owned retriever with hybrid RRF + optional rerank."""
    cfg = settings or get_settings()
    reranker = build_reranker(
        mode=cfg.knowledge_reranker,
        cohere_api_key=cfg.cohere_api_key.get_secret_value() or None,
        cohere_model=cfg.knowledge_cohere_rerank_model,
    )
    return TenantKnowledgeStore(
        session,
        context,
        reranker=reranker,
        rrf_k=cfg.knowledge_rrf_k,
    )
