"""
Tests for the /internal/link-token and /internal/exchange custom routes
(#146 Phase D). These are called by mcp-auth-gateway's hosted Plaid Link
page -- never exposed via nginx/the public internet (see server.py's
_require_internal_secret docstring for the layered defense: nginx scope,
port firewalling, then this shared-secret header check).

Config.load() is monkeypatched directly to a stub instance rather than
through env vars -- get_env() is @lru_cache'd for the life of the process,
so setting env vars per-test wouldn't reliably take effect once any
earlier test (or import-time code) has already called get_env with that
key. PlaidClient's actual Plaid API calls are also monkeypatched; no
network calls, no real Plaid credentials.
"""

import json

import httpx
import pytest

import tool_plaid.server as server_module
from tool_plaid.config import Config
from tool_plaid.plaid.client import PlaidClient


INTERNAL_SECRET = "test-internal-secret"


def _stub_config(tmp_path, **overrides):
    cfg = Config()
    cfg.PLAID_ENV = "sandbox"
    cfg.PLAID_CLIENT_ID = "client-id"
    cfg.PLAID_SECRET = "secret"
    cfg.ENCRYPTION_KEY = "x" * 32
    cfg.PLAID_INTERNAL_SECRET = INTERNAL_SECRET
    for key, value in overrides.items():
        setattr(cfg, key, value)
    # data_dir is a read-only @property on the class; override per-instance
    # via __class__ patching is awkward, so monkeypatch the class property
    # itself in the fixture below instead.
    return cfg


@pytest.fixture
def app_client(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "load", classmethod(lambda cls: _stub_config(tmp_path)))
    monkeypatch.setattr(Config, "data_dir", property(lambda self: tmp_path))
    transport = httpx.ASGITransport(app=server_module.mcp.streamable_http_app())
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


# ── /internal/link-token ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_link_token_requires_secret(app_client):
    async with app_client as client:
        resp = await client.post("/internal/link-token", json={"user_id": "alice"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_link_token_rejects_wrong_secret(app_client):
    async with app_client as client:
        resp = await client.post(
            "/internal/link-token",
            json={"user_id": "alice"},
            headers={"X-Internal-Secret": "wrong"},
        )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_link_token_happy_path(monkeypatch, app_client):
    async def fake_create_link_token(self, user_id, redirect_uri=None):
        assert user_id == "alice"
        assert redirect_uri == "https://gw.example.com/connect/plaid"
        return "link-sandbox-abc123"

    monkeypatch.setattr(PlaidClient, "create_link_token", fake_create_link_token)

    async with app_client as client:
        resp = await client.post(
            "/internal/link-token",
            json={"user_id": "alice", "redirect_uri": "https://gw.example.com/connect/plaid"},
            headers={"X-Internal-Secret": INTERNAL_SECRET},
        )

    assert resp.status_code == 200
    assert resp.json() == {"link_token": "link-sandbox-abc123"}


@pytest.mark.asyncio
async def test_link_token_missing_user_id(app_client):
    async with app_client as client:
        resp = await client.post(
            "/internal/link-token", json={}, headers={"X-Internal-Secret": INTERNAL_SECRET}
        )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_link_token_plaid_error_returns_500(monkeypatch, app_client):
    async def fake_create_link_token(self, user_id, redirect_uri=None):
        raise RuntimeError("Plaid is down")

    monkeypatch.setattr(PlaidClient, "create_link_token", fake_create_link_token)

    async with app_client as client:
        resp = await client.post(
            "/internal/link-token",
            json={"user_id": "alice"},
            headers={"X-Internal-Secret": INTERNAL_SECRET},
        )
    assert resp.status_code == 500


# ── /internal/exchange ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_exchange_requires_secret(app_client):
    async with app_client as client:
        resp = await client.post(
            "/internal/exchange", json={"user_id": "alice", "public_token": "pub-x"}
        )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_exchange_happy_path_writes_token_and_ownership(monkeypatch, app_client, tmp_path):
    async def fake_exchange_public_token(self, public_token):
        assert public_token == "pub-sandbox-abc"
        return {"access_token": "access-sandbox-xyz", "item_id": "item-123"}

    monkeypatch.setattr(PlaidClient, "exchange_public_token", fake_exchange_public_token)

    async with app_client as client:
        resp = await client.post(
            "/internal/exchange",
            json={"user_id": "alice", "public_token": "pub-sandbox-abc", "institution_name": "Test Bank"},
            headers={"X-Internal-Secret": INTERNAL_SECRET},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["item_id"] == "item-123"

    from tool_plaid.auth.ownership import ItemOwnership

    ownership = ItemOwnership(tmp_path)
    assert await ownership.is_owner("alice", "item-123") is True
    assert (tmp_path / "items" / "item-123" / "token.json").is_file()


@pytest.mark.asyncio
async def test_exchange_missing_fields(app_client):
    async with app_client as client:
        resp = await client.post(
            "/internal/exchange", json={"user_id": "alice"}, headers={"X-Internal-Secret": INTERNAL_SECRET}
        )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_exchange_no_user_id_returns_failure_not_500(app_client):
    # perform_exchange's own contract: user_id=None -> a well-formed
    # ExchangeTokenResponse(success=False), not an exception -- but this
    # route requires user_id in the JSON body at all, so this exercises
    # the 400 path rather than perform_exchange's None-handling directly
    # (that's covered in test_transactions-style unit tests instead).
    async with app_client as client:
        resp = await client.post(
            "/internal/exchange",
            json={"user_id": None, "public_token": "pub-x"},
            headers={"X-Internal-Secret": INTERNAL_SECRET},
        )
    assert resp.status_code == 200
    assert resp.json()["success"] is False


@pytest.mark.asyncio
async def test_exchange_plaid_failure_returns_well_formed_failure(monkeypatch, app_client):
    async def fake_exchange_public_token(self, public_token):
        raise RuntimeError("boom")

    monkeypatch.setattr(PlaidClient, "exchange_public_token", fake_exchange_public_token)

    async with app_client as client:
        resp = await client.post(
            "/internal/exchange",
            json={"user_id": "alice", "public_token": "pub-x"},
            headers={"X-Internal-Secret": INTERNAL_SECRET},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert "boom" in body["error"]
