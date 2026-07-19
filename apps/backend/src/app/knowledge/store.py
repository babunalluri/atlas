import uuid
from dataclasses import dataclass
from math import sqrt
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import KnowledgeChunk
from app.tenancy.context import TenantContext


@dataclass(frozen=True, slots=True)
class MemoryConfig:
    mode: Literal["none", "session", "persistent_user"] = "session"
    max_messages: int = 40

    @property
    def persistent(self) -> bool:
        return self.mode == "persistent_user"


class TenantKnowledgeStore:
    """Tenant-scoped retrieval boundary used by Agno knowledge adapters."""

    def __init__(self, session: AsyncSession, context: TenantContext) -> None:
        self.session = session
        self.context = context

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
    ) -> list[dict[str, Any]]:
        """Hybrid semantic/keyword retrieval with mandatory tenant and base filters."""
        top_k = max(1, min(top_k, 20))
        max_context_chars = max(1000, min(max_context_chars, 30_000))
        base_filter = (
            KnowledgeChunk.tenant_id == self.context.tenant_id,
            KnowledgeChunk.knowledge_base_id == knowledge_base_id,
            KnowledgeChunk.embedding.is_not(None),
        )
        is_postgres = bool(self.session.bind and self.session.bind.dialect.name == "postgresql")
        candidates: list[tuple[KnowledgeChunk, float]]
        if is_postgres:
            distance = KnowledgeChunk.embedding.cosine_distance(query_embedding)
            vector_rows = (
                await self.session.execute(
                    select(KnowledgeChunk, distance.label("distance"))
                    .where(*base_filter)
                    .order_by(distance)
                    .limit(min(top_k * 4, 80))
                )
            ).all()
            candidates = [
                (row, max(0.0, 1.0 - float(distance_value))) for row, distance_value in vector_rows
            ]
        else:
            chunk_rows = (
                await self.session.scalars(select(KnowledgeChunk).where(*base_filter).limit(1000))
            ).all()
            candidates = [
                (row, self._cosine(query_embedding, list(row.embedding or [])))
                for row in chunk_rows
            ]

        query_terms = {word for word in query.lower().split() if len(word) > 2}
        ranked: list[tuple[KnowledgeChunk, float, float]] = []
        for row, semantic_score in candidates:
            content_terms = set(row.content.lower().split())
            keyword_score = (
                len(query_terms & content_terms) / len(query_terms) if query_terms else 0.0
            )
            hybrid_score = semantic_score * 0.85 + keyword_score * 0.15
            if semantic_score >= score_threshold:
                ranked.append((row, hybrid_score, semantic_score))
        ranked.sort(key=lambda item: item[1], reverse=True)

        results: list[dict[str, Any]] = []
        used_chars = 0
        for row, hybrid_score, semantic_score in ranked[:top_k]:
            if used_chars + len(row.content) > max_context_chars:
                remaining = max_context_chars - used_chars
                if remaining < 200:
                    break
                content = row.content[:remaining]
            else:
                content = row.content
            used_chars += len(content)
            results.append(
                {
                    "id": str(row.id),
                    "name": str((row.metadata_ or {}).get("filename") or "knowledge"),
                    "content": content,
                    "meta_data": {
                        **(row.metadata_ or {}),
                        "knowledge_base_id": str(knowledge_base_id),
                        "source_id": str(row.source_id),
                        "semantic_score": round(semantic_score, 4),
                        "hybrid_score": round(hybrid_score, 4),
                    },
                }
            )
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
