"""Tests for the list_items tool (tools/transactions.py).

Config.load() is monkeypatched directly, same pattern as
test_internal_routes.py -- get_env() is @lru_cache'd for the life of the
process, so per-test env vars wouldn't reliably take effect. `ctx` is a
minimal stand-in for FastMCP's injected Context: only the
`request_context.request.headers` path that `_caller_user_id` actually
reads (transactions.py) needs to exist.
"""

from types import SimpleNamespace

import pytest

from tool_plaid.auth.ownership import ItemOwnership
from tool_plaid.auth.tokens import TokenManager
from tool_plaid.config import Config
from tool_plaid.tools.transactions import list_items


def _stub_config(tmp_path, **overrides):
    cfg = Config()
    cfg.PLAID_ENV = "sandbox"
    cfg.PLAID_CLIENT_ID = "client-id"
    cfg.PLAID_SECRET = "secret"
    cfg.ENCRYPTION_KEY = "x" * 32
    for key, value in overrides.items():
        setattr(cfg, key, value)
    return cfg


@pytest.fixture
def data_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "load", classmethod(lambda cls: _stub_config(tmp_path)))
    monkeypatch.setattr(Config, "data_dir", property(lambda self: tmp_path))
    return tmp_path


def _ctx(user_id):
    """A fake Context exposing only what _caller_user_id reads."""
    headers = {"x-user-id": user_id} if user_id is not None else {}
    request = SimpleNamespace(headers=headers) if user_id is not None else None
    return SimpleNamespace(request_context=SimpleNamespace(request=request))


async def _link(data_dir, user_id, item_id, institution):
    token_manager = TokenManager(data_dir, "x" * 32)
    await token_manager.store_token(
        access_token=f"access-{item_id}", item_id=item_id, metadata={"institution": institution}
    )
    await ItemOwnership(data_dir).record_ownership(user_id, item_id)


@pytest.mark.asyncio
async def test_lists_only_the_caller_own_items(data_dir):
    await _link(data_dir, "alice", "item-chase", "Chase")
    await _link(data_dir, "alice", "item-amex", "Amex")
    await _link(data_dir, "bob", "item-boa", "Bank of America")

    result = await list_items(_ctx("alice"))

    assert {item.item_id for item in result.items} == {"item-chase", "item-amex"}
    assert {item.institution for item in result.items} == {"Chase", "Amex"}


@pytest.mark.asyncio
async def test_does_not_leak_another_users_items(data_dir):
    await _link(data_dir, "alice", "item-chase", "Chase")
    await _link(data_dir, "bob", "item-boa", "Bank of America")

    result = await list_items(_ctx("bob"))

    assert [item.item_id for item in result.items] == ["item-boa"]


@pytest.mark.asyncio
async def test_legacy_item_surfaces_for_the_legacy_owner_only(data_dir, monkeypatch):
    monkeypatch.setattr(
        Config, "load", classmethod(lambda cls: _stub_config(data_dir, LEGACY_ITEM_OWNER="alice"))
    )
    # Linked before ownership tracking existed: token stored, no owner recorded.
    token_manager = TokenManager(data_dir, "x" * 32)
    await token_manager.store_token(
        access_token="access-legacy", item_id="item-legacy", metadata={"institution": "Wells Fargo"}
    )

    alice_result = await list_items(_ctx("alice"))
    bob_result = await list_items(_ctx("bob"))

    assert [item.item_id for item in alice_result.items] == ["item-legacy"]
    assert bob_result.items == []


@pytest.mark.asyncio
async def test_no_caller_identity_returns_empty_list_not_an_error(data_dir):
    await _link(data_dir, "alice", "item-chase", "Chase")

    result = await list_items(_ctx(None))

    assert result.items == []


@pytest.mark.asyncio
async def test_response_never_contains_the_access_token(data_dir):
    await _link(data_dir, "alice", "item-chase", "Chase")

    result = await list_items(_ctx("alice"))

    assert "access-item-chase" not in result.model_dump_json()
