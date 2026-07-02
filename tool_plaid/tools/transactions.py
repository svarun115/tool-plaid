"""MCP Tools for Plaid"""

import logging
from typing import List, Optional
from datetime import datetime

from pydantic import BaseModel, Field

from tool_plaid.plaid.client import PlaidClient
from tool_plaid.plaid.models import Transaction, AccountBalance
from tool_plaid.storage.base import StorageBackend
from tool_plaid.storage.file import FileStorage
from tool_plaid.auth.tokens import TokenManager
from tool_plaid.config import Config

logger = logging.getLogger(__name__)


class SyncTransactionsInput(BaseModel):
    """Input for sync_transactions tool."""

    item_id: str = Field(description="Plaid item identifier")
    force_refresh: bool = Field(default=False, description="Trigger Plaid refresh")
    days_requested: Optional[int] = Field(
        default=90, ge=1, le=730, description="Days of history"
    )


class SyncTransactionsResponse(BaseModel):
    """Response for sync_transactions tool."""

    added: List[Transaction] = Field(default_factory=list)
    modified: List[Transaction] = Field(default_factory=list)
    removed: List[str] = Field(default_factory=list, description="Transaction IDs")
    next_cursor: str = Field(default="")
    has_more: bool = Field(default=False)
    item_status: str = Field(default="")
    summary: str = Field(default="")
    skipped_count: int = Field(
        default=0,
        description="Malformed transactions skipped this page (logged server-side, not recoverable via re-sync)",
    )


class GetBalanceInput(BaseModel):
    """Input for get_balance tool."""

    item_id: str = Field(description="Plaid item identifier")
    account_ids: Optional[List[str]] = Field(
        default=None, description="Filter accounts"
    )
    force_refresh: bool = Field(default=False, description="Bypass cache")


class GetTransactionsByDateInput(BaseModel):
    """Input for get_transactions_by_date tool."""

    item_id: str = Field(description="Plaid item identifier")
    start_date: str = Field(description="Start date, inclusive (YYYY-MM-DD)")
    end_date: str = Field(description="End date, inclusive (YYYY-MM-DD)")


class GetTransactionsByDateResponse(BaseModel):
    """Response for get_transactions_by_date tool."""

    transactions: List[Transaction] = Field(default_factory=list)
    total_transactions: int = Field(default=0)
    item_status: str = Field(default="")
    summary: str = Field(default="")
    skipped_count: int = Field(
        default=0,
        description="Malformed transactions skipped in this range (logged server-side)",
    )


class ExchangeTokenInput(BaseModel):
    """Input for exchange_public_token tool."""

    public_token: str = Field(
        description="Public token from Plaid Link onSuccess callback (expires in 30 min)"
    )
    institution_name: Optional[str] = Field(
        default=None, description="Name of the linked financial institution"
    )


class GetBalanceResponse(BaseModel):
    """Response for get_balance tool."""

    balances: List[AccountBalance] = Field(default_factory=list)
    cached: bool = Field(default=False)
    timestamp: str = Field(default="")


class ExchangeTokenResponse(BaseModel):
    """Response for exchange_public_token tool."""

    item_id: str = Field(description="Plaid item identifier for future API calls")
    success: bool = Field(default=True)
    error: Optional[str] = Field(default=None)


async def exchange_public_token(
    public_token: str,
    institution_name: Optional[str] = None,
) -> ExchangeTokenResponse:
    """
    Exchange a Plaid Link public_token for an access_token and store it securely.

    Call this after Plaid Link completes successfully. The public_token expires
    in 30 minutes, so exchange it promptly.

    Args:
        public_token: The public_token from Plaid Link's onSuccess callback
        institution_name: Optional name of the linked institution for metadata

    Returns:
        ExchangeTokenResponse with item_id for future API calls
    """
    logger.info("exchange_public_token called")

    config = Config.load()
    plaid_client = PlaidClient(config)
    token_manager = TokenManager(config.data_dir, config.ENCRYPTION_KEY)

    try:
        result = await plaid_client.exchange_public_token(public_token)
        access_token = result["access_token"]
        item_id = result["item_id"]

        metadata = {"institution": institution_name or "Unknown"}
        await token_manager.store_token(
            access_token=access_token,
            item_id=item_id,
            metadata=metadata,
        )

        logger.info(f"Token exchanged and stored for item_id: {item_id}")
        return ExchangeTokenResponse(item_id=item_id, success=True)

    except Exception as e:
        logger.error(f"Failed to exchange public token: {e}")
        return ExchangeTokenResponse(item_id="", success=False, error=str(e))


async def sync_transactions(
    item_id: str,
    force_refresh: bool = False,
    days_requested: Optional[int] = 90,
) -> SyncTransactionsResponse:
    """
    DEPRECATED: prefer `get_transactions_by_date` for new call sites.

    This cursor-based approach ties "what's new" to an opaque local pointer that
    can only move forward and can never be asked to re-fetch a past window. That
    caused two real problems in production: (1) a single malformed transaction on
    a page could crash the whole page (now mitigated, see _build_transaction, but
    the underlying cursor design is still the wrong shape for this), and (2)
    retroactively-settled/backdated transactions are invisible once the cursor has
    moved past their original window. Kept for backward compatibility only.

    Sync transactions from Plaid using cursor-based incremental updates.

    Args:
        item_id: Plaid item identifier
        force_refresh: Trigger Plaid refresh
        days_requested: Days of history

    Returns:
        SyncTransactionsResponse with added, modified, removed transactions
    """
    logger.info(f"sync_transactions called for item_id: {item_id}")

    config = Config.load()
    token_manager = TokenManager(config.data_dir, config.ENCRYPTION_KEY)
    storage = FileStorage(config.data_dir)
    plaid_client = PlaidClient(config)

    # Get access token
    access_token = await token_manager.get_token(item_id)
    if not access_token:
        return SyncTransactionsResponse(
            item_status="ITEM_NOT_FOUND",
            summary=f"Item {item_id} not found or not linked",
        )

    # Trigger refresh if requested
    if force_refresh:
        try:
            await plaid_client.refresh_transactions(access_token)
            logger.info(f"Refreshed transactions for item {item_id}")
        except Exception as e:
            logger.error(f"Failed to refresh transactions: {e}")

    # Get current cursor
    cursor = await storage.get_cursor(item_id)

    # Sync transactions
    try:
        if days_requested:
            result = await plaid_client.sync_transactions(
                access_token=access_token,
                cursor=cursor,
                count=500,
                days_requested=days_requested,
            )
        else:
            result = await plaid_client.sync_transactions(
                access_token=access_token,
                cursor=cursor,
                count=500,
            )
    except Exception as e:
        logger.error(f"Failed to sync transactions: {e}")
        return SyncTransactionsResponse(
            item_status="ERROR",
            summary=f"Failed to sync: {str(e)}",
        )

    # Store updated cursor
    await storage.set_cursor(item_id, result["next_cursor"])

    # Store transactions
    if result["added"]:
        await storage.add_transactions(item_id, result["added"])

    if result["modified"]:
        for tx in result["modified"]:
            await storage.update_transaction(item_id, tx)

    if result["removed"]:
        await storage.remove_transactions(item_id, result["removed"])

    # Build summary
    summary_parts = []
    if result["added"]:
        summary_parts.append(f"Added {len(result['added'])}")
    if result["modified"]:
        summary_parts.append(f"Modified {len(result['modified'])}")
    if result["removed"]:
        summary_parts.append(f"Removed {len(result['removed'])}")

    summary = (", ".join(summary_parts) + " transactions") if summary_parts else "No transactions"

    skipped_count = result.get("skipped_count", 0)
    if skipped_count:
        summary += f" ({skipped_count} malformed transaction(s) skipped this page — see server logs)"

    logger.info(f"sync_transactions completed: {summary}")

    return SyncTransactionsResponse(
        added=result["added"],
        modified=result["modified"],
        removed=result["removed"],
        next_cursor=result["next_cursor"],
        has_more=result["has_more"],
        item_status=result["item_status"],
        summary=summary,
        skipped_count=skipped_count,
    )


async def get_transactions_by_date(
    item_id: str,
    start_date: str,
    end_date: str,
) -> GetTransactionsByDateResponse:
    """
    Fetch all transactions for an item within an explicit date range.

    This is the preferred way to pull transactions — it is a direct, stateless
    date-range query with no hidden cursor/bookkeeping state. Callers (typically
    a skill tracking "last reconciled through" in its own context file) decide
    exactly what window to pull each time. This also makes retroactively-settled
    or backdated transactions visible on a re-pull, which a forward-only cursor
    cannot surface.

    Args:
        item_id: Plaid item identifier
        start_date: Start date, inclusive (YYYY-MM-DD)
        end_date: End date, inclusive (YYYY-MM-DD)

    Returns:
        GetTransactionsByDateResponse with every transaction in the range
    """
    logger.info(
        f"get_transactions_by_date called for item_id: {item_id}, range: {start_date}..{end_date}"
    )

    config = Config.load()
    token_manager = TokenManager(config.data_dir, config.ENCRYPTION_KEY)
    storage = FileStorage(config.data_dir)
    plaid_client = PlaidClient(config)

    access_token = await token_manager.get_token(item_id)
    if not access_token:
        return GetTransactionsByDateResponse(
            item_status="ITEM_NOT_FOUND",
            summary=f"Item {item_id} not found or not linked",
        )

    try:
        result = await plaid_client.get_transactions_by_date(
            access_token=access_token,
            start_date=start_date,
            end_date=end_date,
        )
    except Exception as e:
        logger.error(f"Failed to get transactions by date: {e}")
        return GetTransactionsByDateResponse(
            item_status="ERROR",
            summary=f"Failed to fetch transactions: {str(e)}",
        )

    # Cache locally for reference (does not drive future pulls — no cursor semantics)
    if result["transactions"]:
        await storage.add_transactions(item_id, result["transactions"])

    skipped_count = result.get("skipped_count", 0)
    summary = f"{len(result['transactions'])} of {result['total_transactions']} transactions ({start_date}..{end_date})"
    if skipped_count:
        summary += f" — {skipped_count} malformed transaction(s) skipped, see server logs"

    logger.info(f"get_transactions_by_date completed: {summary}")

    return GetTransactionsByDateResponse(
        transactions=result["transactions"],
        total_transactions=result["total_transactions"],
        item_status="OK",
        summary=summary,
        skipped_count=skipped_count,
    )


async def get_balance(
    item_id: str,
    account_ids: Optional[List[str]] = None,
    force_refresh: bool = False,
) -> GetBalanceResponse:
    """
    Get account balances with intelligent caching.

    Args:
        item_id: Plaid item identifier
        account_ids: Filter specific accounts
        force_refresh: Bypass cache

    Returns:
        GetBalanceResponse with balances and caching metadata
    """
    logger.info(f"get_balance called for item_id: {item_id}")

    config = Config.load()
    token_manager = TokenManager(config.data_dir, config.ENCRYPTION_KEY)
    storage = FileStorage(config.data_dir)
    plaid_client = PlaidClient(config)

    # Get access token
    access_token = await token_manager.get_token(item_id)
    if not access_token:
        return GetBalanceResponse(
            cached=False,
            timestamp=datetime.utcnow().isoformat(),
        )

    # Check cache first
    if not force_refresh:
        cached = await storage.get_balance(item_id, account_ids)
        if cached:
            logger.info("Returning cached balance")
            return GetBalanceResponse(
                balances=[cached],
                cached=True,
                timestamp=cached.timestamp,
            )

    # Fetch from Plaid
    try:
        balances = await plaid_client.get_balance(
            access_token=access_token,
            account_ids=account_ids,
        )
    except Exception as e:
        logger.error(f"Failed to get balance: {e}")
        return GetBalanceResponse(
            cached=False,
            timestamp=datetime.utcnow().isoformat(),
        )

    # Store in cache
    if balances:
        await storage.set_balance(item_id, balances[0])

    timestamp = datetime.utcnow().isoformat()

    logger.info(f"get_balance completed: {len(balances)} accounts")

    return GetBalanceResponse(
        balances=balances,
        cached=False,
        timestamp=timestamp,
    )
