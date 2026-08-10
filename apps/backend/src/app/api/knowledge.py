from __future__ import annotations

import hashlib
import uuid
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.factory import AgentFactoryService
from app.api.schemas import (
    KnowledgeBaseIn,
    KnowledgeBaseOut,
    KnowledgeBaseUpdateIn,
    KnowledgeIngestGithubIn,
    KnowledgeIngestS3In,
    KnowledgeIngestUrlIn,
    KnowledgeSourceOut,
)
from app.auth.dependencies import require_roles
from app.core.settings import get_settings
from app.db.models import KnowledgeChunk, KnowledgeSource, Role
from app.db.repositories import CredentialRepository, KnowledgeRepository
from app.db.session import tenant_session
from app.knowledge import build_tenant_knowledge_store
from app.knowledge.chunking import chunk_text
from app.knowledge.embeddings import EmbeddingService, EmbeddingUnavailableError
from app.storage.documents import display_name_from_uri, get_document_store
from app.tenancy.context import TenantContext
from app.tenancy.ids import new_id

router = APIRouter(prefix="/admin/knowledge", tags=["admin-knowledge"])
AdminContext = Annotated[
    TenantContext, Depends(require_roles(Role.platform_admin, Role.tenant_admin))
]

ALLOWED_MIME = {"text/plain", "text/markdown", "application/json", "application/pdf"}


def _is_missing_object_error(exc: BaseException) -> bool:
    """True for permanent missing-object errors from local or S3 stores."""
    if isinstance(exc, FileNotFoundError):
        return True
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        code = str(response.get("Error", {}).get("Code") or "")
        if code in {"NoSuchKey", "404", "NotFound", "NoSuchBucket"}:
            return True
    name = type(exc).__name__
    return name in {"NoSuchKey", "NotFound"}


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
        uri=display_name_from_uri(source.uri),
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
    return EmbeddingService(
        api_key=api_key,
        model=settings.embedding_model,
        dimensions=settings.embedding_dimensions,
    )


async def _index_source(
    source: KnowledgeSource,
    data: bytes,
    *,
    content_type: str,
    context: TenantContext,
    session: AsyncSession,
) -> None:
    from sqlalchemy.exc import IntegrityError

    repo = KnowledgeRepository(session, context)
    source.status = "indexing"
    source.error_message = None
    await repo.delete_chunks(source.id)
    try:
        if content_type == "application/pdf":
            raise ValueError("PDF extraction is not installed; upload extracted text or Markdown.")
        text = data.decode("utf-8", errors="strict")
        settings = get_settings()
        chunks = chunk_text(
            text,
            size=settings.knowledge_chunk_size,
            overlap=settings.knowledge_chunk_overlap,
        )
        if not chunks:
            raise ValueError("Document contains no indexable text")
        if len(chunks) > settings.max_knowledge_chunks:
            raise ValueError(f"Document exceeds the {settings.max_knowledge_chunks}-chunk limit")
        # Deduplicate identical chunk bodies (UNIQUE tenant/source/content_hash).
        unique_chunks: list[str] = []
        seen_hashes: set[str] = set()
        for content in chunks:
            digest = hashlib.sha256(content.encode()).hexdigest()
            if digest in seen_hashes:
                continue
            seen_hashes.add(digest)
            unique_chunks.append(content)
        embedder = await _embedder(session, context)
        vectors: list[list[float]] = []
        for start in range(0, len(unique_chunks), 100):
            vectors.extend(await embedder.embed(unique_chunks[start : start + 100]))
        filename = str((source.metadata_ or {}).get("filename") or "knowledge")
        for index, (content, embedding) in enumerate(zip(unique_chunks, vectors, strict=True)):
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
        source.chunk_count = len(unique_chunks)
        source.status = "ready"
        await session.flush()
    except (EmbeddingUnavailableError, UnicodeDecodeError, ValueError, IntegrityError) as exc:
        source.status = "failed"
        source.chunk_count = 0
        source.error_message = str(exc)[:1000]
        await session.flush()
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


@router.delete("/bases/{knowledge_base_id}", status_code=204)
async def delete_base(
    knowledge_base_id: uuid.UUID,
    context: AdminContext,
    session: Annotated[AsyncSession, Depends(tenant_session)],
) -> Response:
    repo = KnowledgeRepository(session, context)
    uris = await repo.delete_base(knowledge_base_id)
    if uris is None:
        raise HTTPException(status_code=404, detail="Knowledge base not found")
    store = get_document_store()
    for uri in uris:
        await store.delete(uri)
    return Response(status_code=204)


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


async def _persist_and_index(
    *,
    knowledge_base_id: uuid.UUID,
    kind: str,
    data: bytes,
    content_type: str,
    filename: str,
    metadata: dict[str, Any],
    context: TenantContext,
    session: AsyncSession,
    store_uri: str | None = None,
) -> KnowledgeSourceOut:
    settings = get_settings()
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(status_code=400, detail="Content exceeds size limit")
    content_hash = hashlib.sha256(data).hexdigest()
    repo = KnowledgeRepository(session, context)
    existing = await repo.get_source_by_hash(knowledge_base_id, content_hash)
    if existing is not None:
        return _source_out(existing)
    if await repo.get_base(knowledge_base_id) is None:
        raise HTTPException(status_code=404, detail="Knowledge base not found")

    safe_name = Path(filename).name.replace("\x00", "_") or "source.txt"
    store = get_document_store()
    uri = store_uri or await store.put(
        tenant_id=str(context.tenant_id), name=safe_name, data=data
    )
    source = await repo.create_source(
        knowledge_base_id=knowledge_base_id,
        kind=kind,
        uri=uri,
        metadata={
            "filename": safe_name,
            "content_type": content_type,
            "bytes": len(data),
            **metadata,
        },
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


def _extract_url_text(url: str, raw: bytes, content_type: str) -> tuple[bytes, str]:
    text = ""
    try:
        import trafilatura

        text = trafilatura.extract(raw.decode("utf-8", errors="ignore"), url=url) or ""
    except Exception:
        text = ""
    if not text.strip():
        if content_type.startswith("text/") or content_type in ALLOWED_MIME:
            text = raw.decode("utf-8", errors="replace")
        else:
            raise HTTPException(
                status_code=400,
                detail="Could not extract indexable text from URL",
            )
    return text.encode("utf-8"), "text/plain"


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
    content_type = file.content_type or "application/octet-stream"
    if content_type not in ALLOWED_MIME:
        raise HTTPException(status_code=400, detail=f"Unsupported content type: {content_type}")
    settings = get_settings()
    data = await file.read(settings.max_upload_bytes + 1)
    return await _persist_and_index(
        knowledge_base_id=knowledge_base_id,
        kind="upload",
        data=data,
        content_type=content_type,
        filename=file.filename or "upload.bin",
        metadata={},
        context=context,
        session=session,
    )


@router.post(
    "/bases/{knowledge_base_id}/ingest/url",
    response_model=KnowledgeSourceOut,
    status_code=201,
)
async def ingest_url(
    knowledge_base_id: uuid.UUID,
    body: KnowledgeIngestUrlIn,
    context: AdminContext,
    session: Annotated[AsyncSession, Depends(tenant_session)],
) -> KnowledgeSourceOut:
    import httpx

    url = body.url.strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="URL must start with http:// or https://")
    settings = get_settings()
    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=30.0,
            headers={"User-Agent": "AtlasKnowledgeIngest/1.0"},
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
            raw = response.content[: settings.max_upload_bytes + 1]
            header_ct = response.headers.get("content-type") or "text/plain"
            content_type = header_ct.split(";")[0].strip()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=400, detail=f"Failed to fetch URL: {exc}") from exc
    data, content_type = _extract_url_text(url, raw, content_type)
    filename = Path(url.rstrip("/").split("/")[-1] or "page").name
    if "." not in filename:
        filename = f"{filename}.txt"
    return await _persist_and_index(
        knowledge_base_id=knowledge_base_id,
        kind="url",
        data=data,
        content_type=content_type,
        filename=filename,
        metadata={"source_url": url},
        context=context,
        session=session,
    )


@router.post(
    "/bases/{knowledge_base_id}/ingest/s3",
    response_model=KnowledgeSourceOut,
    status_code=201,
)
async def ingest_s3(
    knowledge_base_id: uuid.UUID,
    body: KnowledgeIngestS3In,
    context: AdminContext,
    session: Annotated[AsyncSession, Depends(tenant_session)],
) -> KnowledgeSourceOut:
    uri = body.uri.strip()
    store = get_document_store()
    try:
        data = await store.get(uri)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Document URI not found in store") from exc
    except Exception as exc:
        if _is_missing_object_error(exc):
            raise HTTPException(status_code=404, detail="Document URI not found in store") from exc
        raise HTTPException(
            status_code=503,
            detail="Document store temporarily unavailable",
        ) from exc
    name = display_name_from_uri(uri)
    suffix = Path(name).suffix.lower()
    content_type = {
        ".md": "text/markdown",
        ".txt": "text/plain",
        ".json": "application/json",
        ".pdf": "application/pdf",
    }.get(suffix, "text/plain")
    if content_type not in ALLOWED_MIME:
        raise HTTPException(status_code=400, detail=f"Unsupported content type: {content_type}")
    return await _persist_and_index(
        knowledge_base_id=knowledge_base_id,
        kind="s3",
        data=data,
        content_type=content_type,
        filename=name,
        metadata={"source_uri": uri},
        context=context,
        session=session,
        store_uri=uri,
    )


@router.post(
    "/bases/{knowledge_base_id}/ingest/github",
    response_model=KnowledgeSourceOut,
    status_code=201,
)
async def ingest_github(
    knowledge_base_id: uuid.UUID,
    body: KnowledgeIngestGithubIn,
    context: AdminContext,
    session: Annotated[AsyncSession, Depends(tenant_session)],
) -> KnowledgeSourceOut:
    from anyio import to_thread

    repo_name = body.repo.strip().strip("/")
    if repo_name.count("/") != 1:
        raise HTTPException(status_code=400, detail="repo must be owner/name")
    token: str | None = None
    if body.credential_id is not None:
        credential = await CredentialRepository(session, context).get(body.credential_id)
        if credential is None:
            raise HTTPException(status_code=404, detail="Credential not found")
        token = AgentFactoryService._decrypt(  # noqa: SLF001
            credential.encrypted_value,
            credential.key_version,
        )
    else:
        credential = await CredentialRepository(session, context).get_for_provider("github")
        if credential is not None:
            token = AgentFactoryService._decrypt(  # noqa: SLF001
                credential.encrypted_value,
                credential.key_version,
            )

    def _fetch() -> tuple[bytes, str]:
        try:
            from github import Github
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("PyGithub is not installed") from exc
        client = Github(login_or_token=token) if token else Github()
        repo = client.get_repo(repo_name)
        content = repo.get_contents(body.path, ref=body.ref)
        if isinstance(content, list):
            raise ValueError("path must point to a file, not a directory")
        raw = content.decoded_content
        if raw is None:
            raise ValueError("GitHub file has no decodable content")
        return raw, content.name or Path(body.path).name

    try:
        data, filename = await to_thread.run_sync(_fetch)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"GitHub fetch failed: {exc}") from exc

    suffix = Path(filename).suffix.lower()
    content_type = {
        ".md": "text/markdown",
        ".txt": "text/plain",
        ".json": "application/json",
        ".py": "text/plain",
        ".ts": "text/plain",
        ".tsx": "text/plain",
        ".js": "text/plain",
        ".yaml": "text/plain",
        ".yml": "text/plain",
    }.get(suffix, "text/plain")
    return await _persist_and_index(
        knowledge_base_id=knowledge_base_id,
        kind="github",
        data=data,
        content_type=content_type,
        filename=filename,
        metadata={
            "repo": repo_name,
            "path": body.path,
            "ref": body.ref,
            "credential_id": str(body.credential_id) if body.credential_id else None,
        },
        context=context,
        session=session,
    )


@router.post("/sources/{source_id}/reindex", response_model=KnowledgeSourceOut)
async def reindex_source(
    source_id: uuid.UUID,
    context: AdminContext,
    session: Annotated[AsyncSession, Depends(tenant_session)],
) -> KnowledgeSourceOut:
    source = await KnowledgeRepository(session, context).get_source(source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Knowledge source not found")
    store = get_document_store()
    try:
        data = await store.get(source.uri)
    except FileNotFoundError:
        source.status = "failed"
        source.error_message = "Uploaded source file is no longer available"
        return _source_out(source)
    except Exception as exc:
        # Transient S3/network errors must not permanently mark the source failed.
        if _is_missing_object_error(exc):
            source.status = "failed"
            source.error_message = "Uploaded source file is no longer available"
            return _source_out(source)
        raise HTTPException(
            status_code=503,
            detail="Document store temporarily unavailable; retry reindex shortly",
        ) from exc
    await _index_source(
        source,
        data,
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
    uri = source.uri
    await repo.delete_source(source_id)
    await get_document_store().delete(uri)
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
    results = await build_tenant_knowledge_store(session, context, settings=settings).search(
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
            score=float(
                item["meta_data"].get("rerank_score")
                or item["meta_data"]["hybrid_score"]
            ),
            source_id=str(item["meta_data"]["source_id"]),
            metadata=item["meta_data"],
        )
        for item in results
    ]
