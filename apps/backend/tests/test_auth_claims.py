"""Auth claim helpers used for Keycloak / OIDC JWTs."""

from __future__ import annotations

import jwt
import pytest

from app.auth.dependencies import (
    AuthClaims,
    _email_from_payload,
    _flatten_oidc_payload,
    _role,
)
from app.db.models import Role


def test_flatten_nested_org_claims():
    flat = _flatten_oidc_payload(
        {"sub": "u1", "o": {"id": "org_demo_acme", "rol": "admin"}}
    )
    assert flat["org_id"] == "org_demo_acme"
    assert flat["org_role"] == "admin"


def test_flatten_multivalued_org_claims():
    flat = _flatten_oidc_payload(
        {"sub": "u1", "org_id": ["org_demo_acme", "org_other"], "org_role": ["org:admin"]}
    )
    assert flat["org_id"] == "org_demo_acme"
    assert flat["org_role"] == "org:admin"


def test_email_from_preferred_username():
    assert (
        _email_from_payload({"preferred_username": "ops@acme.atlas.local"})
        == "ops@acme.atlas.local"
    )
    assert _email_from_payload({"email": "Admin@Atlas.local"}) == "admin@atlas.local"
    assert _email_from_payload({"preferred_username": "not-an-email"}) is None


def test_role_platform_admin_and_org_admin():
    assert (
        _role(AuthClaims(sub="a", org_id="org_x", platform_admin="true"))
        == Role.platform_admin
    )
    assert (
        _role(AuthClaims(sub="a", org_id="org_x", org_role="org:admin"))
        == Role.tenant_admin
    )
    assert _role(AuthClaims(sub="a", org_id="org_x", org_role="org:member")) == Role.end_user


def test_invalid_audience_rejected_without_matching_azp(monkeypatch):
    from app.auth import dependencies as deps
    from app.core.settings import Settings

    settings = Settings(
        auth_issuer="http://localhost:8080/realms/atlas",
        auth_audience="atlas-web",
        auth_jwks_url="http://example.invalid/jwks",
    )

    class _FakeKey:
        key = "secret"

    class _FakeClient:
        def get_signing_key_from_jwt(self, _token: str):
            return _FakeKey()

    def fake_decode(_token, _key, **kwargs):
        if kwargs.get("audience"):
            raise jwt.InvalidAudienceError("Audience doesn't match")
        return {"sub": "u1", "azp": "other-client", "aud": "account"}

    monkeypatch.setattr(deps, "_jwks_client", lambda _url: _FakeClient())
    monkeypatch.setattr(deps.jwt, "decode", fake_decode)

    with pytest.raises(jwt.InvalidAudienceError):
        deps._decode("fake.token.value", settings)


def test_audience_falls_back_to_azp(monkeypatch):
    from app.auth import dependencies as deps
    from app.core.settings import Settings

    settings = Settings(
        auth_issuer="http://localhost:8080/realms/atlas",
        auth_audience="atlas-web",
        auth_jwks_url="http://example.invalid/jwks",
    )

    class _FakeKey:
        key = "secret"

    class _FakeClient:
        def get_signing_key_from_jwt(self, _token: str):
            return _FakeKey()

    def fake_decode(_token, _key, **kwargs):
        if kwargs.get("audience"):
            raise jwt.InvalidAudienceError("Audience doesn't match")
        return {
            "sub": "u1",
            "azp": "atlas-web",
            "aud": "account",
            "org_id": "org_demo_acme",
        }

    monkeypatch.setattr(deps, "_jwks_client", lambda _url: _FakeClient())
    monkeypatch.setattr(deps.jwt, "decode", fake_decode)

    payload = deps._decode("fake.token.value", settings)
    assert payload["sub"] == "u1"
    assert payload["azp"] == "atlas-web"
