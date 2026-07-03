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


def _normalize_category(category) -> str:
    """Plaid may return category as a legacy hierarchy list (e.g. ["Travel", "Parking"])
    or as a plain string depending on the account/product. Flatten to a single string."""
    if not category:
        return ""
    if isinstance(category, (list, tuple)):
        return ", ".join(str(c) for c in category if c)
    return str(category)


def _format_date(d) -> str:
    return d.isoformat() if hasattr(d, "isoformat") else str(d) if d else ""


def _build_transaction(tx) -> Optional[Transaction]:
    """Convert a raw Plaid transaction into our schema. Returns None (and logs) instead of
    raising, so one malformed record can't take down an entire sync page — a single bad
    transaction previously caused the whole page to be silently dropped and unrecoverable
    once Plaid's cursor advanced past it."""
    try:
        return Transaction(
            transaction_id=tx.transaction_id,
            account_id=tx.account_id,
            amount=float(tx.amount),
            date=_format_date(tx.date),
            # `date` is the bank's posting date for posted transactions (can lag the actual
            # purchase by a few days). `authorized_date` reflects when the user actually made
            # the transaction — prefer it downstream. Not all institutions populate it, so this
            # can be empty; callers should fall back to `date` in that case.
            authorized_date=_format_date(getattr(tx, "authorized_date", None)),
            merchant_name=tx.merchant_name or "",
            category=_normalize_category(tx.category),
            pending=tx.pending or False,
        )
    except Exception as e:
        logger.error(
            f"Skipping malformed transaction {getattr(tx, 'transaction_id', '<unknown>')}: {e}"
        )
        return None


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

    async def get_transactions_by_date(
        self,
        access_token: str,
        start_date: str,
        end_date: str,
        count: int = 500,
    ) -> dict:
        """
        Fetch transactions for an explicit date range via Plaid's /transactions/get.

        Unlike sync_transactions (cursor-based, opaque local bookkeeping of "what's
        already been consumed"), this is a direct, stateless date-range query — the
        caller decides exactly what window to pull every time, which:
          - lets any past date range be re-queried on demand (a cursor can only move
            forward; it can't be asked "what happened in March again")
          - naturally handles retroactively-settled/backdated transactions, since
            re-querying a recent window will surface updates to those transactions
          - never gets stuck: a malformed record only affects itself (see
            _build_transaction), never an entire opaque page tied to unrecoverable
            cursor state

        Args:
            access_token: Plaid access token
            start_date: ISO date (YYYY-MM-DD), inclusive
            end_date: ISO date (YYYY-MM-DD), inclusive
            count: Page size per request (Plaid max 500)

        Returns:
            Dictionary with all transactions in range, total_transactions, skipped_count
        """
        logger.debug(f"Fetching transactions {start_date} to {end_date}")

        all_raw = []
        offset = 0
        total_transactions = None

        try:
            while total_transactions is None or offset < total_transactions:
                request = {
                    "access_token": access_token,
                    "start_date": start_date,
                    "end_date": end_date,
                    "options": {"count": count, "offset": offset},
                }
                response = await asyncio.to_thread(
                    self.api_client.transactions_get, request
                )
                page = response.get("transactions", [])
                all_raw.extend(page)
                total_transactions = response.get("total_transactions", len(all_raw))
                offset += len(page)
                if not page:
                    break  # safety: avoid infinite loop if Plaid returns an empty page early

            transactions = [t for t in (_build_transaction(tx) for tx in all_raw) if t is not None]
            skipped_count = len(all_raw) - len(transactions)
            if skipped_count:
                logger.error(
                    f"get_transactions_by_date skipped {skipped_count} malformed "
                    f"transaction(s) out of {len(all_raw)} in range {start_date}..{end_date} "
                    f"— see prior error logs for transaction_ids."
                )

            return {
                "transactions": transactions,
                "total_transactions": total_transactions,
                "skipped_count": skipped_count,
            }
        except Exception as e:
            logger.error(f"Failed to get transactions by date: {e}")
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
