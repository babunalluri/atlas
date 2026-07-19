"""Bounded embedding generation for tenant knowledge."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence

from openai import AsyncOpenAI

EmbedCallable = Callable[[Sequence[str]], Awaitable[list[list[float]]]]


class EmbeddingUnavailableError(RuntimeError):
    pass


class EmbeddingService:
    def __init__(
        self,
        *,
        api_key: str | None,
        model: str = "text-embedding-3-small",
        embed_callable: EmbedCallable | None = None,
    ) -> None:
        self.model = model
        self._embed_callable = embed_callable
        self._client = AsyncOpenAI(api_key=api_key) if api_key else None

    @property
    def available(self) -> bool:
        return self._embed_callable is not None or self._client is not None

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        if len(texts) > 100:
            raise ValueError("Embedding batch exceeds 100 chunks")
        if any(len(text) > 20_000 for text in texts):
            raise ValueError("Embedding input exceeds bounded chunk size")
        if self._embed_callable is not None:
            vectors = await self._embed_callable(texts)
        elif self._client is not None:
            response = await self._client.embeddings.create(model=self.model, input=list(texts))
            vectors = [item.embedding for item in response.data]
        else:
            raise EmbeddingUnavailableError(
                "No OpenAI embedding credential is configured. Add a tenant OpenAI "
                "credential or set OPENAI_API_KEY, then reindex."
            )
        if len(vectors) != len(texts) or any(len(vector) != 1536 for vector in vectors):
            raise ValueError("Embedding provider returned an unexpected vector dimension")
        return vectors
