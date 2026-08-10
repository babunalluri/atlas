"""Resend inbound email address parsing and webhook handling."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.rate_limit import limiter
from app.db.models import Base, Role, Tenant
from app.db.repositories import TeamRepository
from app.email.addressing import (
    build_inbound_address,
    parse_inbound_address,
    strip_quoted_reply,
)
from app.email.resend import ResendError, verify_svix_signature
from app.main import app
from app.tenancy.context import TenantContext


@pytest.fixture(autouse=True)
def _clear_rate_limiter():
    limiter.clear()
    yield
    limiter.clear()


def test_parse_inbound_address_team_and_workflow():
    parsed = parse_inbound_address(
        "team-babus-org.testteam@inbound.example.com",
        inbound_domain="inbound.example.com",
    )
    assert parsed is not None
    assert parsed.kind == "team"
    assert parsed.tenant_slug == "babus-org"
    assert parsed.resource_slug == "testteam"

    workflow = parse_inbound_address(
        "Workflow-acme.intake@inbound.example.com",
        inbound_domain="inbound.example.com",
    )
    assert workflow is not None
    assert workflow.kind == "workflow"
    assert workflow.tenant_slug == "acme"
    assert workflow.resource_slug == "intake"


def test_parse_inbound_address_rejects_wrong_domain_and_junk():
    assert (
        parse_inbound_address(
            "team-acme.support@other.example.com",
            inbound_domain="inbound.example.com",
        )
        is None
    )
    assert (
        parse_inbound_address(
            "agent-acme.bot@inbound.example.com",
            inbound_domain="inbound.example.com",
        )
        is None
    )
    assert build_inbound_address(
        kind="team",
        tenant_slug="acme",
        resource_slug="support",
        inbound_domain="inbound.example.com",
    ) == "team-acme.support@inbound.example.com"


def test_strip_quoted_reply():
    body = "Please book Tuesday.\n\nOn Mon, someone wrote:\n> old text"
    assert strip_quoted_reply(body) == "Please book Tuesday."


def _svix_headers(body: bytes, secret: str) -> dict[str, str]:
    raw = secret[len("whsec_") :] if secret.startswith("whsec_") else secret
    key = base64.b64decode(raw)
    msg_id = "msg_test_1"
    timestamp = str(int(time.time()))
    signed = f"{msg_id}.{timestamp}.".encode() + body
    digest = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
    return {
        "svix-id": msg_id,
        "svix-timestamp": timestamp,
        "svix-signature": f"v1,{digest}",
    }


def test_verify_svix_signature_accepts_valid_and_rejects_bad():
    secret = "whsec_" + base64.b64encode(b"0123456789abcdef0123456789abcdef").decode()
    body = b'{"type":"email.received"}'
    headers = _svix_headers(body, secret)
    verify_svix_signature(body=body, headers=headers, secret=secret)
    with pytest.raises(ResendError):
        verify_svix_signature(
            body=body,
            headers={**headers, "svix-signature": "v1,deadbeef"},
            secret=secret,
        )


@pytest.fixture
async def email_db(monkeypatch):
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(eng, expire_on_commit=False, class_=AsyncSession)

    tenant_a = Tenant(
        id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
        clerk_org_id="org_demo_acme",
        slug="acme",
        name="Acme Corp",
        branding={"primaryColor": "#0f766e"},
    )
    tenant_b = Tenant(
        id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        clerk_org_id="org_demo_globex",
        slug="globex",
        name="Globex Inc",
        branding={},
    )
    async with factory() as session:
        session.add_all([tenant_a, tenant_b])
        await session.commit()

    def make_session():
        return factory()

    for target in (
        "app.db.session.SessionFactory",
        "app.api.public_chat.SessionFactory",
        "app.auth.dependencies.SessionFactory",
        "app.agent_runtime.agent_os.SessionFactory",
    ):
        monkeypatch.setattr(target, make_session)

    monkeypatch.setenv("EMAIL_INBOUND_DOMAIN", "inbound.example.com")
    monkeypatch.setenv(
        "RESEND_WEBHOOK_SECRET",
        "whsec_" + base64.b64encode(b"0123456789abcdef0123456789abcdef").decode(),
    )
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key")
    from app.core.settings import get_settings

    get_settings.cache_clear()

    # Reset in-memory idempotency between tests.
    from app.api import public_email as public_email_mod

    public_email_mod._MEMORY_PROCESSED.clear()

    ctx_a = TenantContext(
        tenant_id=tenant_a.id,
        user_id="user-a",
        role=Role.tenant_admin,
        clerk_org_id=tenant_a.clerk_org_id,
    )
    yield {"factory": factory, "tenant_a": ctx_a, "tenant_b": tenant_b}
    get_settings.cache_clear()
    await eng.dispose()


async def _published_team(factory, tenant: TenantContext, slug: str):
    async with factory() as session:
        session.info["tenant_id"] = tenant.tenant_id
        from app.db.repositories import AgentRepository

        agents = AgentRepository(session, tenant)
        agent = await agents.create_config(slug=f"agent-{slug}", name="Agent")
        agent_version = await agents.create_draft(
            config_id=agent.id,
            instructions="help",
            model_id="openai:gpt-4.1-mini",
            temperature=0.2,
        )
        await agents.publish(agent_version.id)

        teams = TeamRepository(session, tenant)
        config = await teams.create_config(slug=slug, name=slug.title())
        draft = await teams.create_draft(
            config_id=config.id,
            instructions="You are helpful.",
            model_id="openai:gpt-4.1-mini",
            temperature=0.2,
            mode="coordinate",
            member_config_ids=[agent.id],
        )
        await teams.publish(draft.id)
        await session.commit()
        return config


@pytest.mark.asyncio
async def test_webhook_rejects_bad_signature(email_db, monkeypatch):
    del email_db
    body = json.dumps(
        {
            "type": "email.received",
            "data": {
                "email_id": "email_1",
                "from": "user@example.com",
                "to": ["team-acme.support@inbound.example.com"],
                "subject": "Hi",
                "text": "Hello",
            },
        }
    ).encode()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/public/webhooks/resend",
            content=body,
            headers={
                "content-type": "application/json",
                "svix-id": "msg_x",
                "svix-timestamp": str(int(time.time())),
                "svix-signature": "v1,nope",
            },
        )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_webhook_happy_path_sends_reply(email_db, monkeypatch):
    factory = email_db["factory"]
    tenant_a = email_db["tenant_a"]
    await _published_team(factory, tenant_a, "support")

    secret = (
        "whsec_" + base64.b64encode(b"0123456789abcdef0123456789abcdef").decode()
    )
    body = json.dumps(
        {
            "type": "email.received",
            "data": {
                "email_id": "email_happy_1",
                "from": "customer@example.com",
                "to": ["team-acme.support@inbound.example.com"],
                "subject": "Need help",
                "text": "What are your hours?",
                "message_id": "<msg-1@example.com>",
            },
        }
    ).encode()
    headers = _svix_headers(body, secret)

    fake_runtime = MagicMock()
    fake_runtime._saas_metadata = {
        "team_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "team_version_id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
    }

    class _TeamFactory:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        async def create(self, *_args: Any, **_kwargs: Any) -> Any:
            return fake_runtime

    async def fake_collect(*_args: Any, **_kwargs: Any) -> tuple[str, bool]:
        return "We are open 9–5.", False

    sent: dict[str, Any] = {}

    async def fake_send(api_key: str, **kwargs: Any) -> dict[str, Any]:
        sent["api_key"] = api_key
        sent.update(kwargs)
        return {"id": "re_123"}

    async def fake_trace(**_kwargs: Any) -> uuid.UUID:
        return uuid.uuid4()

    monkeypatch.setattr("app.api.public_email.TeamFactoryService", _TeamFactory)
    monkeypatch.setattr("app.api.public_email.AgentFactoryService", MagicMock)
    monkeypatch.setattr("app.api.public_email._collect_run_text", fake_collect)
    monkeypatch.setattr("app.api.public_email.send_resend_email", fake_send)
    monkeypatch.setattr(
        "app.agent_runtime.agent_os._start_runtime_trace", fake_trace
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/public/webhooks/resend",
            content=body,
            headers={"content-type": "application/json", **headers},
        )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["ok"] is True
    assert payload["kind"] == "team"
    assert payload["tenant"] == "acme"
    assert sent["to_address"] == "customer@example.com"
    assert "We are open" in sent["text"]
    assert sent["subject"].lower().startswith("re:")


@pytest.mark.asyncio
async def test_webhook_unpublished_team_404(email_db, monkeypatch):
    secret = (
        "whsec_" + base64.b64encode(b"0123456789abcdef0123456789abcdef").decode()
    )
    body = json.dumps(
        {
            "type": "email.received",
            "data": {
                "email_id": "email_missing_1",
                "from": "customer@example.com",
                "to": ["team-acme.missing@inbound.example.com"],
                "subject": "Hi",
                "text": "Hello",
            },
        }
    ).encode()
    headers = _svix_headers(body, secret)
    monkeypatch.setattr(
        "app.api.public_email.send_resend_email",
        AsyncMock(side_effect=AssertionError("should not send")),
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/public/webhooks/resend",
            content=body,
            headers={"content-type": "application/json", **headers},
        )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_webhook_cross_tenant_address_does_not_use_other_tenant(
    email_db, monkeypatch
):
    """Address names globex tenant; acme tools/data must not be selected via wrong slug."""
    factory = email_db["factory"]
    tenant_a = email_db["tenant_a"]
    await _published_team(factory, tenant_a, "support")

    secret = (
        "whsec_" + base64.b64encode(b"0123456789abcdef0123456789abcdef").decode()
    )
    body = json.dumps(
        {
            "type": "email.received",
            "data": {
                "email_id": "email_xtenant_1",
                "from": "customer@example.com",
                "to": ["team-globex.support@inbound.example.com"],
                "subject": "Hi",
                "text": "Hello",
            },
        }
    ).encode()
    headers = _svix_headers(body, secret)
    monkeypatch.setattr(
        "app.api.public_email.send_resend_email",
        AsyncMock(side_effect=AssertionError("should not send")),
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/public/webhooks/resend",
            content=body,
            headers={"content-type": "application/json", **headers},
        )
    # Globex exists but has no published support team.
    assert response.status_code == 404
