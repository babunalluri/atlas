"""Bounded embedding generation for tenant knowledge."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence

from openai import AsyncOpenAI

from app.core.settings import get_settings

EmbedCallable = Callable[[Sequence[str]], Awaitable[list[list[float]]]]

# Known OpenAI embedding widths. DB column must match settings.embedding_dimensions.
KNOWN_EMBEDDING_DIMENSIONS: dict[str, int] = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
}


class EmbeddingUnavailableError(RuntimeError):
    pass


class EmbeddingService:
    def __init__(
        self,
        *,
        api_key: str | None,
        model: str = "text-embedding-3-small",
        dimensions: int | None = None,
        embed_callable: EmbedCallable | None = None,
    ) -> None:
        self.model = model
        self.dimensions = dimensions or get_settings().embedding_dimensions
        known = KNOWN_EMBEDDING_DIMENSIONS.get(model)
        if known is not None and known != self.dimensions:
            raise ValueError(
                f"EMBEDDING_MODEL {model!r} produces {known}-d vectors but "
                f"EMBEDDING_DIMENSIONS/DB column is {self.dimensions}. "
                "Align the model with the pgvector column (or migrate Vector width)."
            )
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
            kwargs: dict[str, object] = {"model": self.model, "input": list(texts)}
            # text-embedding-3-* accept dimensions; ada-002 does not.
            if self.model.startswith("text-embedding-3-"):
                kwargs["dimensions"] = self.dimensions
            response = await self._client.embeddings.create(**kwargs)
            vectors = [item.embedding for item in response.data]
        else:
            raise EmbeddingUnavailableError(
                "No OpenAI embedding credential is configured. Add a tenant OpenAI "
                "credential or set OPENAI_API_KEY, then reindex."
            )
        if len(vectors) != len(texts) or any(
            len(vector) != self.dimensions for vector in vectors
        ):
            raise ValueError(
                f"Embedding provider returned unexpected vector dimension "
                f"(expected {self.dimensions})"
            )
        return vectors
