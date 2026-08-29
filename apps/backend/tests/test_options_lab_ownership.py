"""Per-user bots and backtests inside the shared tenant session blob.

Bots and backtests live in one tenant-wide blob so the worker can run every
armed bot in a single read. Ownership therefore partitions the blob logically:
a trader sees only their own rows, an operator (and the worker, which builds a
tenant_admin context) sees all of them.
"""

from __future__ import annotations

import pytest

from app.domains import signal_engine_cache as signal_cache
from app.domains.options_lab_backtests import owned_by as bt_owned_by
from app.domains.options_lab_backtests import visible_rows as bt_visible
from app.domains.options_lab_bots import (
    create_bot,
    delete_bot,
    get_bot,
    list_bots,
    load_bots,
    owned_by,
    update_bot,
    visible_rows,
)

TENANT = "11111111-1111-1111-1111-111111111111"
ALICE = "user-alice"
BOB = "user-bob"


@pytest.fixture(autouse=True)
def _reset_cache() -> None:
    signal_cache.reset_signal_cache_for_tests()
    yield
    signal_cache.reset_signal_cache_for_tests()


def _paper(name: str) -> dict[str, object]:
    return {"name": name, "template": "short_straddle", "mode": "paper"}


def test_owned_by_operator_scope_sees_everything() -> None:
    assert owned_by({"owner_id": ALICE}, None) is True
    assert owned_by({"owner_id": None}, None) is True


def test_owned_by_trader_scope_is_exact() -> None:
    assert owned_by({"owner_id": ALICE}, ALICE) is True
    assert owned_by({"owner_id": BOB}, ALICE) is False


def test_legacy_rows_without_an_owner_stay_operator_only() -> None:
    # Written while these routes were admin-only, so they belong to operators —
    # inheriting them to every end user would hand out someone else's bots.
    legacy = {"id": "bot-legacy"}
    assert owned_by(legacy, None) is True
    assert owned_by(legacy, ALICE) is False
    assert visible_rows([legacy], ALICE) == []
    assert bt_owned_by(legacy, ALICE) is False
    assert bt_visible([legacy], None) == [legacy]


async def test_create_stamps_the_owner_and_list_partitions() -> None:
    await create_bot(TENANT, _paper("alice one"), owner_id=ALICE)
    await create_bot(TENANT, _paper("bob one"), owner_id=BOB)

    alice = await list_bots(TENANT, owner_id=ALICE)
    bob = await list_bots(TENANT, owner_id=BOB)
    operator = await list_bots(TENANT, owner_id=None)

    assert [b["name"] for b in alice["bots"]] == ["alice one"]
    assert [b["name"] for b in bob["bots"]] == ["bob one"]
    # The worker and the admin desk both need the whole tenant.
    assert {b["name"] for b in operator["bots"]} == {"alice one", "bob one"}


async def test_a_trader_cannot_read_update_or_delete_another_users_bot() -> None:
    created = await create_bot(TENANT, _paper("alice one"), owner_id=ALICE)
    bot_id = created["bot"]["id"]

    # Absent, not forbidden: the response must not confirm the id exists.
    assert await get_bot(TENANT, bot_id, owner_id=BOB) == {
        "ok": False,
        "error": "Bot not found.",
    }
    assert (await update_bot(TENANT, bot_id, {"name": "hijacked"}, owner_id=BOB))[
        "ok"
    ] is False
    assert (await delete_bot(TENANT, bot_id, owner_id=BOB))["ok"] is False

    # And the row is untouched.
    still = await get_bot(TENANT, bot_id, owner_id=ALICE)
    assert still["ok"] and still["bot"]["name"] == "alice one"


async def test_update_cannot_reassign_ownership() -> None:
    created = await create_bot(TENANT, _paper("alice one"), owner_id=ALICE)
    bot_id = created["bot"]["id"]

    await update_bot(TENANT, bot_id, {"owner_id": BOB, "name": "renamed"}, owner_id=ALICE)

    rows = await load_bots(TENANT)
    assert [r["owner_id"] for r in rows] == [ALICE]


async def test_operator_scope_can_still_manage_a_traders_bot() -> None:
    created = await create_bot(TENANT, _paper("alice one"), owner_id=ALICE)
    bot_id = created["bot"]["id"]

    assert (await get_bot(TENANT, bot_id, owner_id=None))["ok"] is True
    assert (await delete_bot(TENANT, bot_id, owner_id=None))["ok"] is True
    assert await load_bots(TENANT) == []
