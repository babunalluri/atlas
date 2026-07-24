from __future__ import annotations

import hashlib
import uuid
from pathlib import Path
from typing import Annotated, Any

from anyio import to_thread
from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.factory import AgentFactoryService
from app.api.schemas import (
    KnowledgeBaseIn,
    KnowledgeBaseOut,
    KnowledgeBaseUpdateIn,
    KnowledgeSourceOut,
)
from app.auth.dependencies import require_roles
from app.core.settings import get_settings
from app.db.models import KnowledgeChunk, KnowledgeSource, Role
from app.db.repositories import CredentialRepository, KnowledgeRepository
from app.db.session import tenant_session
from app.knowledge.embeddings import EmbeddingService, EmbeddingUnavailableError
from app.knowledge.store import TenantKnowledgeStore
from app.tenancy.context import TenantContext
from app.tenancy.ids import new_id

router = APIRouter(prefix="/admin/knowledge", tags=["admin-knowledge"])
AdminContext = Annotated[
    TenantContext, Depends(require_roles(Role.platform_admin, Role.tenant_admin))
]

ALLOWED_MIME = {"text/plain", "text/markdown", "application/json", "application/pdf"}


class KnowledgeSearchIn(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    top_k: int = Field(default=6, ge=1, le=20)
    score_threshold: float | None = Field(default=None, ge=0, le=1)


class KnowledgeSearchOut(BaseModel):
    id: str
    content: str
    score: float
    source_id: str
    metadata: dict[str, Any]


def _source_out(source: KnowledgeSource) -> KnowledgeSourceOut:
    metadata = dict(source.metadata_ or {})
    if source.error_message:
        metadata["error_message"] = source.error_message
    metadata["chunk_count"] = source.chunk_count
    metadata["embedding_model"] = source.embedding_model
    return KnowledgeSourceOut(
        id=source.id,
        knowledge_base_id=source.knowledge_base_id,
        kind=source.kind,
        uri=Path(source.uri).name,
        status=source.status,
        metadata=metadata,
        created_at=source.created_at,
        updated_at=source.updated_at,
    )


async def _embedder(session: AsyncSession, context: TenantContext) -> EmbeddingService:
    settings = get_settings()
    api_key = settings.openai_api_key.get_secret_value() or None
    credential = await CredentialRepository(session, context).get_for_provider("openai")
    if credential is not None:
        api_key = AgentFactoryService._decrypt(  # noqa: SLF001
            credential.encrypted_value,
            credential.key_version,
        )
    return EmbeddingService(api_key=api_key, model=settings.embedding_model)


def _chunks(text: str, *, size: int = 1200, overlap: int = 150) -> list[str]:
    normalized = text.replace("\x00", "").strip()
    if not normalized:
        return []
    chunks: list[str] = []
    start = 0
    while start < len(normalized):
        end = min(start + size, len(normalized))
        chunk = normalized[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end == len(normalized):
            break
        start = max(end - overlap, start + 1)
    return chunks


async def _index_source(
    source: KnowledgeSource,
    data: bytes,
    *,
    content_type: str,
    context: TenantContext,
    session: AsyncSession,
) -> None:
    repo = KnowledgeRepository(session, context)
    source.status = "indexing"
    source.error_message = None
    await repo.delete_chunks(source.id)
    try:
        if content_type == "application/pdf":
            raise ValueError("PDF extraction is not installed; upload extracted text or Markdown.")
        text = data.decode("utf-8", errors="strict")
        chunks = _chunks(text)
        settings = get_settings()
        if not chunks:
            raise ValueError("Document contains no indexable text")
        if len(chunks) > settings.max_knowledge_chunks:
            raise ValueError(f"Document exceeds the {settings.max_knowledge_chunks}-chunk limit")
        embedder = await _embedder(session, context)
        vectors: list[list[float]] = []
        for start in range(0, len(chunks), 100):
            vectors.extend(await embedder.embed(chunks[start : start + 100]))
        filename = str((source.metadata_ or {}).get("filename") or "knowledge")
        for index, (content, embedding) in enumerate(zip(chunks, vectors, strict=True)):
            session.add(
                KnowledgeChunk(
                    id=new_id(),
                    tenant_id=context.tenant_id,
                    knowledge_base_id=source.knowledge_base_id,
                    source_id=source.id,
                    content=content,
                    embedding=embedding,
                    content_hash=hashlib.sha256(content.encode()).hexdigest(),
                    token_count=max(1, len(content) // 4),
                    metadata_={"chunk_index": index, "filename": filename},
                )
            )
        source.embedding_model = embedder.model
        source.chunk_count = len(chunks)
        source.status = "ready"
    except (EmbeddingUnavailableError, UnicodeDecodeError, ValueError) as exc:
        source.status = "failed"
        source.chunk_count = 0
        source.error_message = str(exc)[:1000]
    except Exception:
        source.status = "failed"
        source.chunk_count = 0
        source.error_message = "Embedding provider failed; verify credentials and reindex."
    await session.flush()


@router.get("/bases", response_model=list[KnowledgeBaseOut])
async def list_bases(
    context: AdminContext,
    session: Annotated[AsyncSession, Depends(tenant_session)],
) -> list[KnowledgeBaseOut]:
    bases = await KnowledgeRepository(session, context).list_bases()
    return [KnowledgeBaseOut(id=b.id, name=b.name, config=b.config) for b in bases]


@router.get("/sources", response_model=list[KnowledgeSourceOut])
async def list_all_sources(
    context: AdminContext,
    session: Annotated[AsyncSession, Depends(tenant_session)],
) -> list[KnowledgeSourceOut]:
    sources = await KnowledgeRepository(session, context).list_all_sources()
    return [_source_out(source) for source in sources]


@router.post("/bases", response_model=KnowledgeBaseOut, status_code=201)
async def create_base(
    body: KnowledgeBaseIn,
    context: AdminContext,
    session: Annotated[AsyncSession, Depends(tenant_session)],
) -> KnowledgeBaseOut:
    base = await KnowledgeRepository(session, context).create_base(
        name=body.name, config=body.config
    )
    return KnowledgeBaseOut(id=base.id, name=base.name, config=base.config)


@router.patch("/bases/{knowledge_base_id}", response_model=KnowledgeBaseOut)
async def update_base(
    knowledge_base_id: uuid.UUID,
    body: KnowledgeBaseUpdateIn,
    context: AdminContext,
    session: Annotated[AsyncSession, Depends(tenant_session)],
) -> KnowledgeBaseOut:
    base = await KnowledgeRepository(session, context).update_base(
        knowledge_base_id,
        name=body.name,
        config=body.config,
    )
    if base is None:
        raise HTTPException(status_code=404, detail="Knowledge base not found")
    return KnowledgeBaseOut(id=base.id, name=base.name, config=base.config)


@router.get("/bases/{knowledge_base_id}/sources", response_model=list[KnowledgeSourceOut])
async def list_sources(
    knowledge_base_id: uuid.UUID,
    context: AdminContext,
    session: Annotated[AsyncSession, Depends(tenant_session)],
) -> list[KnowledgeSourceOut]:
    repo = KnowledgeRepository(session, context)
    if await repo.get_base(knowledge_base_id) is None:
        raise HTTPException(status_code=404, detail="Knowledge base not found")
    return [_source_out(source) for source in await repo.list_sources(knowledge_base_id)]


@router.post(
    "/bases/{knowledge_base_id}/upload",
    response_model=KnowledgeSourceOut,
    status_code=201,
)
async def upload_source(
    knowledge_base_id: uuid.UUID,
    context: AdminContext,
    session: Annotated[AsyncSession, Depends(tenant_session)],
    file: Annotated[UploadFile, File()],
) -> KnowledgeSourceOut:
    settings = get_settings()
    content_type = file.content_type or "application/octet-stream"
    if content_type not in ALLOWED_MIME:
        raise HTTPException(status_code=400, detail=f"Unsupported content type: {content_type}")
    data = await file.read(settings.max_upload_bytes + 1)
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(status_code=400, detail="Upload exceeds size limit")
    content_hash = hashlib.sha256(data).hexdigest()
    repo = KnowledgeRepository(session, context)
    existing = await repo.get_source_by_hash(knowledge_base_id, content_hash)
    if existing is not None:
        return _source_out(existing)
    if await repo.get_base(knowledge_base_id) is None:
        raise HTTPException(status_code=404, detail="Knowledge base not found")

    upload_root = Path(settings.document_upload_dir) / str(context.tenant_id)
    upload_root.mkdir(parents=True, exist_ok=True)
    safe_name = Path(file.filename or "upload.bin").name.replace("\x00", "_")
    path = upload_root / f"{new_id()}-{safe_name}"
    path.write_bytes(data)
    source = await repo.create_source(
        knowledge_base_id=knowledge_base_id,
        kind="upload",
        uri=str(path),
        metadata={"filename": safe_name, "content_type": content_type, "bytes": len(data)},
    )
    source.content_hash = content_hash
    await _index_source(
        source,
        data,
        content_type=content_type,
        context=context,
        session=session,
    )
    return _source_out(source)


@router.post("/sources/{source_id}/reindex", response_model=KnowledgeSourceOut)
async def reindex_source(
    source_id: uuid.UUID,
    context: AdminContext,
    session: Annotated[AsyncSession, Depends(tenant_session)],
) -> KnowledgeSourceOut:
    source = await KnowledgeRepository(session, context).get_source(source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Knowledge source not found")
    path = Path(source.uri)
    if not await to_thread.run_sync(path.is_file):
        source.status = "failed"
        source.error_message = "Uploaded source file is no longer available"
        return _source_out(source)
    await _index_source(
        source,
        await to_thread.run_sync(path.read_bytes),
        content_type=str((source.metadata_ or {}).get("content_type") or "text/plain"),
        context=context,
        session=session,
    )
    return _source_out(source)


@router.delete("/sources/{source_id}", status_code=204)
async def delete_source(
    source_id: uuid.UUID,
    context: AdminContext,
    session: Annotated[AsyncSession, Depends(tenant_session)],
) -> Response:
    repo = KnowledgeRepository(session, context)
    source = await repo.get_source(source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Knowledge source not found")
    path = Path(source.uri)
    await repo.delete_source(source_id)
    try:
        await to_thread.run_sync(lambda: path.unlink(missing_ok=True))
    except OSError:
        pass
    return Response(status_code=204)


@router.post("/bases/{knowledge_base_id}/search", response_model=list[KnowledgeSearchOut])
async def test_search(
    knowledge_base_id: uuid.UUID,
    body: KnowledgeSearchIn,
    context: AdminContext,
    session: Annotated[AsyncSession, Depends(tenant_session)],
) -> list[KnowledgeSearchOut]:
    repo = KnowledgeRepository(session, context)
    if await repo.get_base(knowledge_base_id) is None:
        raise HTTPException(status_code=404, detail="Knowledge base not found")
    try:
        vector = (await (await _embedder(session, context)).embed([body.query]))[0]
    except EmbeddingUnavailableError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    settings = get_settings()
    results = await TenantKnowledgeStore(session, context).search(
        knowledge_base_id,
        body.query,
        vector,
        top_k=body.top_k,
        score_threshold=body.score_threshold
        if body.score_threshold is not None
        else settings.knowledge_score_threshold,
        max_context_chars=settings.max_knowledge_context_chars,
    )
    return [
        KnowledgeSearchOut(
            id=item["id"],
            content=item["content"],
            score=float(item["meta_data"]["hybrid_score"]),
            source_id=str(item["meta_data"]["source_id"]),
            metadata=item["meta_data"],
        )
        for item in results
    ]
