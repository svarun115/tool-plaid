"""File storage backend for tool-plaid"""

import json
import logging
from pathlib import Path
from typing import List, Optional
from datetime import datetime

from tool_plaid.storage.base import StorageBackend
from tool_plaid.plaid.models import Transaction, AccountBalance

logger = logging.getLogger(__name__)


class FileStorage(StorageBackend):
    """File-based storage using JSON text files."""

    def __init__(self, data_dir: Path):
        """
        Initialize file storage.

        Args:
            data_dir: Root directory for data storage
        """
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"File storage initialized at {self.data_dir}")

    def _get_item_dir(self, item_id: str) -> Path:
        """Get directory for a specific item."""
        item_dir = self.data_dir / "items" / item_id
        item_dir.mkdir(parents=True, exist_ok=True)
        return item_dir

    async def add_transactions(
        self, item_id: str, transactions: List[Transaction]
    ) -> None:
        """Add new transactions for an item."""
        item_dir = self._get_item_dir(item_id)
        transactions_file = item_dir / "transactions.json"

        existing = []
        if transactions_file.exists():
            existing = json.loads(transactions_file.read_text())

        new_txs = [tx.model_dump() for tx in transactions]
        existing.extend(new_txs)

        transactions_file.write_text(json.dumps(existing, indent=2))
        logger.info(f"Added {len(transactions)} transactions for {item_id}")

    async def update_transaction(self, item_id: str, transaction: Transaction) -> None:
        """Update an existing transaction."""
        item_dir = self._get_item_dir(item_id)
        transactions_file = item_dir / "transactions.json"

        if not transactions_file.exists():
            return

        existing = json.loads(transactions_file.read_text())

        # Find and update the transaction
        updated = [
            tx
            if tx.get("transaction_id") != transaction.transaction_id
            else transaction.model_dump()
            for tx in existing
        ]

        transactions_file.write_text(json.dumps(updated, indent=2))
        logger.debug(f"Updated transaction {transaction.transaction_id} for {item_id}")

    async def remove_transactions(
        self, item_id: str, transaction_ids: List[str]
    ) -> None:
        """Remove transactions by IDs."""
        item_dir = self._get_item_dir(item_id)
        transactions_file = item_dir / "transactions.json"

        if not transactions_file.exists():
            return

        existing = json.loads(transactions_file.read_text())
        to_remove = set(transaction_ids)

        filtered = [tx for tx in existing if tx.get("transaction_id") not in to_remove]

        transactions_file.write_text(json.dumps(filtered, indent=2))
        logger.info(f"Removed {len(transaction_ids)} transactions for {item_id}")

    async def get_transactions(self, item_id: str) -> List[Transaction]:
        """Get all transactions for an item."""
        item_dir = self._get_item_dir(item_id)
        transactions_file = item_dir / "transactions.json"

        if not transactions_file.exists():
            return []

        existing = json.loads(transactions_file.read_text())
        return [Transaction(**tx) for tx in existing]

    async def set_balance(self, item_id: str, balance: AccountBalance) -> None:
        """Store account balance with timestamp."""
        item_dir = self._get_item_dir(item_id)
        balance_file = item_dir / "balance.json"

        balance_data = balance.model_dump()
        balance_data["timestamp"] = datetime.utcnow().isoformat()

        balance_file.write_text(json.dumps(balance_data, indent=2))
        logger.debug(f"Stored balance for {item_id}")

    async def get_balance(
        self, item_id: str, account_ids: Optional[List[str]] = None
    ) -> Optional[AccountBalance]:
        """Get stored balance for an item."""
        item_dir = self._get_item_dir(item_id)
        balance_file = item_dir / "balance.json"

        if not balance_file.exists():
            return None

        from tool_plaid.config import Config

        config = Config.load()

        # Check cache TTL
        balance_data = json.loads(balance_file.read_text())
        stored_timestamp = datetime.fromisoformat(balance_data["timestamp"])
        age = datetime.utcnow() - stored_timestamp

        if age.total_seconds() > config.BALANCE_CACHE_TTL:
            logger.debug(f"Balance cache expired for {item_id}")
            return None

        # Filter by account_ids if provided
        if account_ids and balance_data["account_id"] not in account_ids:
            return None

        return AccountBalance(**balance_data)
