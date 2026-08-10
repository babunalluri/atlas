"""Public Resend inbound email channel for published teams and workflows."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from app.agent_runtime.factory import (
    AgentFactoryService,
    TeamFactoryService,
    TeamRuntimeRequest,
    WorkflowFactoryService,
    WorkflowRuntimeRequest,
)
from app.agent_runtime.persistence import runtime_session_id, runtime_user_id
from app.api.public_chat import _public_run_context, _rate_limit_public_chat
from app.core.logging import get_logger
from app.core.rate_limit import limiter
from app.core.redis_client import get_redis
from app.core.settings import get_settings
from app.db.repositories import (
    CredentialRepository,
    EndUserSessionBindRepository,
    TeamRepository,
    WorkflowRepository,
)
from app.email.addressing import (
    build_inbound_address,
    normalize_email,
    parse_inbound_address,
    strip_quoted_reply,
)
from app.email.resend import (
    ResendError,
    fetch_received_email,
    send_resend_email,
    verify_svix_signature,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/public", tags=["public-email"])

_IDEMPOTENCY_TTL = 60 * 60 * 24 * 7
_MEMORY_PROCESSED: set[str] = set()


def guest_user_id_for_email(email: str) -> str:
    digest = hashlib.sha256(normalize_email(email).encode()).hexdigest()[:32]
    return f"guest:mail_{digest}"


def session_id_for_email(*, sender: str, inbox: str) -> str:
    digest = hashlib.sha256(
        f"{normalize_email(sender)}|{normalize_email(inbox)}".encode()
    ).hexdigest()[:40]
    return f"eml_{digest}"


def reply_subject(subject: str | None) -> str:
    raw = (subject or "").strip() or "Your request"
    if re.match(r"(?i)^re\s*:", raw):
        return raw
    return f"Re: {raw}"


async def _claim_email_id(email_id: str) -> bool:
    """Return True if this email_id should be processed (first sight)."""
    key = f"public-email:processed:{email_id}"
    client = await get_redis()
    if client is not None:
        try:
            created = await client.set(key, "1", nx=True, ex=_IDEMPOTENCY_TTL)
            return bool(created)
        except Exception:
            logger.warning("public_email_idempotency_redis_failed")
    if email_id in _MEMORY_PROCESSED:
        return False
    _MEMORY_PROCESSED.add(email_id)
    return True


async def _resolve_resend_api_key(session: Any, context: Any) -> str:
    settings = get_settings()
    credential = await CredentialRepository(session, context).get_for_provider("resend")
    if credential is not None:
        return AgentFactoryService._decrypt(  # noqa: SLF001
            credential.encrypted_value, credential.key_version
        )
    fallback = settings.resend_api_key.get_secret_value().strip()
    if fallback:
        return fallback
    raise HTTPException(
        status_code=503,
        detail="Resend is not configured for this workspace (add a resend credential)",
    )


def _from_address(tenant: Any, inbox: str) -> str:
    branding = tenant.branding if isinstance(getattr(tenant, "branding", None), dict) else {}
    configured = (branding.get("emailFrom") or branding.get("email_from") or "").strip()
    if configured:
        return configured
    settings = get_settings()
    domain = (settings.email_inbound_domain or "").strip() or inbox.split("@")[-1]
    return f"noreply@{domain}"


def _extract_text(payload: dict[str, Any]) -> str:
    for key in ("text", "text_body", "plain_text", "body"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    html = payload.get("html") or payload.get("html_body")
    if isinstance(html, str) and html.strip():
        return re.sub(r"<[^>]+>", " ", html)
    return ""


def _pick_to_addresses(data: dict[str, Any]) -> list[str]:
    raw = data.get("to") or []
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, list):
        return [str(item) for item in raw]
    return []


async def _collect_run_text(
    runtime: Any,
    message: str,
    *,
    user_id: str,
    session_id: str,
    context: Any,
    trace_id: uuid.UUID | None,
    external_session_id: str,
    wall_seconds: int,
) -> tuple[str, bool]:
    """Drain streaming events into final text. Returns (text, paused_for_approval)."""
    from app.agent_runtime.agent_os import (
        _fail_runtime_trace,
        _persist_runtime_event,
        _sse_from_agent,
    )

    chunks: list[str] = []
    paused = False
    last_content = ""
    try:
        async for raw in _sse_from_agent(
            runtime,
            message,
            user_id=user_id,
            session_id=session_id,
            session_state=getattr(runtime, "_saas_session_state", None),
            wall_seconds=wall_seconds,
            event_handler=lambda payload: _persist_runtime_event(
                payload,
                context=context,
                trace_id=trace_id,
                external_session_id=external_session_id,
                initial_title=message[:255],
            ),
        ):
            line = raw.decode("utf-8", errors="replace").strip()
            if not line.startswith("data:"):
                continue
            try:
                payload = json.loads(line[5:].strip())
            except json.JSONDecodeError:
                continue
            event = payload.get("event")
            content = payload.get("content")
            if isinstance(content, str) and content:
                if event in {"RunContent", "TeamRunContent", "WorkflowContent"}:
                    chunks.append(content)
                last_content = content
            if event == "RunPaused" or payload.get("requires_confirmation"):
                paused = True
            if event == "RunError":
                err = payload.get("error") or last_content or "Run failed"
                raise RuntimeError(str(err))
    except Exception:
        if trace_id is not None:
            await _fail_runtime_trace(context, trace_id, "Public email run failed")
        raise

    text = "".join(chunks).strip() or last_content.strip()
    return text, paused


@router.post("/webhooks/resend")
async def resend_inbound_webhook(request: Request) -> JSONResponse:
    settings = get_settings()
    body = await request.body()
    header_map = {k.lower(): v for k, v in request.headers.items()}
    try:
        verify_svix_signature(
            body=body,
            headers={
                "svix-id": header_map.get("svix-id", ""),
                "svix-timestamp": header_map.get("svix-timestamp", ""),
                "svix-signature": header_map.get("svix-signature", ""),
            },
            secret=settings.resend_webhook_secret.get_secret_value(),
        )
    except ResendError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    try:
        envelope = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON body") from exc

    event_type = str(envelope.get("type") or "")
    data = envelope.get("data") if isinstance(envelope.get("data"), dict) else {}
    if event_type and event_type not in {"email.received", "email.received.completed"}:
        return JSONResponse({"ok": True, "ignored": event_type})

    email_id = str(data.get("email_id") or data.get("id") or "").strip()
    if not email_id:
        raise HTTPException(status_code=400, detail="Missing email_id")

    if not await _claim_email_id(email_id):
        return JSONResponse({"ok": True, "duplicate": True})

    inbound_domain = settings.email_inbound_domain.strip().lower()
    if not inbound_domain:
        raise HTTPException(status_code=503, detail="EMAIL_INBOUND_DOMAIN is not configured")

    to_list = _pick_to_addresses(data)
    parsed = None
    for candidate in to_list:
        parsed = parse_inbound_address(candidate, inbound_domain=inbound_domain)
        if parsed is not None:
            break
    if parsed is None:
        logger.info("public_email_unmatched_address", to=to_list)
        return JSONResponse({"ok": True, "ignored": "unmatched_address"})

    sender = normalize_email(str(data.get("from") or ""))
    if not sender:
        raise HTTPException(status_code=400, detail="Missing from address")

    host = request.client.host if request.client else "anon"
    guest_user_id = guest_user_id_for_email(sender)
    session_id = session_id_for_email(sender=sender, inbox=parsed.raw)
    acquired = False

    async with _public_run_context(parsed.tenant_slug, guest_user_id=guest_user_id) as (
        session,
        tenant,
        context,
    ):
        try:
            await _rate_limit_public_chat(
                tenant_id=tenant.id,
                guest_user_id=guest_user_id,
                client_host=host,
            )
            acquired = True

            api_key = await _resolve_resend_api_key(session, context)

            subject = str(data.get("subject") or "").strip()
            message_id = str(data.get("message_id") or "").strip() or None
            text_body = _extract_text(data)
            if not text_body.strip():
                received = await fetch_received_email(api_key, email_id)
                if not subject:
                    subject = str(received.get("subject") or "").strip()
                if not message_id:
                    message_id = str(received.get("message_id") or "").strip() or None
                text_body = _extract_text(received)

            message = strip_quoted_reply(text_body)
            if not message:
                await send_resend_email(
                    api_key,
                    from_address=_from_address(tenant, parsed.raw),
                    to_address=sender,
                    subject=reply_subject(subject),
                    text="We received your email but could not find any text to process.",
                    in_reply_to=message_id,
                    references=message_id,
                )
                return JSONResponse({"ok": True, "empty_body": True})

            from app.agent_runtime.agent_os import _start_runtime_trace
            from app.identity.service import IdentityService
            from app.identity.tools import attach_identity_tools
            from app.tenancy.context import TenantContext, set_tenant_context

            end_user = await IdentityService(session, context).ensure_email_identity(
                sender
            )
            await EndUserSessionBindRepository(session, context).upsert(
                external_session_id=session_id,
                guest_user_id=guest_user_id,
                end_user_id=end_user.id,
            )
            context = TenantContext(
                tenant_id=context.tenant_id,
                user_id=context.user_id,
                role=context.role,
                auth_org_id=context.auth_org_id,
                scopes=context.scopes,
                principal_type=context.principal_type,
                verified_end_user_id=end_user.id,
                verified_email=end_user.email,
            )
            set_tenant_context(context)

            if parsed.kind == "team":
                repo = TeamRepository(session, context)
                config = await repo.get_config_by_slug(parsed.resource_slug)
                if config is None or config.published_version_id is None:
                    raise HTTPException(status_code=404, detail="Published team not found")
                runtime = await TeamFactoryService(
                    AgentFactoryService(session, context)
                ).create(
                    TeamRuntimeRequest(
                        version_id=config.published_version_id,
                        session_id=session_id,
                        preview=False,
                    )
                )
                metadata = dict(getattr(runtime, "_saas_metadata", {}) or {})
                metadata["public_email"] = True
                target_id = uuid.UUID(str(metadata["team_id"]))
                version_id = config.published_version_id
                trace_name = "Public team email"
            else:
                repo = WorkflowRepository(session, context)
                config = await repo.get_config_by_slug(parsed.resource_slug)
                if config is None or config.published_version_id is None:
                    raise HTTPException(
                        status_code=404, detail="Published workflow not found"
                    )
                runtime = await WorkflowFactoryService(
                    AgentFactoryService(session, context)
                ).create(
                    WorkflowRuntimeRequest(
                        version_id=config.published_version_id,
                        session_id=session_id,
                        preview=False,
                    )
                )
                metadata = dict(getattr(runtime, "_saas_metadata", {}) or {})
                metadata["public_email"] = True
                target_id = uuid.UUID(str(metadata["workflow_id"]))
                version_id = config.published_version_id
                trace_name = "Public workflow email"

            attach_identity_tools(runtime, session, context)
            metadata["verified_end_user_id"] = str(end_user.id)
            metadata["verified_email"] = end_user.email
            await session.commit()

            durable_user_id = runtime_user_id(context)
            durable_session_id = runtime_session_id(context, session_id)
            trace_id = await _start_runtime_trace(
                context=context,
                external_session_id=session_id,
                target_id=target_id,
                version_id=version_id,
                name=trace_name,
                message=message,
                metadata=metadata,
            )

            try:
                reply_text, paused = await _collect_run_text(
                    runtime,
                    message,
                    user_id=durable_user_id,
                    session_id=durable_session_id,
                    context=context,
                    trace_id=trace_id,
                    external_session_id=session_id,
                    wall_seconds=settings.public_email_run_wall_seconds,
                )
            except Exception as exc:
                logger.exception("public_email_run_failed", error=str(exc))
                reply_text = (
                    "Sorry — we could not complete your request. "
                    "Please try again later or use the chat link."
                )
                paused = False

            if paused:
                approvals_url = (
                    f"{settings.app_public_url.rstrip('/')}/admin/approvals"
                )
                reply_text = (
                    "Your request is waiting for staff approval before we can continue.\n"
                    f"An operator will review it here: {approvals_url}"
                )
            elif not reply_text.strip():
                reply_text = "Your request completed, but there was no text response."

            await send_resend_email(
                api_key,
                from_address=_from_address(tenant, parsed.raw),
                to_address=sender,
                subject=reply_subject(subject),
                text=reply_text,
                in_reply_to=message_id,
                references=message_id,
            )
            return JSONResponse(
                {
                    "ok": True,
                    "kind": parsed.kind,
                    "tenant": parsed.tenant_slug,
                    "resource": parsed.resource_slug,
                    "paused": paused,
                }
            )
        finally:
            if acquired:
                await limiter.async_release(f"public-chat:concurrency:{tenant.id}")


@router.get("/email/address")
async def describe_email_address(
    tenant_slug: str,
    kind: str,
    resource_slug: str,
) -> dict[str, str | None]:
    """Public helper used by Share UI to show the inbound address shape."""
    settings = get_settings()
    if kind not in {"team", "workflow"}:
        raise HTTPException(status_code=400, detail="kind must be team or workflow")
    address = build_inbound_address(
        kind=kind,
        tenant_slug=tenant_slug,
        resource_slug=resource_slug,
        inbound_domain=settings.email_inbound_domain,
    )
    return {
        "address": address,
        "inbound_domain": settings.email_inbound_domain.strip() or None,
    }
