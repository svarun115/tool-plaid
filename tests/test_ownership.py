"""Tests for auth/ownership.py's per-user item_id authorization.

Pure file-based logic, no Plaid API or real credentials needed.
"""

import pytest

from tool_plaid.auth.ownership import ItemOwnership


@pytest.fixture
def ownership(tmp_path):
    return ItemOwnership(tmp_path / "data")


@pytest.mark.asyncio
async def test_owner_can_access_their_own_item(ownership):
    await ownership.record_ownership("alice", "item-1")
    assert await ownership.is_owner("alice", "item-1") is True


@pytest.mark.asyncio
async def test_non_owner_cannot_access_someone_elses_item(ownership):
    # Mirrors the acceptance criterion: presenting one user's item_id while
    # authenticated as another must be rejected, not silently served.
    await ownership.record_ownership("alice", "item-1")
    assert await ownership.is_owner("bob", "item-1") is False


@pytest.mark.asyncio
async def test_two_users_each_only_see_their_own_items(ownership):
    await ownership.record_ownership("alice", "item-alice")
    await ownership.record_ownership("bob", "item-bob")

    assert await ownership.is_owner("alice", "item-alice") is True
    assert await ownership.is_owner("alice", "item-bob") is False
    assert await ownership.is_owner("bob", "item-bob") is True
    assert await ownership.is_owner("bob", "item-alice") is False


@pytest.mark.asyncio
async def test_no_identity_is_never_authorized_even_for_a_real_item(ownership):
    await ownership.record_ownership("alice", "item-1")
    assert await ownership.is_owner(None, "item-1") is False


@pytest.mark.asyncio
async def test_no_identity_is_not_authorized_even_with_legacy_owner_configured(ownership):
    # legacy_owner attributes *unowned* items to a specific user_id -- it
    # must never become a blanket "no identity is fine" bypass.
    assert await ownership.is_owner(None, "item-1", legacy_owner="alice") is False


@pytest.mark.asyncio
async def test_unrecorded_item_with_no_legacy_owner_is_denied(ownership):
    assert await ownership.is_owner("alice", "some-item-never-recorded") is False


@pytest.mark.asyncio
async def test_legacy_owner_gets_access_to_a_pre_existing_unowned_item(ownership):
    # Simulates the real migration case: one item already linked before
    # ownership tracking existed, so it appears in no one's owned list.
    assert await ownership.is_owner("alice", "pre-existing-item", legacy_owner="alice") is True


@pytest.mark.asyncio
async def test_legacy_fallback_does_not_extend_to_a_different_user(ownership):
    assert await ownership.is_owner("bob", "pre-existing-item", legacy_owner="alice") is False


@pytest.mark.asyncio
async def test_legacy_fallback_does_not_apply_once_item_is_explicitly_owned(ownership):
    # Once an item is explicitly recorded under someone, the legacy fallback
    # must not also grant it to the legacy_owner -- explicit ownership wins.
    await ownership.record_ownership("bob", "item-1")
    assert await ownership.is_owner("alice", "item-1", legacy_owner="alice") is False


@pytest.mark.asyncio
async def test_record_ownership_is_idempotent(ownership):
    await ownership.record_ownership("alice", "item-1")
    await ownership.record_ownership("alice", "item-1")
    assert await ownership.owned_items("alice") == ["item-1"]


@pytest.mark.asyncio
async def test_owned_items_returns_empty_list_for_unknown_user(ownership):
    assert await ownership.owned_items("nobody") == []
