"""Background evaluator for armed Options Lab paper bots.

Slow poll (~60s). Paper only — live never auto-fires. No Agno.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid

from sqlalchemy import select

from app.core.logging import get_logger
from app.db.models import Role, Tenant
from app.db.session import SessionFactory, apply_tenant_guc
from app.domains.options_lab import OptionsLabService
from app.domains.options_lab_bots import list_armed_tenant_ids
from app.tenancy.context import TenantContext

logger = get_logger(__name__)

BOTS_POLL_SECONDS = 60


def _parse_tenant_ids(raw_ids: list[str]) -> list[uuid.UUID]:
    out: list[uuid.UUID] = []
    for tid in raw_ids:
        try:
            out.append(uuid.UUID(tid))
        except ValueError:
            logger.warning("options_lab_bots_skip_invalid_tenant", tenant_id=tid)
    return out


async def evaluate_tenant_bots(tenant_id: uuid.UUID, *, auth_org_id: str) -> dict:
    context = TenantContext(
        tenant_id=tenant_id,
        user_id="options-lab-bots",
        role=Role.tenant_admin,
        auth_org_id=auth_org_id,
        principal_type="scheduler",
    )
    try:
        async with SessionFactory() as session:
            async with session.begin():
                await apply_tenant_guc(session, tenant_id)
                return await OptionsLabService(session, context).evaluate_armed_bots()
    except Exception as exc:
        logger.warning(
            "options_lab_bots_evaluate_failed",
            tenant_id=str(tenant_id),
            error=str(exc),
        )
        return {"ok": False, "error": str(exc)}


class OptionsLabBotsWorker:
    """Ticks armed paper bots once per minute when enabled."""

    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self.tick()
            except Exception as exc:
                logger.exception("options_lab_bots_tick_failed", error=str(exc))
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=BOTS_POLL_SECONDS)
            except asyncio.TimeoutError:
                continue

    async def tick(self) -> int:
        raw_ids = await list_armed_tenant_ids()
        tenant_ids = _parse_tenant_ids(raw_ids)
        if not tenant_ids:
            return 0
        async with SessionFactory() as session:
            async with session.begin():
                rows = (
                    await session.execute(
                        select(Tenant.id, Tenant.auth_org_id).where(Tenant.id.in_(tenant_ids))
                    )
                ).all()
        org_by_id = {row.id: str(row.auth_org_id or "") for row in rows}
        ran = 0
        for tid in tenant_ids:
            org = org_by_id.get(tid)
            if not org:
                continue
            out = await evaluate_tenant_bots(tid, auth_org_id=org)
            if out.get("ok"):
                ran += 1
        return ran
