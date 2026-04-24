"""Async Plaid API client wrapper (compatible with Plaid SDK v38.0.0+)"""

import asyncio
import logging
from typing import Optional, List

from plaid.configuration import Configuration
from plaid.api_client import ApiClient
from plaid.api.plaid_api import PlaidApi

from tool_plaid.config import Config
from tool_plaid.plaid.models import Transaction, AccountBalance

logger = logging.getLogger(__name__)


class PlaidClient:
    """Async wrapper around Plaid Python SDK (v38.0.0+)"""

    def __init__(self, config: Config):
        """
        Initialize Plaid client.

        Args:
            config: Application configuration
        """
        self.config = config

        # Create Plaid Configuration with api_key parameter
        plaid_config = Configuration(
            host=self._get_host(),
            api_key={
                "clientId": config.PLAID_CLIENT_ID,
                "secret": config.PLAID_SECRET,
            },
        )

        # Create API client
        api_client_obj = ApiClient(configuration=plaid_config)
        self.api_client = PlaidApi(api_client=api_client_obj)

        logger.info(f"PlaidClient initialized for {config.PLAID_ENV}")

    def _get_host(self) -> str:
        """Get Plaid API host based on environment."""
        if self.config.is_sandbox:
            return "https://sandbox.plaid.com"
        return "https://production.plaid.com"

    async def exchange_public_token(self, public_token: str) -> dict:
        """
        Exchange public token for access token.

        Args:
            public_token: Public token from Plaid Link

        Returns:
            Dictionary with access_token and item_id

        Raises:
            Exception: If exchange fails
        """
        logger.info("Exchanging public token for access token")

        try:
            response = await asyncio.to_thread(
                self.api_client.item_public_token_exchange,
                {"public_token": public_token},
            )
            result = {
                "access_token": response["access_token"],
                "item_id": response["item_id"],
            }
            logger.info(
                f"Public token exchanged successfully, item_id: {result['item_id']}"
            )
            return result
        except Exception as e:
            logger.error(f"Failed to exchange public token: {e}")
            raise

    async def sync_transactions(
        self,
        access_token: str,
        cursor: Optional[str] = None,
        count: int = 500,
        **kwargs,
    ) -> dict:
        """
        Sync transactions using cursor-based approach.

        Args:
            access_token: Plaid access token
            cursor: Stored cursor for incremental sync
            count: Number of transactions to fetch (default: 500)
            **kwargs: Additional parameters (e.g., days_requested, count)

        Returns:
            Dictionary with added, modified, removed, next_cursor, has_more, item_status
        """
        logger.debug(f"Syncing transactions with cursor: {cursor}")

        try:
            # Build request with access_token and cursor
            request = {"access_token": access_token}
            if cursor:
                request["cursor"] = cursor

            # Add additional parameters
            if "count" not in kwargs:
                request["count"] = count
            else:
                request.update(kwargs)

            response = await asyncio.to_thread(
                self.api_client.transactions_sync, request
            )

            # Convert Plaid models to our schema
            added = [
                Transaction(
                    transaction_id=tx.transaction_id,
                    account_id=tx.account_id,
                    amount=float(tx.amount),
                    date=tx.date.isoformat() if hasattr(tx.date, "isoformat") else str(tx.date),
                    merchant_name=tx.merchant_name or "",
                    category=tx.category or "",
                    pending=tx.pending or False,
                )
                for tx in response.get("added", [])
            ]

            modified = [
                Transaction(
                    transaction_id=tx.transaction_id,
                    account_id=tx.account_id,
                    amount=float(tx.amount),
                    date=tx.date.isoformat() if hasattr(tx.date, "isoformat") else str(tx.date),
                    merchant_name=tx.merchant_name or "",
                    category=tx.category or "",
                    pending=tx.pending or False,
                )
                for tx in response.get("modified", [])
            ]

            removed = [tx.transaction_id for tx in response.get("removed", [])]

            return {
                "added": added,
                "modified": modified,
                "removed": removed,
                "next_cursor": response.get("next_cursor", ""),
                "has_more": response.get("has_more", False),
                "item_status": response.get("item_status", "UNKNOWN"),
            }
        except Exception as e:
            logger.error(f"Failed to sync transactions: {e}")
            raise

    async def get_balance(
        self, access_token: str, account_ids: Optional[List[str]] = None, **kwargs
    ) -> List[AccountBalance]:
        """
        Get account balances.

        Args:
            access_token: Plaid access token
            account_ids: Optional list of account IDs to filter

        Returns:
            List of account balances
        """
        logger.debug(
            f"Fetching account balances for {len(account_ids) if account_ids else 'all'} accounts"
        )

        try:
            # Build request with access_token
            request = {"access_token": access_token}

            # Add account_ids if provided
            if account_ids:
                request["options"] = {"account_ids": account_ids}

            # Add additional parameters
            request.update(kwargs)

            response = await asyncio.to_thread(
                self.api_client.accounts_balance_get, request
            )

            # Convert Plaid models to our schema
            balances = []
            for account in response.get("accounts", []):
                # Filter by account_ids if provided
                if account_ids and account["account_id"] not in account_ids:
                    continue

                balance_data = account.get("balances", {})

                balances.append(
                    AccountBalance(
                        account_id=account["account_id"],
                        name=account["name"],
                        mask=account.get("mask") or "",
                        type=str(account["type"].value)
                        if hasattr(account["type"], "value")
                        else str(account["type"]),
                        available=balance_data.get("available"),
                        current=balance_data.get("current"),
                        iso_currency_code=balance_data.get("iso_currency_code", "USD"),
                    )
                )

            logger.info(f"Retrieved {len(balances)} account balances")
            return balances
        except Exception as e:
            logger.error(f"Failed to get balance: {e}")
            raise

    async def refresh_transactions(self, access_token: str, **kwargs) -> None:
        """
        Trigger transaction refresh for an item.

        Args:
            access_token: Plaid access token
        """
        logger.info("Triggering transaction refresh")

        try:
            request = {"access_token": access_token}
            request.update(kwargs)

            await asyncio.to_thread(self.api_client.transactions_refresh, request)

            logger.info("Transaction refresh triggered successfully")
        except Exception as e:
            logger.error(f"Failed to refresh transactions: {e}")
            raise
