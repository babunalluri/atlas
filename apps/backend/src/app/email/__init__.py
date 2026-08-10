"""Email channel helpers (Resend inbound + outbound)."""

from app.email.addressing import (
    InboundAddress,
    build_inbound_address,
    normalize_email,
    parse_inbound_address,
    strip_quoted_reply,
)

__all__ = [
    "InboundAddress",
    "build_inbound_address",
    "normalize_email",
    "parse_inbound_address",
    "strip_quoted_reply",
]
