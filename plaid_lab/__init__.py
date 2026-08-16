"""plaid_lab -- a Plaid API client and CLI for personal finance work.

Layered so the CLI is disposable and the rest is not:

    config.py    credentials and environment resolution
    client.py    client construction, error normalization, JSON coercion
    products.py  one function per endpoint, plain values in and out
    store.py     access_token and transaction-cursor persistence
    fmt.py       console tables
    cli.py       argparse front end
"""

from .client import PlaidError, make_client
from .config import ConfigError, Settings, load_settings
from .store import Item, ItemStore, StoreError

__all__ = [
    "ConfigError",
    "Item",
    "ItemStore",
    "PlaidError",
    "Settings",
    "StoreError",
    "load_settings",
    "make_client",
]
