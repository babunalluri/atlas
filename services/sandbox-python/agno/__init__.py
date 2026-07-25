"""Minimal Agno compatibility shims for Editable Python sandbox guests.

Real Agno is not installed in the guest image. These packages exist so common
imports (e.g. ``from agno.utils.log import log_info``) succeed at import time.
Editable Python still requires top-level ``async def`` capabilities — class-based
Agno toolkits are not executed as Agent toolkits in v1.
"""

__all__: list[str] = []
