"""MCP server for Plaid"""

import logging
import os
import secrets
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from tool_plaid.utils.logging import setup_logging
from tool_plaid.config import Config
from tool_plaid.plaid.client import PlaidClient
from tool_plaid.tools.transactions import (
    get_transactions_by_date,
    get_balance,
    exchange_public_token,
    perform_exchange,
)

# Load environment variables from .env.agent file
env_file = Path(__file__).parent.parent / ".env.agent"
if env_file.exists():
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ[key.strip()] = value.strip()

# Setup file-based logging to avoid stdout/stderr interference
setup_logging()

logger = logging.getLogger(__name__)

# Create MCP server
mcp = FastMCP(
    "Plaid Tool",
    instructions="Get transactions and account balances from Plaid",
    transport_security=TransportSecuritySettings(
        allowed_hosts=[
            "assistant-vm.eastus.cloudapp.azure.com",
            "127.0.0.1:*",
            "localhost:*",
        ],
        allowed_origins=["https://assistant-vm.eastus.cloudapp.azure.com"],
    ),
)

# Register tools
mcp.tool()(exchange_public_token)
mcp.tool()(get_transactions_by_date)
mcp.tool()(get_balance)


def _require_internal_secret(request: Request) -> Response | None:
    """Gate for the two /internal/* routes below (#146). None means authorized.

    Defense-in-depth, not the primary boundary: the primary protection is
    that nginx only ever proxies /plaid/mcp to this process, and the port
    these routes listen on is firewalled from anything but localhost (see
    SELF_SERVE_AUTH_PLAN.md Phase D). This header check is what stops a
    request that somehow reaches this process anyway (e.g. a misconfigured
    proxy rule) from minting Link tokens or exchanging tokens on anyone's
    behalf. Plaid API secrets and the encryption key never leave this
    process regardless -- these routes only ever return a link_token or an
    ExchangeTokenResponse, never raw Plaid credentials.
    """
    config = Config.load()
    if not config.PLAID_INTERNAL_SECRET:
        return JSONResponse({"error": "PLAID_INTERNAL_SECRET not configured"}, status_code=503)
    presented = request.headers.get("x-internal-secret", "")
    if not presented or not secrets.compare_digest(presented, config.PLAID_INTERNAL_SECRET):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return None


@mcp.custom_route("/internal/link-token", methods=["POST"])
async def internal_link_token(request: Request) -> Response:
    """POST /internal/link-token -- {"user_id": ..., "redirect_uri": ...?} -> {"link_token": ...}.

    Called by mcp-auth-gateway's hosted Plaid Link page (localhost only,
    see _require_internal_secret) to mint the link_token that page's
    Plaid Link JS SDK needs to initialize.
    """
    denied = _require_internal_secret(request)
    if denied is not None:
        return denied

    try:
        body = await request.json()
        user_id = body["user_id"]
    except Exception:
        return JSONResponse({"error": "expected JSON body with user_id"}, status_code=400)
    redirect_uri = body.get("redirect_uri")

    config = Config.load()
    plaid_client = PlaidClient(config)
    try:
        link_token = await plaid_client.create_link_token(user_id, redirect_uri=redirect_uri)
    except Exception as e:
        logger.error(f"internal_link_token failed for user_id={user_id}: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)

    return JSONResponse({"link_token": link_token})


@mcp.custom_route("/internal/exchange", methods=["POST"])
async def internal_exchange(request: Request) -> Response:
    """POST /internal/exchange -- {"user_id": ..., "public_token": ..., "institution_name": ...?}
    -> ExchangeTokenResponse JSON.

    Called by mcp-auth-gateway's hosted Plaid Link page after the user
    completes Link, once the gateway has already validated its own
    connect-state record and resolved user_id server-side -- there's no
    MCP Context/X-User-Id header on this path (a plain browser POST), so
    this shares perform_exchange with the MCP tool rather than
    duplicating the exchange+store+ownership logic.
    """
    denied = _require_internal_secret(request)
    if denied is not None:
        return denied

    try:
        body = await request.json()
        user_id = body["user_id"]
        public_token = body["public_token"]
    except Exception:
        return JSONResponse({"error": "expected JSON body with user_id and public_token"}, status_code=400)
    institution_name = body.get("institution_name")

    result = await perform_exchange(user_id, public_token, institution_name)
    return JSONResponse(result.model_dump())


def main() -> None:
    """Main entry point for MCP server."""
    try:
        config = Config.load()
        config.validate()

        logger.info(f"Starting Plaid MCP Tool in {config.PLAID_ENV} mode")
        logger.info(f"Storage mode: {config.STORAGE_MODE}")
        logger.info(f"Transport: {config.MCP_TRANSPORT}")

        if config.MCP_TRANSPORT == "streamable-http":
            mcp.settings.host = "127.0.0.1"
            mcp.settings.port = config.MCP_PORT
            logger.info(f"Binding {mcp.settings.host}:{mcp.settings.port}")

        mcp.run(transport=config.MCP_TRANSPORT)

    except ValueError as e:
        logger.error(f"Configuration error: {e}")
        raise
    except Exception as e:
        logger.error(f"Failed to start MCP server: {e}")
        raise


if __name__ == "__main__":
    main()
