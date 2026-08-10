"""Knowledge retrieval quality: chunking, hybrid RRF, local rerank."""

from __future__ import annotations

import uuid

import pytest

from app.db.models import KnowledgeChunk
from app.db.repositories import KnowledgeRepository
from app.knowledge.chunking import chunk_text
from app.knowledge.embeddings import EmbeddingService
from app.knowledge.rerank import LocalLexicalReranker, build_reranker
from app.knowledge.store import TenantKnowledgeStore


def test_chunk_text_respects_markdown_headings():
    text = """# Billing

Refunds are available for 30 days after purchase.

## Kitchen

The office kitchen closes at five.
"""
    chunks = chunk_text(text, size=200, overlap=20)
    assert len(chunks) >= 2
    assert any(chunk.startswith("# Billing") for chunk in chunks)
    assert any("Kitchen" in chunk for chunk in chunks)


def test_chunk_text_packs_short_paragraphs():
    text = "Alpha paragraph.\n\nBeta paragraph.\n\nGamma paragraph."
    chunks = chunk_text(text, size=80, overlap=10)
    assert chunks
    assert all(len(chunk) <= 80 or "\n\n" not in chunk for chunk in chunks)


def test_build_reranker_modes():
    assert build_reranker(mode="off") is None
    assert isinstance(build_reranker(mode="local"), LocalLexicalReranker)
    assert isinstance(
        build_reranker(mode="cohere", cohere_api_key=None), LocalLexicalReranker
    )


@pytest.mark.asyncio
async def test_local_reranker_promotes_lexical_match():
    reranker = LocalLexicalReranker(lexical_weight=0.5)
    docs = [
        {
            "id": "1",
            "content": "Unrelated travel policy details.",
            "meta_data": {"hybrid_score": 0.55, "source_id": "s1"},
        },
        {
            "id": "2",
            "content": "Billing refund policy for enterprise customers.",
            "meta_data": {"hybrid_score": 0.4, "source_id": "s2"},
        },
    ]
    ranked = await reranker.rerank("billing refund policy", docs, top_n=2)
    assert ranked[0]["id"] == "2"


@pytest.mark.asyncio
async def test_hybrid_rrf_prefers_keyword_when_vectors_tie(session, tenant_a):
    """Exact terms should surface even when semantic scores are similar."""

    async def fake_embed(texts):
        # Force near-ties in embedding space so keyword RRF can decide.
        return [[0.5, 0.5, *([0.0] * 1534)] for _ in texts]

    embedder = EmbeddingService(api_key=None, embed_callable=fake_embed)
    query_vector = (await embedder.embed(["kitchen closes"]))[0]

    session.info["tenant_id"] = tenant_a.tenant_id
    repo = KnowledgeRepository(session, tenant_a)
    base = await repo.create_base(name="Hybrid")
    source = await repo.create_source(
        knowledge_base_id=base.id,
        kind="test",
        uri="hybrid.txt",
    )
    session.add_all(
        [
            KnowledgeChunk(
                id=uuid.uuid4(),
                tenant_id=tenant_a.tenant_id,
                knowledge_base_id=base.id,
                source_id=source.id,
                content="Billing refunds are available for 30 days.",
                embedding=[0.5, 0.5, *([0.0] * 1534)],
                content_hash="d" * 64,
                metadata_={"filename": "billing.txt"},
            ),
            KnowledgeChunk(
                id=uuid.uuid4(),
                tenant_id=tenant_a.tenant_id,
                knowledge_base_id=base.id,
                source_id=source.id,
                content="The office kitchen closes at five.",
                embedding=[0.5, 0.5, *([0.0] * 1534)],
                content_hash="e" * 64,
                metadata_={"filename": "office.txt"},
            ),
        ]
    )
    await session.flush()

    store = TenantKnowledgeStore(
        session,
        tenant_a,
        reranker=LocalLexicalReranker(),
        rrf_k=60,
    )
    results = await store.search(
        base.id,
        "kitchen closes",
        query_vector,
        score_threshold=0.0,
    )
    assert results
    assert "kitchen" in results[0]["content"].lower()
    assert "keyword_score" in results[0]["meta_data"]
    assert "hybrid_score" in results[0]["meta_data"]
