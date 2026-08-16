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

from .config import REPO_ROOT

STORE_PATH = REPO_ROOT / "items.json"


class StoreError(RuntimeError):
    """Raised when Item selection is ambiguous or matches nothing."""


@dataclass
class Item:
    item_id: str
    access_token: str
    environment: str
    institution_id: str | None = None
    institution_name: str | None = None
    products: list[str] = field(default_factory=list)
    created_at: str = ""
    cursor: str | None = None

    def label(self) -> str:
        name = self.institution_name or self.institution_id or "unknown institution"
        return f"{name} [{self.item_id[:12]}...]"


class ItemStore:
    def __init__(self, path: Path = STORE_PATH):
        self.path = path
        self.items: list[Item] = []
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        raw = json.loads(self.path.read_text(encoding="utf-8") or "{}")
        self.items = [Item(**row) for row in raw.get("items", [])]

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
        item.created_at = item.created_at or datetime.now(timezone.utc).isoformat(
            timespec="seconds"
        )
        existing = next((i for i in self.items if i.item_id == item.item_id), None)
        if existing:
            self.items[self.items.index(existing)] = item
        else:
            self.items.append(item)
        self.save()
        return item

    def remove(self, item: Item) -> None:
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
