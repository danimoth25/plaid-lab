"""Local persistence for linked Items.

An `access_token` is the long-lived credential for one Item (one set of bank
logins). Plaid never returns it again after the exchange, so losing this file
means re-linking. It is gitignored for the same reason a token file is:
in Production it is a live credential to someone's bank data.

The transactions cursor lives here too. `/transactions/sync` is incremental --
it returns only what changed since the cursor -- so the cursor is part of the
Item's state, not a per-run value.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import secrets
from .config import REPO_ROOT

STORE_PATH = REPO_ROOT / "items.json"


class StoreError(RuntimeError):
    """Raised when Item selection is ambiguous or matches nothing."""


@dataclass
class Item:
    """One linked Item. Everything here is non-secret and lives in items.json.

    The `access_token` is deliberately NOT a field: it is held in the OS
    credential store under `access_token:<item_id>` and reached through the
    `access_token` property. That keeps items.json inspectable and safe to read
    over someone's shoulder while the actual credential never touches disk.
    """

    item_id: str
    environment: str
    institution_id: str | None = None
    institution_name: str | None = None
    products: list[str] = field(default_factory=list)
    created_at: str = ""
    cursor: str | None = None

    @property
    def access_token(self) -> str:
        token = secrets.get(secrets.access_token_key(self.item_id))
        if not token:
            raise StoreError(
                f"No access_token stored for {self.label()}.\n"
                "It lives in the OS credential store, not items.json. If this "
                "Item predates that change, run: python -m plaid_lab secrets migrate"
            )
        return token

    def store_access_token(self, token: str) -> None:
        secrets.set(secrets.access_token_key(self.item_id), token)

    def forget_access_token(self) -> bool:
        return secrets.delete(secrets.access_token_key(self.item_id))

    def has_access_token(self) -> bool:
        try:
            return bool(secrets.get(secrets.access_token_key(self.item_id)))
        except secrets.KeyringUnavailable:
            return False

    def label(self) -> str:
        name = self.institution_name or self.institution_id or "unknown institution"
        return f"{name} [{self.item_id[:12]}...]"


class ItemStore:
    def __init__(self, path: Path = STORE_PATH):
        self.path = path
        self.items: list[Item] = []
        self.legacy_tokens: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        raw = json.loads(self.path.read_text(encoding="utf-8") or "{}")
        self.items = []
        self.legacy_tokens: dict[str, str] = {}
        for row in raw.get("items", []):
            row = dict(row)
            # Items written before tokens moved to the credential store still
            # carry one. Keep it aside for `secrets migrate` rather than
            # silently dropping it, which would orphan the Item at Plaid.
            token = row.pop("access_token", None)
            if token:
                self.legacy_tokens[row["item_id"]] = token
            self.items.append(Item(**row))

    def save(self) -> None:
        """Write atomically: a half-written items.json loses access tokens."""
        payload = {"items": [asdict(item) for item in self.items]}
        text = json.dumps(payload, indent=2)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def add(self, item: Item) -> Item:
        """Insert or replace by item_id, preserving sync state.

        Re-adding an Item is routine: re-importing a token, or claiming a Link
        session twice (Plaid's public_token exchange is idempotent and returns
        the same item_id). A naive replace drops the transactions cursor, and
        the next sync then replays the Item's entire history as `added` --
        verified 2026-08-15, cursor went from set to unset on a second claim.

        So the merge lives here rather than at each call site, where it was
        already forgotten once. A caller wanting a genuine resync passes an
        explicit cursor, or uses `transactions --reset`.
        """
        item.created_at = item.created_at or datetime.now(timezone.utc).isoformat(
            timespec="seconds"
        )
        existing = next((i for i in self.items if i.item_id == item.item_id), None)
        if existing:
            if item.cursor is None:
                item.cursor = existing.cursor
            item.created_at = existing.created_at or item.created_at
            self.items[self.items.index(existing)] = item
        else:
            self.items.append(item)
        self.save()
        return item

    def remove(self, item: Item) -> None:
        item.forget_access_token()
        self.items = [i for i in self.items if i.item_id != item.item_id]
        self.save()

    def update(self, item: Item, **changes: Any) -> Item:
        for key, value in changes.items():
            setattr(item, key, value)
        self.save()
        return item

    def for_env(self, environment: str) -> list[Item]:
        return [i for i in self.items if i.environment == environment]

    def select(self, environment: str, selector: str | None) -> Item:
        """Resolve one Item, failing closed rather than defaulting to the first.

        Accepts a 1-based index, an item_id prefix, or a case-insensitive
        substring of the institution name.
        """
        candidates = self.for_env(environment)
        if not candidates:
            raise StoreError(
                f"No linked Items in {environment}. Run `link` first."
            )

        if selector is None:
            if len(candidates) == 1:
                return candidates[0]
            listing = "\n".join(
                f"  {n}. {i.label()}" for n, i in enumerate(candidates, 1)
            )
            raise StoreError(
                f"{len(candidates)} Items linked in {environment}; "
                f"pass --item to choose one:\n{listing}"
            )

        if selector.isdigit():
            index = int(selector)
            if not 1 <= index <= len(candidates):
                raise StoreError(
                    f"--item {index} out of range (1..{len(candidates)})"
                )
            return candidates[index - 1]

        needle = selector.lower()
        matches = [
            i
            for i in candidates
            if i.item_id.lower().startswith(needle)
            or needle in (i.institution_name or "").lower()
            or needle == (i.institution_id or "").lower()
        ]
        if not matches:
            raise StoreError(f"No Item matches {selector!r} in {environment}")
        if len(matches) > 1:
            listing = ", ".join(i.label() for i in matches)
            raise StoreError(f"{selector!r} is ambiguous: {listing}")
        return matches[0]
