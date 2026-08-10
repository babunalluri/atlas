"""Per-user vault: isolation, settings merge, session_state token mapping."""

from __future__ import annotations

import pytest

from app.credentials.provider import EncryptedEnvelope, LocalFernetCipher
from app.db.models import Role
from app.db.repositories import UserVaultRepository
from app.tenancy.context import TenantContext
from app.tools.providers import merge_user_vault_into_settings
from app.vault import pick_user_token, session_state_for_user


@pytest.fixture
def cipher() -> LocalFernetCipher:
    # 32-byte urlsafe key material via settings-compatible path
    from app.core.settings import get_settings

    settings = get_settings()
    return LocalFernetCipher(
        settings.encryption_key.get_secret_value(),
        settings.encryption_key_version,
        previous_keys=settings.encryption_previous_keys,
    )


@pytest.mark.asyncio
async def test_vault_isolation_by_user_and_tenant(session, tenant_a, tenant_b, cipher):
    session.info["tenant_id"] = tenant_a.tenant_id
    repo_a = UserVaultRepository(session, tenant_a)
    other_user = TenantContext(
        tenant_id=tenant_a.tenant_id,
        user_id="user-a-other",
        role=Role.end_user,
        auth_org_id=tenant_a.auth_org_id,
    )
    env = cipher.encrypt("secret-a")
    await repo_a.upsert(
        user_id=tenant_a.user_id,
        name="user_token",
        kind="secret",
        encrypted_value=env.ciphertext,
        key_version=env.key_version,
    )
    env_other = cipher.encrypt("secret-other")
    await UserVaultRepository(session, other_user).upsert(
        user_id=other_user.user_id,
        name="user_token",
        kind="secret",
        encrypted_value=env_other.ciphertext,
        key_version=env_other.key_version,
    )
    await session.flush()

    mine = await repo_a.list_for_user(tenant_a.user_id)
    theirs = await UserVaultRepository(session, other_user).list_for_user(other_user.user_id)
    assert len(mine) == 1
    assert mine[0].name == "user_token"
    assert (
        cipher.decrypt(
            EncryptedEnvelope(mine[0].encrypted_value, mine[0].key_version)
        )
        == "secret-a"
    )
    assert len(theirs) == 1
    assert theirs[0].user_id == "user-a-other"

    # Tenant B context cannot see tenant A rows via scoped queries.
    session.info["tenant_id"] = tenant_b.tenant_id
    cross = await UserVaultRepository(session, tenant_b).list_for_user(tenant_a.user_id)
    assert cross == []


@pytest.mark.asyncio
async def test_vault_list_never_exposes_plaintext_in_repo_fields(session, tenant_a, cipher):
    session.info["tenant_id"] = tenant_a.tenant_id
    env = cipher.encrypt("super-secret-value")
    row = await UserVaultRepository(session, tenant_a).upsert(
        user_id=tenant_a.user_id,
        name="api_key",
        kind="secret",
        encrypted_value=env.ciphertext,
        key_version=env.key_version,
    )
    assert "super-secret-value" not in row.encrypted_value
    listed = await UserVaultRepository(session, tenant_a).list_for_user(tenant_a.user_id)
    assert listed[0].name == "api_key"
    assert listed[0].kind == "secret"


def test_merge_user_vault_overrides_settings():
    merged = merge_user_vault_into_settings(
        {"domain": "shared", "timeout": 30},
        {"domain": "mine", "api_key": "k"},
    )
    assert merged["domain"] == "mine"
    assert merged["api_key"] == "k"
    assert merged["timeout"] == 30


def test_pick_user_token_preference_order():
    assert pick_user_token({"api_key": "a", "bearer_token": "b", "user_token": "u"}) == "u"
    assert pick_user_token({"api_key": "a", "bearer_token": "b"}) == "b"
    assert pick_user_token({"api_key": "a"}) == "a"
    assert pick_user_token({}) is None


def test_session_state_for_user_matches_resolve_token_path(tenant_a):
    state = session_state_for_user(
        tenant_a,
        {"user_token": "tok-1", "freshdesk_domain": "acme"},
    )
    assert state is not None
    data = state["additional_information"]["data"]
    assert data["user_token"] == "tok-1"
    assert data["userId"] == tenant_a.user_id
    assert data["freshdesk_domain"] == "acme"
