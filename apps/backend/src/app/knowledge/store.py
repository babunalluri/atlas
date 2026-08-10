"""Tenant-scoped knowledge retrieval (hybrid RRF + optional rerank)."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from math import sqrt
from typing import Any, Literal

from sqlalchemy import func, literal_column, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import KnowledgeChunk
from app.knowledge.rerank import Reranker
from app.tenancy.context import TenantContext

_TERM_RE = re.compile(r"[a-z0-9]{3,}")


@dataclass(frozen=True, slots=True)
class MemoryConfig:
    mode: Literal["none", "session", "persistent_user"] = "session"
    max_messages: int = 40

    @property
    def persistent(self) -> bool:
        return self.mode == "persistent_user"


class TenantKnowledgeStore:
    """Tenant-scoped retrieval boundary used by Agno knowledge adapters."""

    def __init__(
        self,
        session: AsyncSession,
        context: TenantContext,
        *,
        reranker: Reranker | None = None,
        rrf_k: int = 60,
    ) -> None:
        self.session = session
        self.context = context
        self.reranker = reranker
        self.rrf_k = max(1, rrf_k)

    async def chunks(
        self, knowledge_base_id: uuid.UUID, *, limit: int = 20
    ) -> list[dict[str, Any]]:
        rows = await self.session.scalars(
            select(KnowledgeChunk)
            .where(
                KnowledgeChunk.tenant_id == self.context.tenant_id,
                KnowledgeChunk.knowledge_base_id == knowledge_base_id,
            )
            .limit(min(limit, 100))
        )
        return [
            {"id": str(row.id), "content": row.content, "metadata": row.metadata_} for row in rows
        ]

    async def search(
        self,
        knowledge_base_id: uuid.UUID,
        query: str,
        query_embedding: list[float],
        *,
        top_k: int = 6,
        score_threshold: float = 0.25,
        max_context_chars: int = 12_000,
        candidate_multiplier: int = 4,
    ) -> list[dict[str, Any]]:
        """Hybrid vector + keyword retrieval fused with Reciprocal Rank Fusion."""
        top_k = max(1, min(top_k, 20))
        max_context_chars = max(1000, min(max_context_chars, 30_000))
        pool = max(top_k * max(2, candidate_multiplier), top_k)
        pool = min(pool, 80)

        base_filter = (
            KnowledgeChunk.tenant_id == self.context.tenant_id,
            KnowledgeChunk.knowledge_base_id == knowledge_base_id,
            KnowledgeChunk.embedding.is_not(None),
        )
        is_postgres = bool(self.session.bind and self.session.bind.dialect.name == "postgresql")

        vector_ranked = await self._vector_ranks(
            base_filter, query_embedding, pool=pool, is_postgres=is_postgres
        )
        keyword_ranked = await self._keyword_ranks(
            base_filter, query, pool=pool, is_postgres=is_postgres
        )
        fused = self._rrf_fuse(vector_ranked, keyword_ranked)

        # Keep items with usable semantic signal or a strong keyword hit.
        filtered: list[tuple[KnowledgeChunk, float, float, float]] = []
        for row, rrf_score, semantic_score, keyword_score in fused:
            if semantic_score >= score_threshold or keyword_score >= max(score_threshold, 0.35):
                filtered.append((row, rrf_score, semantic_score, keyword_score))
        if not filtered and fused:
            # Soft fallback so empty threshold configs still return something ranked.
            filtered = list(fused[:top_k])

        documents = [
            self._to_document(
                row,
                knowledge_base_id=knowledge_base_id,
                semantic_score=semantic_score,
                keyword_score=keyword_score,
                hybrid_score=rrf_score,
            )
            for row, rrf_score, semantic_score, keyword_score in filtered
        ]

        if self.reranker is not None and documents:
            documents = await self.reranker.rerank(
                query,
                documents,
                top_n=min(len(documents), max(top_k * 2, top_k)),
            )
            documents.sort(
                key=lambda item: float(
                    (item.get("meta_data") or {}).get("rerank_score")
                    or (item.get("meta_data") or {}).get("hybrid_score")
                    or 0.0
                ),
                reverse=True,
            )
        else:
            documents = documents[: max(top_k * 2, top_k)]

        return self._fit_context(documents[:top_k], max_context_chars=max_context_chars)

    async def _vector_ranks(
        self,
        base_filter: tuple[Any, ...],
        query_embedding: list[float],
        *,
        pool: int,
        is_postgres: bool,
    ) -> list[tuple[KnowledgeChunk, float]]:
        if is_postgres:
            distance = KnowledgeChunk.embedding.cosine_distance(query_embedding)
            vector_rows = (
                await self.session.execute(
                    select(KnowledgeChunk, distance.label("distance"))
                    .where(*base_filter)
                    .order_by(distance)
                    .limit(pool)
                )
            ).all()
            return [
                (row, max(0.0, 1.0 - float(distance_value)))
                for row, distance_value in vector_rows
            ]

        chunk_rows = (
            await self.session.scalars(select(KnowledgeChunk).where(*base_filter).limit(1000))
        ).all()
        scored = [
            (row, self._cosine(query_embedding, list(row.embedding or []))) for row in chunk_rows
        ]
        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[:pool]

    async def _keyword_ranks(
        self,
        base_filter: tuple[Any, ...],
        query: str,
        *,
        pool: int,
        is_postgres: bool,
    ) -> list[tuple[KnowledgeChunk, float]]:
        terms = sorted(set(_TERM_RE.findall(query.lower())))
        if not terms:
            return []

        if is_postgres:
            ts_query = func.plainto_tsquery(literal_column("'english'"), query)
            ts_vector = func.to_tsvector(literal_column("'english'"), KnowledgeChunk.content)
            rank = func.ts_rank_cd(ts_vector, ts_query)
            rows = (
                await self.session.execute(
                    select(KnowledgeChunk, rank.label("rank"))
                    .where(*base_filter, ts_vector.op("@@")(ts_query))
                    .order_by(rank.desc())
                    .limit(pool)
                )
            ).all()
            if rows:
                max_rank = max(float(value) for _, value in rows) or 1.0
                return [(row, float(value) / max_rank) for row, value in rows]

        # SQLite / fallback: load tenant pool and score in Python.
        chunk_rows = (
            await self.session.scalars(select(KnowledgeChunk).where(*base_filter).limit(1000))
        ).all()
        term_set = set(terms)
        scored: list[tuple[KnowledgeChunk, float]] = []
        for row in chunk_rows:
            content_terms = set(_TERM_RE.findall(row.content.lower()))
            if not content_terms:
                continue
            overlap = len(term_set & content_terms) / len(term_set)
            if overlap <= 0:
                continue
            scored.append((row, overlap))
        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[:pool]

    def _rrf_fuse(
        self,
        vector_ranked: list[tuple[KnowledgeChunk, float]],
        keyword_ranked: list[tuple[KnowledgeChunk, float]],
    ) -> list[tuple[KnowledgeChunk, float, float, float]]:
        semantic_by_id = {row.id: score for row, score in vector_ranked}
        keyword_by_id = {row.id: score for row, score in keyword_ranked}
        rows_by_id = {row.id: row for row, _ in vector_ranked}
        rows_by_id.update({row.id: row for row, _ in keyword_ranked})

        rrf_scores: dict[uuid.UUID, float] = {}
        for rank, (row, _) in enumerate(vector_ranked, start=1):
            rrf_scores[row.id] = rrf_scores.get(row.id, 0.0) + 1.0 / (self.rrf_k + rank)
        for rank, (row, _) in enumerate(keyword_ranked, start=1):
            rrf_scores[row.id] = rrf_scores.get(row.id, 0.0) + 1.0 / (self.rrf_k + rank)

        fused: list[tuple[KnowledgeChunk, float, float, float]] = []
        for chunk_id, rrf_score in rrf_scores.items():
            row = rows_by_id[chunk_id]
            fused.append(
                (
                    row,
                    rrf_score,
                    semantic_by_id.get(chunk_id, 0.0),
                    keyword_by_id.get(chunk_id, 0.0),
                )
            )
        fused.sort(key=lambda item: item[1], reverse=True)
        return fused

    @staticmethod
    def _to_document(
        row: KnowledgeChunk,
        *,
        knowledge_base_id: uuid.UUID,
        semantic_score: float,
        keyword_score: float,
        hybrid_score: float,
    ) -> dict[str, Any]:
        return {
            "id": str(row.id),
            "name": str((row.metadata_ or {}).get("filename") or "knowledge"),
            "content": row.content,
            "meta_data": {
                **(row.metadata_ or {}),
                "knowledge_base_id": str(knowledge_base_id),
                "source_id": str(row.source_id),
                "semantic_score": round(semantic_score, 4),
                "keyword_score": round(keyword_score, 4),
                "hybrid_score": round(hybrid_score, 4),
            },
        }

    @staticmethod
    def _fit_context(
        documents: list[dict[str, Any]], *, max_context_chars: int
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        used_chars = 0
        for doc in documents:
            content = str(doc.get("content") or "")
            if used_chars + len(content) > max_context_chars:
                remaining = max_context_chars - used_chars
                if remaining < 200:
                    break
                content = content[:remaining]
            used_chars += len(content)
            results.append({**doc, "content": content})
        return results

    @staticmethod
    def _cosine(left: list[float], right: list[float]) -> float:
        if not left or len(left) != len(right):
            return 0.0
        left_norm = sqrt(sum(value * value for value in left))
        right_norm = sqrt(sum(value * value for value in right))
        if left_norm == 0 or right_norm == 0:
            return 0.0
        return sum(a * b for a, b in zip(left, right, strict=True)) / (left_norm * right_norm)
