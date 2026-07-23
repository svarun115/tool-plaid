"""Tests for auth/tokens.py's TokenManager.

Pure file-based logic, no Plaid API needed.
"""

import pytest

from tool_plaid.auth.tokens import TokenManager

TEST_KEY = "test_key_32_bytes_long_for_testing_purposes_only"


@pytest.fixture
def token_manager(tmp_path):
    return TokenManager(tmp_path / "data", TEST_KEY)


@pytest.mark.asyncio
async def test_get_metadata_returns_institution_and_created_at(token_manager):
    await token_manager.store_token(
        access_token="access-sandbox-secret",
        item_id="item-1",
        metadata={"institution": "Chase"},
    )

    metadata = await token_manager.get_metadata("item-1")

    assert metadata["institution"] == "Chase"
    assert metadata["created_at"]


@pytest.mark.asyncio
async def test_get_metadata_never_includes_access_token(token_manager):
    await token_manager.store_token(
        access_token="access-sandbox-secret",
        item_id="item-1",
        metadata={"institution": "Chase"},
    )

    metadata = await token_manager.get_metadata("item-1")

    assert "access_token" not in metadata
    assert "access-sandbox-secret" not in str(metadata)


@pytest.mark.asyncio
async def test_get_metadata_defaults_institution_when_absent(token_manager):
    await token_manager.store_token(access_token="access-sandbox-secret", item_id="item-1")

    metadata = await token_manager.get_metadata("item-1")

    assert metadata["institution"] == "Unknown"


@pytest.mark.asyncio
async def test_get_metadata_returns_none_for_unknown_item(token_manager):
    assert await token_manager.get_metadata("never-stored") is None


@pytest.mark.asyncio
async def test_list_items_includes_all_stored_items_regardless_of_owner(token_manager):
    await token_manager.store_token(access_token="tok-1", item_id="item-alice")
    await token_manager.store_token(access_token="tok-2", item_id="item-bob")

    assert set(await token_manager.list_items()) == {"item-alice", "item-bob"}
