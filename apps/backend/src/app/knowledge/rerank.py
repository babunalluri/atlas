"""Optional post-retrieval reranking for tenant knowledge search."""

from __future__ import annotations

import logging
import re
from typing import Any, Protocol

import httpx

logger = logging.getLogger(__name__)

_TERM_RE = re.compile(r"[a-z0-9]{3,}")


class Reranker(Protocol):
    async def rerank(
        self,
        query: str,
        documents: list[dict[str, Any]],
        *,
        top_n: int,
    ) -> list[dict[str, Any]]: ...


class LocalLexicalReranker:
    """Cheap local rerank: blend retrieval score with query-term overlap.

    No external dependency. Improves exact-term hits after hybrid fusion.
    """

    def __init__(self, *, lexical_weight: float = 0.35) -> None:
        self.lexical_weight = max(0.0, min(lexical_weight, 0.8))

    async def rerank(
        self,
        query: str,
        documents: list[dict[str, Any]],
        *,
        top_n: int,
    ) -> list[dict[str, Any]]:
        if not documents:
            return []
        terms = set(_TERM_RE.findall(query.lower()))
        scored: list[tuple[float, dict[str, Any]]] = []
        for doc in documents:
            meta = dict(doc.get("meta_data") or {})
            base = float(meta.get("hybrid_score") or meta.get("semantic_score") or 0.0)
            content = str(doc.get("content") or "").lower()
            content_terms = set(_TERM_RE.findall(content))
            lexical = (
                len(terms & content_terms) / len(terms) if terms else 0.0
            )
            score = (1.0 - self.lexical_weight) * base + self.lexical_weight * lexical
            meta["rerank_score"] = round(score, 4)
            meta["lexical_score"] = round(lexical, 4)
            scored.append((score, {**doc, "meta_data": meta}))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [doc for _, doc in scored[: max(1, top_n)]]


class CohereReranker:
    """Optional Cohere Rerank API. Falls back to local ranking on failure."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "rerank-v3.5",
        fallback: Reranker | None = None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.fallback = fallback or LocalLexicalReranker()

    async def rerank(
        self,
        query: str,
        documents: list[dict[str, Any]],
        *,
        top_n: int,
    ) -> list[dict[str, Any]]:
        if not documents:
            return []
        top_n = max(1, min(top_n, len(documents)))
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.post(
                    "https://api.cohere.com/v2/rerank",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self.model,
                        "query": query,
                        "top_n": top_n,
                        "documents": [str(doc.get("content") or "") for doc in documents],
                    },
                )
                response.raise_for_status()
                payload = response.json()
            results = payload.get("results") or []
            ranked: list[dict[str, Any]] = []
            for item in results:
                index = int(item["index"])
                if index < 0 or index >= len(documents):
                    continue
                doc = documents[index]
                meta = dict(doc.get("meta_data") or {})
                meta["rerank_score"] = round(float(item.get("relevance_score") or 0.0), 4)
                ranked.append({**doc, "meta_data": meta})
            return ranked[:top_n] or await self.fallback.rerank(
                query, documents, top_n=top_n
            )
        except Exception:
            logger.warning("Cohere rerank failed; using local lexical reranker", exc_info=True)
            return await self.fallback.rerank(query, documents, top_n=top_n)


def build_reranker(
    *,
    mode: str,
    cohere_api_key: str | None = None,
    cohere_model: str = "rerank-v3.5",
) -> Reranker | None:
    """Build a reranker from settings. ``off`` disables post-fusion rerank."""
    normalized = (mode or "local").strip().lower()
    if normalized in {"off", "none", "false", "0"}:
        return None
    local = LocalLexicalReranker()
    if normalized == "local":
        return local
    if normalized == "cohere":
        if not cohere_api_key:
            logger.warning("KNOWLEDGE_RERANKER=cohere but no Cohere API key; using local")
            return local
        return CohereReranker(api_key=cohere_api_key, model=cohere_model, fallback=local)
    logger.warning("Unknown KNOWLEDGE_RERANKER=%r; using local", mode)
    return local
