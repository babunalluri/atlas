import pytest

from app.credentials.provider import CredentialError, LocalFernetCipher
from app.observability.tracing import redact
from app.tools.registry import SafeRestClient, UnsafeOutboundRequest


def test_credentials_encrypt_and_authenticate() -> None:
    cipher = LocalFernetCipher(LocalFernetCipher.generate_key())
    envelope = cipher.encrypt("super-secret")

    assert envelope.ciphertext != "super-secret"
    assert cipher.decrypt(envelope) == "super-secret"

    tampered = envelope.__class__(envelope.ciphertext[:-2] + "aa", envelope.key_version)
    with pytest.raises(CredentialError):
        cipher.decrypt(tampered)


def test_trace_redaction_is_recursive() -> None:
    value = {"headers": {"Authorization": "Bearer secret"}, "nested": [{"api_key": "secret"}]}

    assert redact(value) == {
        "headers": {"Authorization": "[REDACTED]"},
        "nested": [{"api_key": "[REDACTED]"}],
    }


@pytest.mark.asyncio
async def test_ssrf_blocks_loopback() -> None:
    client = SafeRestClient({"localhost"})

    with pytest.raises(UnsafeOutboundRequest, match="non-global"):
        await client.validate_url("https://localhost/private")


@pytest.mark.asyncio
async def test_ssrf_requires_https_and_allowlist() -> None:
    client = SafeRestClient({"api.example.com"})

    with pytest.raises(UnsafeOutboundRequest, match="allowlisted HTTPS"):
        await client.validate_url("http://api.example.com/data")
    with pytest.raises(UnsafeOutboundRequest, match="allowlisted HTTPS"):
        await client.validate_url("https://evil.example/data")
