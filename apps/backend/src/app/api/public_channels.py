"""Public Slack / Telegram / WhatsApp webhooks for published teams and workflows."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response

from app.agent_runtime.factory import (
    AgentFactoryService,
    TeamFactoryService,
    TeamRuntimeRequest,
    WorkflowFactoryService,
    WorkflowRuntimeRequest,
)
from app.agent_runtime.persistence import runtime_session_id, runtime_user_id
from app.api.public_chat import _public_run_context, _rate_limit_public_chat
from app.api.public_email import _collect_run_text
from app.core.logging import get_logger
from app.core.rate_limit import limiter
from app.core.settings import get_settings
from app.db.models import Role
from app.db.repositories import (
    ChannelBindingRepository,
    CredentialRepository,
    TeamRepository,
    TenantAdminRepository,
    WorkflowRepository,
)
from app.db.session import SessionFactory, apply_tenant_guc
from app.tenancy.context import TenantContext, set_tenant_context

logger = get_logger(__name__)

router = APIRouter(prefix="/public", tags=["public-channels"])

BindingIdQuery = Annotated[uuid.UUID | None, Query()]
TenantSlugQuery = Annotated[str, Query(min_length=1)]


def guest_user_id_for_channel(*, provider: str, external_user: str) -> str:
    digest = hashlib.sha256(f"{provider}|{external_user}".encode()).hexdigest()[:32]
    return f"guest:{provider}_{digest}"


def session_id_for_channel(*, provider: str, external_user: str, thread: str) -> str:
    digest = hashlib.sha256(
        f"{provider}|{external_user}|{thread}".encode()
    ).hexdigest()[:40]
    return f"{provider[:3]}_{digest}"


def _match_binding(bindings: list[Any], payload: dict[str, Any]) -> Any | None:
    if not bindings:
        return None
    if len(bindings) == 1:
        return bindings[0]
    for binding in bindings:
        cfg = dict(binding.external_config or {})
        for key in (
            "team_id",
            "channel_id",
            "bot_username",
            "phone_number_id",
            "app_id",
            "webhook_token",
        ):
            expected = str(cfg.get(key) or "").strip()
            if not expected:
                continue
            actual = payload.get(key)
            if actual is None and isinstance(payload.get("event"), dict):
                actual = payload["event"].get(key)
            if actual is None and isinstance(payload.get("entry"), list) and payload["entry"]:
                changes = payload["entry"][0].get("changes") or []
                if changes and isinstance(changes[0].get("value"), dict):
                    actual = changes[0]["value"].get("metadata", {}).get(key)
            if str(actual or "").strip() == expected:
                return binding
    return bindings[0]


async def _load_binding(
    *,
    provider: str,
    tenant_slug: str,
    binding_id: uuid.UUID | None,
    payload: dict[str, Any],
) -> tuple[Any, Any, TenantContext]:
    async with SessionFactory() as session:
        tenant = await TenantAdminRepository(session).get_by_slug(tenant_slug)
        if tenant is None or not tenant.is_active:
            raise HTTPException(status_code=404, detail="Tenant not found")
        await apply_tenant_guc(session, tenant.id)
        context = TenantContext(
            tenant_id=tenant.id,
            user_id=f"guest:{provider}",
            role=Role.end_user,
            auth_org_id=tenant.auth_org_id,
            principal_type="guest",
        )
        set_tenant_context(context)
        repo = ChannelBindingRepository(session, context)
        if binding_id is not None:
            binding = await repo.get(binding_id)
            if (
                binding is None
                or binding.provider != provider
                or not binding.active
            ):
                raise HTTPException(status_code=404, detail="Channel binding not found")
        else:
            bindings = list(await repo.list_by_provider(provider, active_only=True))
            binding = _match_binding(bindings, payload)
            if binding is None:
                raise HTTPException(status_code=404, detail="No active channel binding")
        # Detach for use outside this session
        await session.refresh(binding)
        await session.commit()
        return tenant, binding, context


async def _run_binding(
    *,
    tenant_slug: str,
    binding: Any,
    message: str,
    guest_user_id: str,
    session_id: str,
    client_host: str,
) -> tuple[str, bool]:
    settings = get_settings()
    acquired = False
    async with _public_run_context(tenant_slug, guest_user_id=guest_user_id) as (
        session,
        tenant,
        context,
    ):
        try:
            await _rate_limit_public_chat(
                tenant_id=tenant.id,
                guest_user_id=guest_user_id,
                client_host=client_host,
            )
            acquired = True

            from app.agent_runtime.agent_os import _start_runtime_trace

            if binding.target_type == "team":
                repo = TeamRepository(session, context)
                config = await repo.get_config(binding.target_config_id)
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
                target_id = uuid.UUID(str(metadata.get("team_id") or config.id))
                version_id = config.published_version_id
                trace_name = f"Public {binding.provider} team"
            else:
                repo = WorkflowRepository(session, context)
                config = await repo.get_config(binding.target_config_id)
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
                target_id = uuid.UUID(str(metadata.get("workflow_id") or config.id))
                version_id = config.published_version_id
                trace_name = f"Public {binding.provider} workflow"

            metadata[f"public_{binding.provider}"] = True
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
                return await _collect_run_text(
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
                logger.exception("public_channel_run_failed", error=str(exc))
                return (
                    "Sorry — we could not complete your request. Please try again later.",
                    False,
                )
        finally:
            if acquired:
                await limiter.async_release(f"public-chat:concurrency:{tenant.id}")


async def _decrypt_credential(
    session: Any, context: TenantContext, credential_id: uuid.UUID
) -> str:
    credential = await CredentialRepository(session, context).get(credential_id)
    if credential is None:
        raise HTTPException(status_code=503, detail="Channel credential missing")
    return AgentFactoryService._decrypt(  # noqa: SLF001
        credential.encrypted_value, credential.key_version
    )


def _verify_slack_signature(*, body: bytes, headers: dict[str, str], secret: str) -> None:
    timestamp = headers.get("x-slack-request-timestamp", "")
    signature = headers.get("x-slack-signature", "")
    if not timestamp or not signature or not secret:
        raise HTTPException(status_code=401, detail="Missing Slack signature headers")
    try:
        ts = int(timestamp)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Invalid Slack timestamp") from exc
    if abs(time.time() - ts) > 60 * 5:
        raise HTTPException(status_code=401, detail="Stale Slack request")
    base = f"v0:{timestamp}:{body.decode('utf-8')}"
    digest = hmac.new(secret.encode(), base.encode(), hashlib.sha256).hexdigest()
    expected = f"v0={digest}"
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=401, detail="Invalid Slack signature")


@router.post("/webhooks/slack")
async def slack_webhook(
    request: Request,
    tenant: TenantSlugQuery,
    binding_id: BindingIdQuery = None,
) -> Response:
    body = await request.body()
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON body") from exc

    if payload.get("type") == "url_verification":
        return JSONResponse({"challenge": payload.get("challenge")})

    tenant_row, binding, context = await _load_binding(
        provider="slack",
        tenant_slug=tenant,
        binding_id=binding_id,
        payload=payload,
    )
    del tenant_row

    async with SessionFactory() as session:
        await apply_tenant_guc(session, context.tenant_id)
        session.info["tenant_id"] = context.tenant_id
        secret = await _decrypt_credential(session, context, binding.credential_id)

    headers = {k.lower(): v for k, v in request.headers.items()}
    # Credential may be bot token or "signing_secret|bot_token"
    signing_secret = secret.split("|", 1)[0].strip()
    _verify_slack_signature(body=body, headers=headers, secret=signing_secret)

    event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
    if event.get("bot_id") or event.get("subtype") == "bot_message":
        return JSONResponse({"ok": True, "ignored": "bot"})
    text = str(event.get("text") or "").strip()
    if not text:
        return JSONResponse({"ok": True, "ignored": "empty"})
    user = str(event.get("user") or "unknown")
    channel = str(event.get("channel") or event.get("user") or "dm")
    guest = guest_user_id_for_channel(provider="slack", external_user=user)
    session_id = session_id_for_channel(provider="slack", external_user=user, thread=channel)
    host = request.client.host if request.client else "anon"
    reply, paused = await _run_binding(
        tenant_slug=tenant,
        binding=binding,
        message=text,
        guest_user_id=guest,
        session_id=session_id,
        client_host=host,
    )
    if paused:
        reply = "Your request is waiting for staff approval."
    # Best-effort reply via chat.postMessage when a bot token is present.
    bot_token = secret.split("|", 1)[1].strip() if "|" in secret else secret
    if bot_token.startswith("xoxb-") and channel:
        try:
            import httpx

            async with httpx.AsyncClient(timeout=15.0) as client:
                await client.post(
                    "https://slack.com/api/chat.postMessage",
                    headers={"Authorization": f"Bearer {bot_token}"},
                    json={"channel": channel, "text": reply},
                )
        except Exception:
            logger.warning("slack_reply_failed")
    return JSONResponse({"ok": True, "replied": True})


@router.post("/webhooks/telegram")
async def telegram_webhook(
    request: Request,
    tenant: TenantSlugQuery,
    binding_id: BindingIdQuery = None,
) -> JSONResponse:
    body = await request.body()
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON body") from exc

    tenant_row, binding, context = await _load_binding(
        provider="telegram",
        tenant_slug=tenant,
        binding_id=binding_id,
        payload=payload,
    )
    del tenant_row

    message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
    text = str(message.get("text") or "").strip()
    if not text:
        return JSONResponse({"ok": True, "ignored": "empty"})
    chat = message.get("chat") if isinstance(message.get("chat"), dict) else {}
    chat_id = str(chat.get("id") or "")
    user = str((message.get("from") or {}).get("id") or chat_id or "unknown")
    guest = guest_user_id_for_channel(provider="telegram", external_user=user)
    session_id = session_id_for_channel(
        provider="telegram", external_user=user, thread=chat_id or user
    )
    host = request.client.host if request.client else "anon"
    reply, paused = await _run_binding(
        tenant_slug=tenant,
        binding=binding,
        message=text,
        guest_user_id=guest,
        session_id=session_id,
        client_host=host,
    )
    if paused:
        reply = "Your request is waiting for staff approval."

    async with SessionFactory() as session:
        await apply_tenant_guc(session, context.tenant_id)
        session.info["tenant_id"] = context.tenant_id
        token = await _decrypt_credential(session, context, binding.credential_id)
    if chat_id and token:
        try:
            import httpx

            async with httpx.AsyncClient(timeout=15.0) as client:
                await client.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id": chat_id, "text": reply},
                )
        except Exception:
            logger.warning("telegram_reply_failed")
    return JSONResponse({"ok": True, "replied": True})


@router.get("/webhooks/whatsapp")
async def whatsapp_verify(
    tenant: TenantSlugQuery,
    hub_mode: Annotated[str | None, Query(alias="hub.mode")] = None,
    hub_verify_token: Annotated[str | None, Query(alias="hub.verify_token")] = None,
    hub_challenge: Annotated[str | None, Query(alias="hub.challenge")] = None,
    binding_id: BindingIdQuery = None,
) -> Response:
    if hub_mode != "subscribe" or not hub_verify_token or hub_challenge is None:
        raise HTTPException(status_code=400, detail="Invalid WhatsApp verification")
    _, binding, context = await _load_binding(
        provider="whatsapp",
        tenant_slug=tenant,
        binding_id=binding_id,
        payload={},
    )
    expected = str((binding.external_config or {}).get("verify_token") or "")
    if not expected:
        async with SessionFactory() as session:
            await apply_tenant_guc(session, context.tenant_id)
            session.info["tenant_id"] = context.tenant_id
            expected = await _decrypt_credential(session, context, binding.credential_id)
    if not hmac.compare_digest(str(hub_verify_token), expected):
        raise HTTPException(status_code=403, detail="Verify token mismatch")
    return PlainTextResponse(str(hub_challenge))


@router.post("/webhooks/whatsapp")
async def whatsapp_webhook(
    request: Request,
    tenant: TenantSlugQuery,
    binding_id: BindingIdQuery = None,
) -> JSONResponse:
    body = await request.body()
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON body") from exc

    tenant_row, binding, context = await _load_binding(
        provider="whatsapp",
        tenant_slug=tenant,
        binding_id=binding_id,
        payload=payload,
    )
    del tenant_row

    text = ""
    sender = "unknown"
    phone_number_id = ""
    try:
        entry = (payload.get("entry") or [])[0]
        change = (entry.get("changes") or [])[0]
        value = change.get("value") or {}
        phone_number_id = str((value.get("metadata") or {}).get("phone_number_id") or "")
        msg = (value.get("messages") or [])[0]
        sender = str(msg.get("from") or "unknown")
        text = str((msg.get("text") or {}).get("body") or "").strip()
    except (IndexError, TypeError, AttributeError):
        return JSONResponse({"ok": True, "ignored": "no_message"})
    if not text:
        return JSONResponse({"ok": True, "ignored": "empty"})

    guest = guest_user_id_for_channel(provider="whatsapp", external_user=sender)
    session_id = session_id_for_channel(
        provider="whatsapp", external_user=sender, thread=phone_number_id or sender
    )
    host = request.client.host if request.client else "anon"
    reply, paused = await _run_binding(
        tenant_slug=tenant,
        binding=binding,
        message=text,
        guest_user_id=guest,
        session_id=session_id,
        client_host=host,
    )
    if paused:
        reply = "Your request is waiting for staff approval."

    async with SessionFactory() as session:
        await apply_tenant_guc(session, context.tenant_id)
        session.info["tenant_id"] = context.tenant_id
        token = await _decrypt_credential(session, context, binding.credential_id)
    phone_id = phone_number_id or str(
        (binding.external_config or {}).get("phone_number_id") or ""
    )
    if phone_id and token and sender:
        try:
            import httpx

            async with httpx.AsyncClient(timeout=15.0) as client:
                await client.post(
                    f"https://graph.facebook.com/v19.0/{phone_id}/messages",
                    headers={"Authorization": f"Bearer {token}"},
                    json={
                        "messaging_product": "whatsapp",
                        "to": sender,
                        "type": "text",
                        "text": {"body": reply},
                    },
                )
        except Exception:
            logger.warning("whatsapp_reply_failed")
    return JSONResponse({"ok": True, "replied": True})
