"""MCP server for Plaid"""

import logging
import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from tool_plaid.utils.logging import setup_logging
from tool_plaid.config import Config
from tool_plaid.tools.transactions import (
    sync_transactions,
    get_balance,
    exchange_public_token,
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
    instructions="Sync transactions and get account balances from Plaid",
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
mcp.tool()(sync_transactions)
mcp.tool()(get_balance)


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
