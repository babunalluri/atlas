"""Inbound email address parsing and body cleanup for the Resend channel."""

from __future__ import annotations

import re
from dataclasses import dataclass
from email.utils import parseaddr


_LOCAL_RE = re.compile(
    r"^(?P<kind>team|workflow)-(?P<tenant>[a-z0-9]+(?:-[a-z0-9]+)*)\.(?P<resource>[a-z0-9]+(?:-[a-z0-9]+)*)$"
)
_QUOTE_MARKERS = (
    "\n>",
    "\nOn ",
    "\nFrom:",
    "\n-----Original Message-----",
    "\n________________________________",
)


@dataclass(frozen=True, slots=True)
class InboundAddress:
    kind: str  # "team" | "workflow"
    tenant_slug: str
    resource_slug: str
    raw: str


def normalize_email(value: str) -> str:
    _, addr = parseaddr((value or "").strip())
    return addr.lower().strip()


def parse_inbound_address(value: str, *, inbound_domain: str) -> InboundAddress | None:
    """Parse ``team-{tenant}.{slug}@domain`` / ``workflow-…`` addresses."""
    addr = normalize_email(value)
    if not addr or "@" not in addr:
        return None
    local, _, domain = addr.partition("@")
    expected = (inbound_domain or "").strip().lower()
    if expected and domain != expected:
        return None
    match = _LOCAL_RE.match(local)
    if match is None:
        return None
    return InboundAddress(
        kind=match.group("kind"),
        tenant_slug=match.group("tenant"),
        resource_slug=match.group("resource"),
        raw=addr,
    )


def build_inbound_address(
    *,
    kind: str,
    tenant_slug: str,
    resource_slug: str,
    inbound_domain: str,
) -> str | None:
    domain = (inbound_domain or "").strip().lower()
    if not domain or kind not in {"team", "workflow"}:
        return None
    return f"{kind}-{tenant_slug}.{resource_slug}@{domain}"


def strip_quoted_reply(body: str) -> str:
    """Drop common reply/signature tails; keep the newest customer text."""
    text = (body or "").replace("\r\n", "\n").strip()
    if not text:
        return ""
    cut = len(text)
    for marker in _QUOTE_MARKERS:
        idx = text.find(marker)
        if idx > 0:
            cut = min(cut, idx)
    # Plain ">" quoted blocks at the start of a line after a blank line.
    lines = text[:cut].split("\n")
    kept: list[str] = []
    for line in lines:
        if kept and line.startswith(">"):
            break
        kept.append(line)
    return "\n".join(kept).strip()
