"""Distributed leadership helpers for the in-process scheduler poller."""

from __future__ import annotations

import os
import uuid

from app.core.logging import get_logger
from app.core.redis_client import (
    get_redis,
    redis_enabled,
    release_leader_lock,
    renew_leader_lock,
)
from app.core.settings import get_settings

logger = get_logger(__name__)

LEADER_KEY = "atlas:scheduler:leader"
LEADER_TTL_SECONDS = 30


class SchedulerLeaderLock:
    """Redis SET NX leader lock with TTL renew. Falls back for local/test only."""

    def __init__(self) -> None:
        self.instance_id = f"{os.getpid()}:{uuid.uuid4().hex[:8]}"
        self._memory_held = False

    async def try_acquire(self) -> bool:
        settings = get_settings()
        if not redis_enabled():
            if settings.is_development:
                self._memory_held = True
                return True
            logger.error(
                "scheduler_redis_required",
                detail="REDIS_URL required for scheduler in non-development environments",
            )
            return False

        client = await get_redis()
        if client is None:
            if settings.is_development:
                self._memory_held = True
                return True
            logger.error("scheduler_redis_unavailable")
            return False

        acquired = await client.set(
            LEADER_KEY,
            self.instance_id,
            nx=True,
            ex=LEADER_TTL_SECONDS,
        )
        if acquired:
            return True
        return await renew_leader_lock(LEADER_KEY, self.instance_id, LEADER_TTL_SECONDS)

    async def renew(self) -> bool:
        if self._memory_held:
            return True
        return await renew_leader_lock(LEADER_KEY, self.instance_id, LEADER_TTL_SECONDS)

    async def release(self) -> None:
        if self._memory_held:
            self._memory_held = False
            return
        await release_leader_lock(LEADER_KEY, self.instance_id)
