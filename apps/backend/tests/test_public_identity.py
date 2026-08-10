"""Verified end-user identity: OTP bind, tenant isolation, profile tools."""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.rate_limit import limiter
from app.db.models import Base, Role, Tenant
from app.identity.service import IdentityService, _hash_code
from app.identity.tools import build_identity_tools
from app.main import app
from app.tenancy.context import TenantContext, set_tenant_context


@pytest.fixture(autouse=True)
def _clear_rate_limiter():
    limiter.clear()
    yield
    limiter.clear()


@pytest.fixture
async def identity_db(monkeypatch):
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(eng, expire_on_commit=False, class_=AsyncSession)

    tenant_a = Tenant(
        id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
        auth_org_id="org_demo_acme",
        slug="acme",
        name="Acme Corp",
        branding={},
    )
    tenant_b = Tenant(
        id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        auth_org_id="org_demo_globex",
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
        "app.api.public_identity.SessionFactory",
        "app.auth.dependencies.SessionFactory",
        "app.agent_runtime.agent_os.SessionFactory",
    ):
        monkeypatch.setattr(target, make_session, raising=False)

    ctx_a = TenantContext(
        tenant_id=tenant_a.id,
        user_id="guest-a",
        role=Role.end_user,
        auth_org_id=tenant_a.auth_org_id,
        principal_type="guest",
    )
    ctx_b = TenantContext(
        tenant_id=tenant_b.id,
        user_id="guest-b",
        role=Role.end_user,
        auth_org_id=tenant_b.auth_org_id,
        principal_type="guest",
    )
    yield {"factory": factory, "tenant_a": ctx_a, "tenant_b": ctx_b, "eng": eng}
    await eng.dispose()


async def test_challenge_verify_and_status(identity_db, monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    from app.core.settings import get_settings

    get_settings.cache_clear()

    session_id = f"sess_{uuid.uuid4().hex}"
    headers = {"X-Guest-Id": "guest-a-unique-01"}

    with patch(
        "app.identity.service.send_resend_email",
        new_callable=AsyncMock,
    ) as send_mock:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            challenge = await client.post(
                "/public/t/acme/identity/challenge",
                headers=headers,
                json={"email": "Pat@Example.com", "session_id": session_id},
            )
            assert challenge.status_code == 200, challenge.text
            body = challenge.json()
            assert body["email"] == "pat@example.com"
            assert body.get("debug_code")
            code = body["debug_code"]

            status_before = await client.get(
                "/public/t/acme/identity/status",
                headers=headers,
                params={"session_id": session_id},
            )
            assert status_before.status_code == 200
            assert status_before.json()["verified"] is False

            verify = await client.post(
                "/public/t/acme/identity/verify",
                headers=headers,
                json={
                    "email": "pat@example.com",
                    "code": code,
                    "session_id": session_id,
                },
            )
            assert verify.status_code == 200, verify.text
            verified = verify.json()
            assert verified["verified"] is True
            assert verified["email"] == "pat@example.com"
            assert verified["end_user_id"]

            status_after = await client.get(
                "/public/t/acme/identity/status",
                headers=headers,
                params={"session_id": session_id},
            )
            assert status_after.json()["verified"] is True
            assert status_after.json()["email"] == "pat@example.com"

    # Dev mode may skip send; either way challenge succeeded.
    assert challenge.status_code == 200
    _ = send_mock


async def test_verify_rejects_wrong_guest(identity_db, monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    from app.core.settings import get_settings

    get_settings.cache_clear()

    session_id = f"sess_{uuid.uuid4().hex}"
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        challenge = await client.post(
            "/public/t/acme/identity/challenge",
            headers={"X-Guest-Id": "guest-owner-aaaa"},
            json={"email": "owner@example.com", "session_id": session_id},
        )
        assert challenge.status_code == 200
        code = challenge.json()["debug_code"]
        stolen = await client.post(
            "/public/t/acme/identity/verify",
            headers={"X-Guest-Id": "guest-thief-bbbb"},
            json={
                "email": "owner@example.com",
                "code": code,
                "session_id": session_id,
            },
        )
        assert stolen.status_code == 403


async def test_end_users_are_tenant_scoped(identity_db):
    factory = identity_db["factory"]
    ctx_a = identity_db["tenant_a"]
    ctx_b = identity_db["tenant_b"]

    async with factory() as session:
        session.info["tenant_id"] = ctx_a.tenant_id
        user_a = await IdentityService(session, ctx_a).ensure_email_identity(
            "shared@example.com"
        )
        await session.commit()
        a_id = user_a.id

    async with factory() as session:
        session.info["tenant_id"] = ctx_b.tenant_id
        user_b = await IdentityService(session, ctx_b).ensure_email_identity(
            "shared@example.com"
        )
        await session.commit()
        assert user_b.id != a_id
        assert user_b.tenant_id == ctx_b.tenant_id

    async with factory() as session:
        session.info["tenant_id"] = ctx_b.tenant_id
        from app.db.repositories import EndUserRepository

        leaked = await EndUserRepository(session, ctx_b).get(a_id)
        assert leaked is None


async def test_my_profile_uses_context_not_model_ids(identity_db):
    factory = identity_db["factory"]
    ctx = identity_db["tenant_a"]

    async with factory() as session:
        session.info["tenant_id"] = ctx.tenant_id
        user = await IdentityService(session, ctx).ensure_email_identity(
            "profile@example.com"
        )
        await session.commit()

        bound = TenantContext(
            tenant_id=ctx.tenant_id,
            user_id=ctx.user_id,
            role=ctx.role,
            auth_org_id=ctx.auth_org_id,
            principal_type="guest",
            verified_end_user_id=user.id,
            verified_email=user.email,
        )
        set_tenant_context(bound)
        tools = build_identity_tools(session, bound)
        my_profile = next(t for t in tools if getattr(t, "name", None) == "my_profile")
        raw = await my_profile()
        payload = json.loads(raw)
        assert payload["verified"] is True
        assert payload["email"] == "profile@example.com"
        assert payload["id"] == str(user.id)

        unbound = TenantContext(
            tenant_id=ctx.tenant_id,
            user_id=ctx.user_id,
            role=ctx.role,
            auth_org_id=ctx.auth_org_id,
            principal_type="guest",
        )
        set_tenant_context(unbound)
        tools_u = build_identity_tools(session, unbound)
        my_profile_u = next(
            t for t in tools_u if getattr(t, "name", None) == "my_profile"
        )
        raw_u = await my_profile_u()
        assert json.loads(raw_u)["verified"] is False


def test_otp_hash_is_tenant_and_email_scoped():
    a = _hash_code("123456", tenant_id="t1", email="a@x.com")
    b = _hash_code("123456", tenant_id="t2", email="a@x.com")
    c = _hash_code("123456", tenant_id="t1", email="b@x.com")
    assert a != b
    assert a != c
