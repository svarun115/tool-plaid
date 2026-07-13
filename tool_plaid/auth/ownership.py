"""Per-user item_id ownership.

Storage here is already multi-tenant (TokenManager/FileStorage are keyed by
item_id), but nothing previously stopped a caller from passing any item_id,
including one that belongs to someone else. This module closes that gap:
user_id -> [item_id, ...], checked before any tool returns data for a given
item_id.
"""

import json
from pathlib import Path
from typing import Optional


class ItemAccessDeniedError(Exception):
    """Raised when the calling user_id is not authorized for the given item_id.

    Deliberately a distinct exception type, not a normal "not found" response
    -- an authorization failure is a different thing than "no data here", and
    should surface to the caller as a clear tool error, not a quietly empty
    result that looks the same as an unlinked item.
    """


class ItemOwnership:
    """File-backed user_id -> item_id[] registry, alongside the existing
    encrypted-token and transaction/balance storage under the same data_dir.
    """

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def _index_file(self) -> Path:
        return self.data_dir / "user_items.json"

    def _load(self) -> dict:
        f = self._index_file()
        if not f.exists():
            return {}
        return json.loads(f.read_text())

    def _save(self, index: dict) -> None:
        self._index_file().write_text(json.dumps(index, indent=2))

    async def record_ownership(self, user_id: str, item_id: str) -> None:
        """Attribute a newly-linked item to the user who linked it."""
        index = self._load()
        owned = index.setdefault(user_id, [])
        if item_id not in owned:
            owned.append(item_id)
        self._save(index)

    async def is_owner(
        self, user_id: Optional[str], item_id: str, legacy_owner: Optional[str] = None
    ) -> bool:
        """True if user_id may access item_id.

        user_id=None (no identity resolved -- the caller didn't come through
        the gateway's per-user path at all) is never authorized, matching
        this system's standing rule that an absent identity means zero
        access, never a default account.

        legacy_owner, if given, is attributed ownership of any item_id that
        predates this registry entirely (linked before per-user tracking
        existed, so it appears in no one's owned list) -- without this, the
        one item already linked before this feature shipped would become
        inaccessible to its own owner the moment this deploys.
        """
        if user_id is None:
            return False

        index = self._load()
        if item_id in index.get(user_id, []):
            return True

        if legacy_owner and user_id == legacy_owner:
            all_owned = {i for items in index.values() for i in items}
            if item_id not in all_owned:
                return True

        return False

    async def owned_items(self, user_id: str) -> list:
        """List every item_id this user_id owns (for building a picker, etc.)."""
        return list(self._load().get(user_id, []))
