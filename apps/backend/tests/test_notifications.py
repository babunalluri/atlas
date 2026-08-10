"""In-app org notifications: fan-out, isolation, read state."""

import pytest

from app.db.models import Role
from app.db.repositories import MembershipRepository, UserNotificationRepository
from app.tenancy.context import TenantContext


async def _seed_users(session, tenant):
    session.info["tenant_id"] = tenant.tenant_id
    users = MembershipRepository(session, tenant)
    alice = await users.create(
        user_id="user_alice",
        display_name="Alice",
        email="alice@example.com",
        role=Role.end_user,
        is_active=True,
    )
    bob = await users.create(
        user_id="user_bob",
        display_name="Bob",
        email="bob@example.com",
        role=Role.end_user,
        is_active=True,
    )
    inactive = await users.create(
        user_id="user_inactive",
        display_name="Inactive",
        email="off@example.com",
        role=Role.end_user,
        is_active=False,
    )
    pending = await users.create(
        user_id="invite:pending@example.com",
        display_name="Pending",
        email="pending@example.com",
        role=Role.end_user,
        is_active=True,
    )
    return users, alice, bob, inactive, pending


@pytest.mark.asyncio
async def test_send_to_one_user(session, tenant_a):
    _, alice, bob, _, _ = await _seed_users(session, tenant_a)
    notes = UserNotificationRepository(session, tenant_a)
    batch_id, rows = await notes.create_batch(
        title="Hello",
        body="Just Alice",
        created_by=tenant_a.user_id,
        audience="user",
        recipient_user_ids=[alice.user_id],
    )
    assert len(rows) == 1
    assert rows[0].batch_id == batch_id
    assert rows[0].user_id == "user_alice"

    alice_ctx = TenantContext(
        tenant_id=tenant_a.tenant_id,
        user_id=alice.user_id,
        role=Role.end_user,
        auth_org_id=tenant_a.auth_org_id,
    )
    bob_ctx = TenantContext(
        tenant_id=tenant_a.tenant_id,
        user_id=bob.user_id,
        role=Role.end_user,
        auth_org_id=tenant_a.auth_org_id,
    )
    assert len(await UserNotificationRepository(session, alice_ctx).list_for_user(alice.user_id)) == 1
    assert len(await UserNotificationRepository(session, bob_ctx).list_for_user(bob.user_id)) == 0


@pytest.mark.asyncio
async def test_send_to_all_active_skips_inactive_and_invite(session, tenant_a):
    users, alice, bob, inactive, pending = await _seed_users(session, tenant_a)
    rows = await users.list_users()
    recipients = [
        row.user_id
        for row in rows
        if row.is_active and row.user_id and not row.user_id.startswith("invite:")
    ]
    notes = UserNotificationRepository(session, tenant_a)
    _, created = await notes.create_batch(
        title="All hands",
        body="Everyone",
        created_by=tenant_a.user_id,
        audience="all",
        recipient_user_ids=recipients,
    )
    got = {row.user_id for row in created}
    assert got == {alice.user_id, bob.user_id}
    assert inactive.user_id not in got
    assert pending.user_id not in got


@pytest.mark.asyncio
async def test_mark_read_and_unread_count(session, tenant_a):
    _, alice, _, _, _ = await _seed_users(session, tenant_a)
    alice_ctx = TenantContext(
        tenant_id=tenant_a.tenant_id,
        user_id=alice.user_id,
        role=Role.end_user,
        auth_org_id=tenant_a.auth_org_id,
    )
    notes = UserNotificationRepository(session, alice_ctx)
    _, rows = await notes.create_batch(
        title="A",
        body="1",
        created_by=tenant_a.user_id,
        audience="user",
        recipient_user_ids=[alice.user_id],
    )
    await notes.create_batch(
        title="B",
        body="2",
        created_by=tenant_a.user_id,
        audience="user",
        recipient_user_ids=[alice.user_id],
    )
    assert await notes.unread_count(alice.user_id) == 2
    marked = await notes.mark_read(alice.user_id, rows[0].id)
    assert marked is not None
    assert marked.read_at is not None
    assert await notes.unread_count(alice.user_id) == 1
    updated = await notes.mark_all_read(alice.user_id)
    assert updated == 1
    assert await notes.unread_count(alice.user_id) == 0


@pytest.mark.asyncio
async def test_recipient_cannot_read_other_users_notification(session, tenant_a):
    _, alice, bob, _, _ = await _seed_users(session, tenant_a)
    admin_notes = UserNotificationRepository(session, tenant_a)
    _, rows = await admin_notes.create_batch(
        title="Private",
        body="Alice only",
        created_by=tenant_a.user_id,
        audience="user",
        recipient_user_ids=[alice.user_id],
    )
    bob_ctx = TenantContext(
        tenant_id=tenant_a.tenant_id,
        user_id=bob.user_id,
        role=Role.end_user,
        auth_org_id=tenant_a.auth_org_id,
    )
    bob_notes = UserNotificationRepository(session, bob_ctx)
    assert await bob_notes.get_for_user(bob.user_id, rows[0].id) is None
    assert await bob_notes.mark_read(bob.user_id, rows[0].id) is None


@pytest.mark.asyncio
async def test_fanout_rejects_empty_and_oversized(session, tenant_a):
    notes = UserNotificationRepository(session, tenant_a)
    with pytest.raises(ValueError, match="No recipients"):
        await notes.create_batch(
            title="x",
            body="y",
            created_by="admin",
            audience="all",
            recipient_user_ids=["invite:x"],
        )
    too_many = [f"user_{i}" for i in range(notes.MAX_FANOUT + 1)]
    with pytest.raises(ValueError, match="Too many recipients"):
        await notes.create_batch(
            title="x",
            body="y",
            created_by="admin",
            audience="all",
            recipient_user_ids=too_many,
        )


@pytest.mark.asyncio
async def test_list_sent_batches(session, tenant_a):
    _, alice, bob, _, _ = await _seed_users(session, tenant_a)
    notes = UserNotificationRepository(session, tenant_a)
    batch_a, _ = await notes.create_batch(
        title="One",
        body="a",
        created_by="admin",
        audience="user",
        recipient_user_ids=[alice.user_id],
    )
    batch_b, _ = await notes.create_batch(
        title="Many",
        body="b",
        created_by="admin",
        audience="all",
        recipient_user_ids=[alice.user_id, bob.user_id],
    )
    sent = await notes.list_sent_batches()
    by_title = {row["title"]: row for row in sent}
    assert by_title["One"]["batch_id"] == batch_a
    assert by_title["One"]["recipient_count"] == 1
    assert by_title["Many"]["batch_id"] == batch_b
    assert by_title["Many"]["recipient_count"] == 2
    assert {row["batch_id"] for row in sent} >= {batch_a, batch_b}
