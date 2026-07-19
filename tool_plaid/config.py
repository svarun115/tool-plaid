"""Configuration management for tool-plaid"""

import os
from functools import lru_cache
from pathlib import Path
from typing import Optional


@lru_cache
def get_env(key: str, default: Optional[str] = None) -> str:
    """Get environment variable with optional default."""
    # First check .env.agent file in project root
    env_file = Path(__file__).parent.parent / ".env.agent"
    if env_file.exists():
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key_name, value = line.split("=", 1)
                    os.environ[key_name.strip()] = value.strip()
    
    # Fall back to os.getenv()
    value = os.getenv(key, default)
    if value is None:
        raise ValueError(f"Environment variable {key} is required but not set")
    return value


def get_env_int(key: str, default: int = 0) -> int:
    """Get environment variable as integer."""
    value = os.getenv(key)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        raise ValueError(f"Environment variable {key} must be an integer, got: {value}")


class Config:
    """Application configuration from environment variables."""

    PLAID_ENV: str = "sandbox"
    PLAID_CLIENT_ID: str = ""
    PLAID_SECRET: str = ""
    ENCRYPTION_KEY: str = ""
    STORAGE_MODE: str = "file"
    DATABASE_URL: Optional[str] = None
    MCP_TRANSPORT: str = "stdio"
    MCP_PORT: int = 8000
    BALANCE_CACHE_TTL: int = 300  # 5 minutes in seconds
    LEGACY_ITEM_OWNER: Optional[str] = None  # attributes pre-ownership-tracking items; see auth/ownership.py
    PLAID_INTERNAL_SECRET: Optional[str] = None  # gates /internal/* routes (#146); see server.py

    @classmethod
    def load(cls) -> "Config":
        """Load configuration from environment variables."""
        config = cls()

        config.PLAID_ENV = get_env("PLAID_ENV", "sandbox")
        config.PLAID_CLIENT_ID = get_env("PLAID_CLIENT_ID")
        config.PLAID_SECRET = get_env("PLAID_SECRET")
        config.ENCRYPTION_KEY = get_env("ENCRYPTION_KEY")

        config.STORAGE_MODE = get_env("STORAGE_MODE", "file")
        if config.STORAGE_MODE == "postgres":
            config.DATABASE_URL = get_env("DATABASE_URL")

        config.MCP_TRANSPORT = get_env("MCP_TRANSPORT", "stdio")
        config.MCP_PORT = get_env_int("MCP_PORT", 8000)
        config.BALANCE_CACHE_TTL = get_env_int("BALANCE_CACHE_TTL", 300)
        config.LEGACY_ITEM_OWNER = os.getenv("LEGACY_ITEM_OWNER")
        config.PLAID_INTERNAL_SECRET = os.getenv("PLAID_INTERNAL_SECRET")

        return config

    @property
    def is_sandbox(self) -> bool:
        """Check if running in Sandbox environment."""
        return self.PLAID_ENV.lower() == "sandbox"

    @property
    def data_dir(self) -> Path:
        """Get data directory for file storage."""
        from pathlib import Path
        return Path.cwd() / "data"

    def validate(self) -> None:
        """Validate configuration."""
        if len(self.ENCRYPTION_KEY) < 32:
            raise ValueError("ENCRYPTION_KEY must be at least 32 bytes")

        if self.STORAGE_MODE not in ("file", "postgres"):
            raise ValueError("STORAGE_MODE must be 'file' or 'postgres'")

        if self.MCP_TRANSPORT not in ("stdio", "streamable-http"):
            raise ValueError("MCP_TRANSPORT must be 'stdio' or 'streamable-http'")
