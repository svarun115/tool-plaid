"""Token management for tool-plaid"""

import json
import logging
from pathlib import Path
from typing import Dict, Optional, List
from dataclasses import dataclass

from tool_plaid.utils.encryption import Encryptor

logger = logging.getLogger(__name__)


@dataclass
class TokenData:
    """Stored token data."""
    access_token: str
    item_id: str
    metadata: Dict[str, str]
    created_at: str


class TokenManager:
    """Manage Plaid access tokens with encryption."""

    def __init__(self, data_dir: Path, encryption_key: str):
        """
        Initialize token manager.

        Args:
            data_dir: Directory for token storage
            encryption_key: 32-byte encryption key
        """
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.encryptor = Encryptor(encryption_key)
        logger.info("TokenManager initialized")

    def _get_token_file(self, item_id: str) -> Path:
        """Get token file path for an item."""
        return self.data_dir / "items" / item_id / "token.json"

    def _get_index_file(self) -> Path:
        """Get index file path."""
        return self.data_dir / "item_index.json"

    async def store_token(
        self,
        access_token: str,
        item_id: str,
        metadata: Optional[Dict[str, str]] = None
    ) -> None:
        """
        Store access token encrypted.

        Args:
            access_token: Plaid access token
            item_id: Unique identifier for this item
            metadata: Optional metadata (institution name, etc.)
        """
        from datetime import datetime

        item_dir = self.data_dir / "items" / item_id
        item_dir.mkdir(parents=True, exist_ok=True)

        token_data = TokenData(
            access_token=access_token,
            item_id=item_id,
            metadata=metadata or {},
            created_at=datetime.utcnow().isoformat(),
        )

        encrypted = self.encryptor.encrypt(json.dumps(token_data.__dict__))

        token_file = self._get_token_file(item_id)
        token_file.write_text(encrypted)

        # Update index
        index_file = self._get_index_file()
        index = {}
        if index_file.exists():
            index = json.loads(index_file.read_text())
        
        index[item_id] = item_dir.as_posix()
        index_file.write_text(json.dumps(index, indent=2))

        logger.info(f"Stored token for item_id: {item_id}")

    async def get_token(self, item_id: str) -> Optional[str]:
        """
        Retrieve and decrypt access token.

        Args:
            item_id: Item identifier

        Returns:
            Access token or None if not found
        """
        token_file = self._get_token_file(item_id)

        if not token_file.exists():
            logger.warning(f"Token not found for item_id: {item_id}")
            return None

        try:
            encrypted = token_file.read_text()
            decrypted = self.encryptor.decrypt(encrypted)
            token_data = json.loads(decrypted)
            return token_data["access_token"]
        except Exception as e:
            logger.error(f"Failed to decrypt token for {item_id}: {e}")
            return None

    async def get_metadata(self, item_id: str) -> Optional[Dict[str, str]]:
        """
        Retrieve an item's metadata (institution name, created_at) without
        decrypting/returning the access_token itself -- for tool responses
        that leave this process (list_items), where the token must never
        appear.

        Args:
            item_id: Item identifier

        Returns:
            Dict with "institution" and "created_at" keys, or None if not
            found/undecryptable.
        """
        token_file = self._get_token_file(item_id)

        if not token_file.exists():
            return None

        try:
            encrypted = token_file.read_text()
            decrypted = self.encryptor.decrypt(encrypted)
            token_data = json.loads(decrypted)
            return {
                "institution": token_data.get("metadata", {}).get("institution", "Unknown"),
                "created_at": token_data.get("created_at", ""),
            }
        except Exception as e:
            logger.error(f"Failed to decrypt metadata for {item_id}: {e}")
            return None

    async def remove_token(self, item_id: str) -> None:
        """
        Remove stored token.

        Args:
            item_id: Item identifier
        """
        token_file = self._get_token_file(item_id)

        if token_file.exists():
            token_file.unlink()

        # Update index
        index_file = self._get_index_file()
        if index_file.exists():
            index = json.loads(index_file.read_text())
            if item_id in index:
                del index[item_id]
                index_file.write_text(json.dumps(index, indent=2))

        logger.info(f"Removed token for item_id: {item_id}")

    async def list_items(self) -> List[str]:
        """List all stored item IDs."""
        index_file = self._get_index_file()

        if not index_file.exists():
            return []

        index = json.loads(index_file.read_text())
        return list(index.keys())
